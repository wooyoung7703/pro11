from starlette.testclient import TestClient

from backend.apps.api.main import app


def test_exit_metrics_exposed_on_metrics_endpoint():
    client = TestClient(app)
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.content
    # Basic presence checks for newly added metrics
    assert b"trading_exit_events_total" in body
    assert b"trading_exit_return_bps" in body
    assert b"trading_exit_hold_seconds" in body
    assert b"trading_exit_trail_pullback_bps" in body
