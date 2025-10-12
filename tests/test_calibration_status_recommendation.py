import pytest
import httpx
from backend.apps.api.main import app, _streak_state, cfg

def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

@pytest.mark.asyncio
async def test_no_recommend_without_snapshot():
    # reset state
    _streak_state.abs_streak = 0
    _streak_state.rel_streak = 0
    _streak_state.last_snapshot = None
    async with _client() as ac:
        r = await ac.get("/api/monitor/calibration/status")
    data = r.json()
    assert data["recommend_retrain"] is False

@pytest.mark.asyncio
async def test_abs_streak_recommendation():
    # Simulate snapshot with abs drift flagged
    cfg.calibration_monitor_enabled = True  # ensure logic path active
    _streak_state.abs_streak = cfg.calibration_monitor_abs_streak_trigger
    _streak_state.rel_streak = 0
    _streak_state.last_snapshot = {
        "ts": 1.0,
        "live_ece": 0.12,
        "live_mce": 0.2,
        "prod_ece": 0.05,
        "delta": 0.07,
        "abs_threshold": cfg.calibration_monitor_ece_drift_abs,
        "rel_threshold": cfg.calibration_monitor_ece_drift_rel,
        "abs_drift": True,
        "rel_drift": False,
        "rel_gap": 0.07 / 0.05,
        "sample_count": 500,
    }
    async with _client() as ac:
        r = await ac.get("/api/monitor/calibration/status")
    data = r.json()
    assert data["recommend_retrain"] is True
    assert any(reason.startswith("abs_drift_streak") for reason in data["recommend_retrain_reasons"])  # streak reason present

@pytest.mark.asyncio
async def test_delta_fast_path_recommendation():
    # Simulate snapshot with large delta >= 2x abs threshold but no streak yet
    cfg.calibration_monitor_enabled = True
    _streak_state.abs_streak = 0
    _streak_state.rel_streak = 0
    big_delta = 2 * cfg.calibration_monitor_ece_drift_abs
    _streak_state.last_snapshot = {
        "ts": 2.0,
        "live_ece": 0.2,
        "live_mce": 0.25,
        "prod_ece": 0.05,
        "delta": big_delta,
        "abs_threshold": cfg.calibration_monitor_ece_drift_abs,
        "rel_threshold": cfg.calibration_monitor_ece_drift_rel,
        "abs_drift": True,
        "rel_drift": True,
        "rel_gap": big_delta / 0.05,
        "sample_count": 600,
    }
    async with _client() as ac:
        r = await ac.get("/api/monitor/calibration/status")
    data = r.json()
    assert data["recommend_retrain"] is True
    assert "delta_gt_2x_abs_threshold" in data["recommend_retrain_reasons"]
