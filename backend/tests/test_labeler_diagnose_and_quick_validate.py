import pytest
from httpx import AsyncClient, ASGITransport

from backend.apps.api.main import app

API_KEY = "dev-key"

@pytest.mark.asyncio
async def test_labeler_diagnose_filters(monkeypatch):
    # Fake repo rows with mixed groups and string/obj extra
    class FakeRepo:
        async def fetch_unlabeled_candidates(self, min_age_seconds: int = 0, limit: int = 500):
            return [
                {"symbol": "BTCUSDT", "interval": "1m", "extra": {"target": "bottom"}},
                {"symbol": "ETHUSDT", "interval": "1m", "extra": {"target": "bottom"}},
                {"symbol": "BTCUSDT", "interval": "5m", "extra": {"target": "bottom"}},
                {"symbol": "BTCUSDT", "interval": "1m", "extra": '{"target":"bottom"}'},
                {"symbol": "BTCUSDT", "interval": "1m", "extra": '{"target":"other"}'},
            ]
    from backend.apps.training.repository import inference_log_repository as repo_mod
    monkeypatch.setattr(repo_mod, "InferenceLogRepository", lambda: FakeRepo())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # No filters aggregates counts
        r = await ac.get("/admin/inference/labeler/diagnose", headers={"X-API-Key": API_KEY})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["total"] == 5
    # With filters: only BTCUSDT 1m bottom should count 2 entries
        r = await ac.get(
            "/admin/inference/labeler/diagnose",
            params={"only_symbol": "BTCUSDT", "only_interval": "1m", "only_target": "bottom"},
            headers={"X-API-Key": API_KEY},
        )
        assert r.status_code == 200
        d2 = r.json()
        assert d2["status"] == "ok"
    assert d2["total"] == 2
    assert any(g["symbol"] == "BTCUSDT" and g["interval"] == "1m" and g["target"] == "bottom" and g["count"] == 2 for g in d2["groups"]) 


@pytest.mark.asyncio
async def test_quick_validate_wires_filters(monkeypatch):
    # Spy on labeler.run_once to capture args
    calls = {}
    class DummySvc:
        async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.update(kwargs)
            return {"status": "ok", "labeled": 0, "candidates": 0}
    from backend.apps.training.service import auto_labeler as al_mod
    monkeypatch.setattr(al_mod, "get_auto_labeler_service", lambda: DummySvc())

    # Also stub batch predict and queue flush to be no-op fast
    async def fake_batch(**kwargs):
        return {"status": "ok", "attempted": kwargs.get("count", 5), "success": kwargs.get("count", 5), "errors": [], "symbol": kwargs.get("symbol"), "interval": kwargs.get("interval"), "threshold": kwargs.get("threshold")}
    from backend.apps.api import main as main_mod
    monkeypatch.setattr(main_mod, "admin_inference_predict_batch", fake_batch)
    class FakeQ:
        async def flush_now(self):
            return 0
    monkeypatch.setattr(main_mod, "get_inference_log_queue", lambda: FakeQ())
    # Stub calibration to minimal response
    async def fake_cal(**kwargs):
        return {"status": "ok", "ece": 0.1, "brier": 0.2, "mce": 0.3, "samples": 0, "window_seconds": kwargs.get("window_seconds", 1800), "bins": kwargs.get("bins", 10), "target": kwargs.get("target")}
    monkeypatch.setattr(main_mod, "inference_live_calibration", fake_cal)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/admin/inference/quick-validate",
            params={
                "count": 3,
                "target": "bottom",
                "symbol": "XRPUSDT",
                "interval": "1m",
                "labeler_min_age_seconds": 0,
                "labeler_limit": 50,
                "lookahead": 5,
                "drawdown": 0.01,
                "rebound": 0.005,
                "decimals": 2,
            },
            headers={"X-API-Key": API_KEY},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        # Ensure filters/overrides were forwarded to run_once
        assert calls.get("min_age_seconds") == 0
        assert calls.get("limit") == 50
        assert calls.get("lookahead") == 5
        assert abs(calls.get("drawdown") - 0.01) < 1e-9
        assert abs(calls.get("rebound") - 0.005) < 1e-9
        assert calls.get("only_target") == "bottom"
        assert calls.get("only_symbol") == "XRPUSDT"
    assert calls.get("only_interval") == "1m"
    # params echo
    assert body["summary"]["params"].get("decimals") == 2


