import pytest
import httpx
from backend.apps.api.main import app, _streak_state, cfg


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_recommendation_metric_transitions():
    cfg.calibration_monitor_enabled = True
    # Reset state
    _streak_state.abs_streak = 0
    _streak_state.rel_streak = 0
    _streak_state.last_snapshot = None
    _streak_state.last_recommend = False
    async with _client() as ac:
        # First call with no snapshot -> no recommendation
        r1 = await ac.get("/api/monitor/calibration/status")
        assert r1.status_code == 200
        # Set snapshot with abs streak trigger reached
        _streak_state.abs_streak = cfg.calibration_monitor_abs_streak_trigger
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
        r2 = await ac.get("/api/monitor/calibration/status")
        assert r2.json()["recommend_retrain"] is True
        # Clear conditions
        _streak_state.abs_streak = 0
        _streak_state.last_snapshot = {
            "ts": 2.0,
            "live_ece": 0.051,
            "live_mce": 0.08,
            "prod_ece": 0.05,
            "delta": 0.001,
            "abs_threshold": cfg.calibration_monitor_ece_drift_abs,
            "rel_threshold": cfg.calibration_monitor_ece_drift_rel,
            "abs_drift": False,
            "rel_drift": False,
            "rel_gap": 0.001/0.05,
            "sample_count": 520,
        }
        r3 = await ac.get("/api/monitor/calibration/status")
        assert r3.json()["recommend_retrain"] is False
        # Fetch metrics output and assert gauge appears (can't easily assert counters values without parsing)
        metrics_resp = await ac.get("/metrics")
        text = metrics_resp.text
        assert "calibration_retrain_recommendation" in text
        assert "calibration_retrain_recommendation_events_total" in text