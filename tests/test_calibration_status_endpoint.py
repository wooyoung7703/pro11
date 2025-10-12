import pytest
import httpx
from backend.apps.api.main import app, _streak_state


def _client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_calibration_status_endpoint_basic():
    async with _client() as ac:
        resp = await ac.get("/api/monitor/calibration/status")
        assert resp.status_code == 200
        data = resp.json()
    assert "enabled" in data
    assert "abs_streak" in data and "rel_streak" in data
    assert "thresholds" in data and "abs" in data["thresholds"] and "rel" in data["thresholds"]


@pytest.mark.asyncio
async def test_calibration_status_endpoint_streak_update():
    _streak_state.abs_streak = 5
    _streak_state.rel_streak = 3
    _streak_state.last_snapshot = {"ts": 123.4, "live_ece": 0.07, "prod_ece": 0.05, "delta": 0.02}
    async with _client() as ac:
        resp = await ac.get("/api/monitor/calibration/status")
        assert resp.status_code == 200
        data = resp.json()
    assert data["abs_streak"] == 5
    assert data["rel_streak"] == 3
    assert data["last_snapshot"]["delta"] == 0.02