@pytest.mark.asyncio
async def test_quick_validate_skip_flags_and_model_select(monkeypatch):
    """skip_predict/skip_label 플래그와 모델 선택 파라미터가 반영되는지 검증"""
    calls = {"run_once": None, "batch": None}

    class DummySvc:
        async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
            calls["run_once"] = kwargs
            return {"status": "ok", "labeled": 0}

    from backend.apps.training.service import auto_labeler as al_mod
    monkeypatch.setattr(al_mod, "get_auto_labeler_service", lambda: DummySvc())

    async def fake_batch(**kwargs):
        calls["batch"] = kwargs
        return {"status": "ok", "attempted": kwargs.get("count", 0), "success": kwargs.get("count", 0), "errors": [], "last": {"model_name": "mX", "model_version": "v1", "used_production": False}}

    from backend.apps.api import main as main_mod
    monkeypatch.setattr(main_mod, "admin_inference_predict_batch", fake_batch)

    class FakeQ:
        async def flush_now(self):
            return 0
    monkeypatch.setattr(main_mod, "get_inference_log_queue", lambda: FakeQ())

    async def fake_cal(**kwargs):
        return {"status": "ok", "ece": 0.1, "brier": 0.2, "mce": 0.3, "samples": 0}
    monkeypatch.setattr(main_mod, "inference_live_calibration", fake_cal)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1) skip_predict=true 이면 배치 예측이 호출되지 않고 summary.batch=None
        r1 = await ac.post(
            "/admin/inference/quick-validate",
            params={"skip_predict": True, "target": "bottom"},
            headers={"X-API-Key": API_KEY},
        )
        assert r1.status_code == 200
        body1 = r1.json()
        assert body1["status"] == "ok"
        assert body1["summary"]["batch"] is None
        # label 단계는 호출됨 (skip_label 미설정)
        assert isinstance(calls["run_once"], dict)

        # 2) 모델 선택 파라미터가 배치에 전달되는지 (skip_label=true 로 라벨은 건너뜀)
        calls["run_once"] = None
        calls["batch"] = None
        r2 = await ac.post(
            "/admin/inference/quick-validate",
            params={
                "count": 7,
                "prefer_latest": True,
                "version": "v1234",
                "skip_label": True,
                "skip_flush": True,
                "target": "bottom",
            },
            headers={"X-API-Key": API_KEY},
        )
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["status"] == "ok"
        # 배치 파라미터 전달 확인
        assert isinstance(calls["batch"], dict)
        assert calls["batch"].get("prefer_latest") is True
        assert calls["batch"].get("version") == "v1234"
        assert calls["batch"].get("count") == 7
        # 라벨은 스킵되므로 호출 정보 없음
        assert calls["run_once"] is None
        # skip_flush=true → summary.flushed is None
        assert body2["summary"].get("flushed") is None
        # summary.scope & now_ts 존재 확인
        scope = body2["summary"].get("scope")
        assert isinstance(scope, dict)
        assert scope.get("target") == "bottom"
        assert "now_ts" in body2["summary"] and isinstance(body2["summary"]["now_ts"], (int, float))
    # model metadata surfaced
    model = body2["summary"].get("model")
    assert isinstance(model, dict) and model.get("version") == "v1" and model.get("production") is False
    # run_id, timing.total_sec 존재 확인
    assert isinstance(body2["summary"].get("run_id"), str)
    assert isinstance(body2["summary"].get("timing", {}).get("total_sec"), (int, float))
    # request_id presence (middleware supplies one)
    assert "request_id" in body2["summary"]


