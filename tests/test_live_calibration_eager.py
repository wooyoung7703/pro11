import os
import re
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient


@contextmanager
def _env(**kwargs):
    old = {}
    for k, v in kwargs.items():
        old[k] = os.environ.get(k)
        os.environ[k] = str(v)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _parse_counter(metrics_text: str, name: str) -> float:
    # Prom counter exposition: e.g., `inference_calibration_eager_label_runs_total 3.0`
    pat = re.compile(rf"^{re.escape(name)}\s+(\d+(?:\.\d+)?)$", re.MULTILINE)
    m = pat.search(metrics_text)
    return float(m.group(1)) if m else 0.0


def test_live_calibration_eager_attempt(monkeypatch):
    # Speed up startup and bypass auth for test
    with _env(FAST_STARTUP=1, DISABLE_API_KEY=1):
        # Lazy import under env context to apply FAST_STARTUP
        from backend.apps.api.main import app, InferenceLogRepository

        # 1) Force no_data window by returning empty rows for calibration fetch
        async def _empty_fetch(*args, **kwargs):
            return []
        monkeypatch.setattr(InferenceLogRepository, "fetch_window_for_live_calibration", _empty_fetch, raising=True)

        # 2) Patch labeler service to a dummy with async run_once
        class _DummyLabeler:
            async def run_once(self, **kwargs):  # noqa: D401
                return {"status": "ok", "labeled": 0}

        import backend.apps.training.service.auto_labeler as _als
        monkeypatch.setattr(_als, "get_auto_labeler_service", lambda: _DummyLabeler(), raising=True)

        client = TestClient(app)

        # Read baseline metric value
        r0 = client.get("/metrics")
        assert r0.status_code == 200
        base = _parse_counter(r0.text, "inference_calibration_eager_label_runs_total")

        # Call live calibration with small window; eager_label defaults to true
        r = client.get("/api/inference/calibration/live", params={"window_seconds": 60, "bins": 5})
        assert r.status_code == 200
        j = r.json()
        assert j.get("status") == "no_data"
        # Should reflect that eager attempt was made
        assert j.get("attempted_eager_label") is True
        assert "auto_labeler_triggered" in j

        # Metric should increase by at least 1
        r1 = client.get("/metrics")
        assert r1.status_code == 200
        cur = _parse_counter(r1.text, "inference_calibration_eager_label_runs_total")
        assert cur >= base + 1.0
