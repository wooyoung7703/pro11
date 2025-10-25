import math
from backend.apps.trading.service.exit_policy import ExitPolicyConfig, ExitState, evaluate_exit


def test_percent_trailing_basic():
    cfg = ExitPolicyConfig(trail_mode="percent", trail_percent=0.1, time_stop_bars=0)
    st = ExitState(peak_price=None, bars_since_entry=0)
    prices = [100, 105, 112, 110, 101]  # peak 112, 10% pullback threshold = 100.8
    res = None
    for i, p in enumerate(prices):
        st.bars_since_entry = i
        res = evaluate_exit(cfg, st, p)
        if res["exit"]:
            break
    assert res is not None
    # Expect exit when price crosses below 112 * (1 - 0.1) = 100.8 -> at 101 still above threshold, next below would exit
    assert res["exit"] is False
    # Now drop to 100 (below 100.8) -> exit
    st.bars_since_entry += 1
    res2 = evaluate_exit(cfg, st, 100)
    assert res2["exit"] is True
    assert res2["reason"] == "trail_percent"


def test_time_stop_triggers():
    cfg = ExitPolicyConfig(trail_mode="percent", trail_percent=0.2, time_stop_bars=3)
    st = ExitState(peak_price=None, bars_since_entry=0)
    # No price drop enough to trail; time stop at bar >= 3
    for i, p in enumerate([100, 101, 102, 103]):
        st.bars_since_entry = i
        res = evaluate_exit(cfg, st, p)
    assert res["exit"] is True
    assert res["reason"] == "time_stop"
