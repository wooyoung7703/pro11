import pytest, time
from backend.apps.api.main import calibration_monitor_status, _streak_state, CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS
from backend.apps.training.auto_retrain_scheduler import STATE

@pytest.mark.asyncio
async def test_calibration_retrain_improvement(monkeypatch):
    # Reset state relevant fields
    STATE.last_calibration_effect_snapshot_ts = None
    STATE.last_calibration_effect_evaluated_ts = None
    STATE.last_calibration_delta_before = 0.2
    retrain_ts = time.time() - 5
    STATE.last_calibration_retrain_ts = retrain_ts

    # baseline counter values
    improved_before = CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS.labels(result="improved")._value.get()
    worsened_before = CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS.labels(result="worsened")._value.get()
    no_change_before = CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS.labels(result="no_change")._value.get()

    # Snapshot showing improvement (delta 0.2 -> 0.1)
    _streak_state.last_snapshot = {
        "ts": time.time(),
        "live_ece": 0.15,
        "prod_ece": 0.05,
        "delta": 0.10,
        "abs_drift": False,
        "rel_drift": False,
        "rel_gap": 1.0,
        "sample_count": 500,
    }
    result = await calibration_monitor_status()
    assert result["calibration_retrain_delta_before"] == 0.2
    imp = result["calibration_retrain_delta_improvement"]
    assert imp is not None and 0.45 < imp < 0.55
    # Counter incremented once for improved
    improved_after = CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS.labels(result="improved")._value.get()
    assert improved_after == improved_before + 1
    # Idempotent (no second increment without new snapshot)
    result2 = await calibration_monitor_status()
    improved_after2 = CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS.labels(result="improved")._value.get()
    assert improved_after2 == improved_after
    assert CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS.labels(result="worsened")._value.get() == worsened_before
    assert CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS.labels(result="no_change")._value.get() == no_change_before

    # Now simulate worsened scenario with new retrain and snapshot
    STATE.last_calibration_delta_before = 0.2
    STATE.last_calibration_retrain_ts = time.time() + 1  # future relative to previous snapshot so need new snapshot after
    STATE.last_calibration_effect_snapshot_ts = None
    _streak_state.last_snapshot = {
        "ts": time.time() + 2,
        "live_ece": 0.30,
        "prod_ece": 0.05,
        "delta": 0.25,  # worse
        "abs_drift": False,
        "rel_drift": False,
        "rel_gap": 1.0,
        "sample_count": 500,
    }
    result3 = await calibration_monitor_status()
    imp2 = result3["calibration_retrain_delta_improvement"]
    assert imp2 is not None and imp2 < 0
    worsened_after = CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS.labels(result="worsened")._value.get()
    assert worsened_after == worsened_before + 1
    # Ensure no_change unchanged
    assert CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS.labels(result="no_change")._value.get() == no_change_before