from fastapi.testclient import TestClient

from backend.apps.api.main import app


client = TestClient(app)


def test_bocpd_status_endpoint_basic():
    r = client.get("/api/guard/bocpd/status")
    assert r.status_code == 200
    data = r.json()
    # Basic shape checks (payload is wrapped with status/events/last_updated)
    assert "status" in data
    status = data["status"]
    for k in ("enabled", "hazard", "min_down", "cooldown_sec"):
        assert k in status


def test_scale_in_requirement_setting_soft_default_get():
    # DB가 없는 테스트 환경에서는 PUT이 실패할 수 있으므로 GET으로 soft default만 검증
    key = "live_trading.scale_in.require_bocpd"
    res = client.get(f"/admin/settings/{key}")
    assert res.status_code == 200
    data = res.json()
    # 값이 bool 형태로 노출되는지만 확인 (soft default 경로)
    if isinstance(data, dict) and "value" in data:
        assert isinstance(data["value"], bool)
    elif isinstance(data, dict) and "item" in data and isinstance(data["item"], dict):
        assert isinstance(data["item"].get("value"), bool)
    else:
        assert isinstance(data, bool)


def test_health_endpoint_basic():
    r = client.get("/health")
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, dict)
    assert payload.get("status") in ("ok", "degraded", "error")