@pytest.mark.asyncio
async def test_quick_validate_compact_mode(monkeypatch):
    """compact=true 일 때 응답이 축약 형태인지 확인"""
    class DummySvc:
        async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"status": "ok", "labeled": 1}

    from backend.apps.training.service import auto_labeler as al_mod
    monkeypatch.setattr(al_mod, "get_auto_labeler_service", lambda: DummySvc())

    async def fake_batch(**kwargs):
        return {"status": "ok", "attempted": 2, "success": 2, "errors": []}

    from backend.apps.api import main as main_mod
    monkeypatch.setattr(main_mod, "admin_inference_predict_batch", fake_batch)

    class FakeQ:
        def __init__(self):
            self._n = 3
        async def size(self):
            return self._n
        async def flush_now(self):
            self._n = 0
            return 2
    monkeypatch.setattr(main_mod, "get_inference_log_queue", lambda: FakeQ())

    async def fake_cal(**kwargs):
        return {"status": "ok", "ece": 0.09, "brier": 0.19, "mce": 0.29, "samples": 10}
    monkeypatch.setattr(main_mod, "inference_live_calibration", fake_cal)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/admin/inference/quick-validate",
            params={"compact": True, "count": 2, "target": "bottom"},
            headers={"X-API-Key": API_KEY},
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("status") == "ok"
        assert body.get("compact") is True
        # compact 응답에는 summary만 축약되고, heavy 필드(calibration_before 등)는 제외됨
        assert "calibration_before" not in body
        assert "calibration" not in body
        # calibration_delta는 유지
        assert "calibration_delta" in body
        s = body.get("summary")
        assert isinstance(s, dict)
        for k in ["result_code", "progress", "scope", "now_ts", "batch_attempted", "batch_success", "flushed", "labeled"]:
            assert k in s
        # compact에도 run_id와 total_sec 포함
        assert isinstance(s.get("run_id"), str)
        assert isinstance(s.get("total_sec"), (int, float))
    assert "request_id" in s
    # queue size reported in compact
    assert isinstance(s.get("queue"), dict) and s["queue"].get("before") == 3 and s["queue"].get("after") == 0


@pytest.mark.asyncio
async def test_quick_validate_errors_capture(monkeypatch):
    class DummySvc:
        async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"status": "ok", "labeled": 0}

    from backend.apps.training.service import auto_labeler as al_mod
    monkeypatch.setattr(al_mod, "get_auto_labeler_service", lambda: DummySvc())

    async def fake_batch(**kwargs):
        return {"status": "ok", "attempted": 1, "success": 1, "errors": []}

    from backend.apps.api import main as main_mod
    monkeypatch.setattr(main_mod, "admin_inference_predict_batch", fake_batch)

    class FakeQ:
        async def flush_now(self):
            raise RuntimeError("boom")
    monkeypatch.setattr(main_mod, "get_inference_log_queue", lambda: FakeQ())

    async def fake_cal(**kwargs):
        return {"status": "ok", "ece": 0.1, "brier": 0.2, "mce": 0.3, "samples": 1}
    monkeypatch.setattr(main_mod, "inference_live_calibration", fake_cal)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # normal response includes errors
        r = await ac.post(
            "/admin/inference/quick-validate",
            params={"target": "bottom"},
            headers={"X-API-Key": API_KEY},
        )
        assert r.status_code == 200
        body = r.json()
        assert "flush_error" in body["summary"].get("errors", [])

        # compact response includes errors too
        r2 = await ac.post(
            "/admin/inference/quick-validate",
            params={"target": "bottom", "compact": True},
            headers={"X-API-Key": API_KEY},
        )
        assert r2.status_code == 200
        body2 = r2.json()
        assert "errors" in body2["summary"] and "flush_error" in body2["summary"]["errors"]


@pytest.mark.asyncio
async def test_quick_validate_decimals_rounding(monkeypatch):
    class DummySvc:
        async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"status": "ok", "labeled": 0}

    from backend.apps.training.service import auto_labeler as al_mod
    monkeypatch.setattr(al_mod, "get_auto_labeler_service", lambda: DummySvc())

    async def fake_batch(**kwargs):
        return {"status": "ok", "attempted": 1, "success": 1, "errors": []}

    from backend.apps.api import main as main_mod
    monkeypatch.setattr(main_mod, "admin_inference_predict_batch", fake_batch)

    class FakeQ:
        async def size(self):
            return 1
        async def flush_now(self):
            return 1
    monkeypatch.setattr(main_mod, "get_inference_log_queue", lambda: FakeQ())

    async def fake_cal(**kwargs):
        return {"status": "ok", "ece": 0.123456, "brier": 0.234567, "mce": 0.345678, "samples": 12}
    monkeypatch.setattr(main_mod, "inference_live_calibration", fake_cal)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/admin/inference/quick-validate",
            params={"decimals": 3, "target": "bottom"},
            headers={"X-API-Key": API_KEY},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        # calibration and delta fields should be rounded to 3 decimals
        cal = body.get("calibration")
        calb = body.get("calibration_before")
        cald = body.get("calibration_delta")
        assert isinstance(cal, dict) and isinstance(calb, dict) and isinstance(cald, dict)
        for k in ("ece", "brier", "mce"):
            assert isinstance(cal.get(k), float) and len(f"{cal.get(k):.3f}") > 0
            assert isinstance(cald.get(k), float)
        # timing also rounded
        t = body["summary"]["timing"]
        assert all(isinstance(t.get(x), (int, float)) for x in ("predict_sec", "label_sec", "total_sec"))


@pytest.mark.asyncio
async def test_quick_validate_health_criteria(monkeypatch):
    class DummySvc:
        async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"status": "ok", "labeled": 0}

    from backend.apps.training.service import auto_labeler as al_mod
    monkeypatch.setattr(al_mod, "get_auto_labeler_service", lambda: DummySvc())

    async def fake_batch(**kwargs):
        return {"status": "ok", "attempted": 1, "success": 1, "errors": []}

    from backend.apps.api import main as main_mod
    monkeypatch.setattr(main_mod, "admin_inference_predict_batch", fake_batch)

    class FakeQ:
        async def flush_now(self):
            return 0
    monkeypatch.setattr(main_mod, "get_inference_log_queue", lambda: FakeQ())

    async def fake_cal_ok(**kwargs):
        return {"status": "ok", "ece": 0.05, "brier": 0.12, "mce": 0.08, "samples": 50}
    async def fake_cal_bad(**kwargs):
        return {"status": "ok", "ece": 0.2, "brier": 0.3, "mce": 0.5, "samples": 5}

    # Pass case
    monkeypatch.setattr(main_mod, "inference_live_calibration", fake_cal_ok)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/admin/inference/quick-validate",
            params={"target": "bottom", "ece_target": 0.1, "min_samples": 10, "max_mce": 0.2},
            headers={"X-API-Key": API_KEY},
        )
        assert r.status_code == 200
        body = r.json()
        h = body["summary"].get("health")
        assert isinstance(h, dict) and h.get("pass") is True

    # Fail case
    monkeypatch.setattr(main_mod, "inference_live_calibration", fake_cal_bad)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r2 = await ac.post(
            "/admin/inference/quick-validate",
            params={"target": "bottom", "ece_target": 0.1, "min_samples": 10, "max_mce": 0.2},
            headers={"X-API-Key": API_KEY},
        )
        assert r2.status_code == 200
        body2 = r2.json()
        h2 = body2["summary"].get("health")
        assert isinstance(h2, dict) and h2.get("pass") is False
        reasons = h2.get("reasons", [])
        assert "ece_above_target" in reasons and "insufficient_samples" in reasons and "mce_above_max" in reasons


@pytest.mark.asyncio
async def test_quick_validate_has_result_code_and_progress(monkeypatch):
    """quick-validate 응답에 result_code/progress가 포함되는지 확인"""
    class DummySvc:
        async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"status": "ok", "labeled": 0}

    from backend.apps.training.service import auto_labeler as al_mod
    monkeypatch.setattr(al_mod, "get_auto_labeler_service", lambda: DummySvc())

    async def fake_batch(**kwargs):
        return {"status": "ok", "attempted": 0, "success": 0, "errors": []}

    from backend.apps.api import main as main_mod
    monkeypatch.setattr(main_mod, "admin_inference_predict_batch", fake_batch)

    class FakeQ:
        async def flush_now(self):
            return 0
    monkeypatch.setattr(main_mod, "get_inference_log_queue", lambda: FakeQ())

    async def fake_cal(**kwargs):
        # 캘리브레이션 상태만 OK로 응답
        return {"status": "ok", "ece": 0.1, "brier": 0.2, "mce": 0.3, "samples": 0}
    monkeypatch.setattr(main_mod, "inference_live_calibration", fake_cal)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/admin/inference/quick-validate",
            params={"skip_predict": True, "skip_label": True, "target": "bottom"},
            headers={"X-API-Key": API_KEY},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        summary = body.get("summary", {})
        assert isinstance(summary, dict)
        # result_code 는 캘리브레이션 OK 이므로 'calibrated' 예상
        assert summary.get("result_code") in ("calibrated", "labeled", "ok", "no_data")
        # progress 키 존재 및 불리언 값 확인
        progress = summary.get("progress")
        assert isinstance(progress, dict)
        for k in ["diagnose_scoped_reduced", "diagnose_total_reduced", "labeled_positive", "calibration_improved", "samples_increase"]:
            assert k in progress and isinstance(progress[k], bool)
        # warnings present and reflect skip flags
        assert "predict_skipped" in summary.get("warnings", [])
        assert "label_skipped" in summary.get("warnings", [])


@pytest.mark.asyncio
async def test_quick_validate_warnings_no_data(monkeypatch):
    class DummySvc:
        async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"status": "ok", "labeled": 0}

    from backend.apps.training.service import auto_labeler as al_mod
    monkeypatch.setattr(al_mod, "get_auto_labeler_service", lambda: DummySvc())

    async def fake_batch(**kwargs):
        return {"status": "ok", "attempted": 0, "success": 0, "errors": []}

    from backend.apps.api import main as main_mod
    monkeypatch.setattr(main_mod, "admin_inference_predict_batch", fake_batch)

    class FakeQ:
        async def flush_now(self):
            return 0
    monkeypatch.setattr(main_mod, "get_inference_log_queue", lambda: FakeQ())

    async def fake_cal(**kwargs):
        return {"status": "no_data"}
    monkeypatch.setattr(main_mod, "inference_live_calibration", fake_cal)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/admin/inference/quick-validate",
            params={"skip_predict": True, "target": "bottom"},
            headers={"X-API-Key": API_KEY},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        # no_data warning expected
        warnings = body["summary"].get("warnings", [])
        assert "calibration_no_data" in warnings


    @pytest.mark.asyncio
    async def test_quick_validate_prod_ece_and_gap_fields(monkeypatch):
        class DummySvc:
            async def run_once(self, **kwargs):  # type: ignore[no-untyped-def]
                return {"status": "ok", "labeled": 0}

        from backend.apps.training.service import auto_labeler as al_mod
        monkeypatch.setattr(al_mod, "get_auto_labeler_service", lambda: DummySvc())

        async def fake_batch(**kwargs):
            return {"status": "ok", "attempted": 1, "success": 1, "errors": []}

        from backend.apps.api import main as main_mod
        monkeypatch.setattr(main_mod, "admin_inference_predict_batch", fake_batch)

        # Stub registry repo to control prod ECE
        class FakeRepo:
            async def fetch_latest(self, name, model_type, limit=5):  # type: ignore[no-untyped-def]
                return [
                    {"status": "production", "metrics": {"ece": 0.10}},
                ]

        monkeypatch.setattr(main_mod, "ModelRegistryRepository", lambda: FakeRepo())

        class FakeQ:
            async def flush_now(self):
                return 0
        monkeypatch.setattr(main_mod, "get_inference_log_queue", lambda: FakeQ())

        # Live calibration with ECE=0.13 → gap=0.03, rel=0.3
        async def fake_cal(**kwargs):
            return {"status": "ok", "ece": 0.13, "brier": 0.2, "mce": 0.3, "samples": 20}
        monkeypatch.setattr(main_mod, "inference_live_calibration", fake_cal)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Full response
            r = await ac.post(
                "/admin/inference/quick-validate",
                params={"target": "bottom", "decimals": 2},
                headers={"X-API-Key": API_KEY},
            )
            assert r.status_code == 200
            body = r.json()
            s = body["summary"]
            assert isinstance(s.get("production_ece"), (int, float)) and abs(s["production_ece"] - 0.10) < 1e-9
            assert isinstance(s.get("ece_gap_to_prod"), (int, float)) and round(s["ece_gap_to_prod"], 2) == 0.03
            assert isinstance(s.get("ece_gap_to_prod_rel"), (int, float)) and round(s["ece_gap_to_prod_rel"], 2) == 0.3
            # Hints should include drift note
            assert any("prod보다 높음" in h for h in s.get("hints", []))

            # Compact response
            r2 = await ac.post(
                "/admin/inference/quick-validate",
                params={"target": "bottom", "compact": True, "decimals": 2},
                headers={"X-API-Key": API_KEY},
            )
            assert r2.status_code == 200
            body2 = r2.json()
            s2 = body2["summary"]
            assert isinstance(s2.get("ece_gap_to_prod"), (int, float)) and s2["ece_gap_to_prod"] == 0.03
            assert isinstance(s2.get("ece_gap_to_prod_rel"), (int, float)) and s2["ece_gap_to_prod_rel"] == 0.3
