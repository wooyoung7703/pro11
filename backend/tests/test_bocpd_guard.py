import math

from backend.apps.trading.service.bocpd_guard import BocpdGuardService, BocpdConfig


def test_bocpd_guard_drop_then_recovery_and_new_peak_resets():
    # min_down=0.1 (10%)
    svc = BocpdGuardService(BocpdConfig(enabled=True, min_down=0.1, cooldown_sec=0))

    # Initialize around 100
    svc.update(100)
    s0 = svc.status()
    assert s0["enabled"] is True
    assert math.isclose(s0["peak_price"], 100.0)
    assert s0["down_detected"] is False

    # New peak to 105 should keep detection False and update peak
    svc.update(105)
    s1 = svc.status()
    assert math.isclose(s1["peak_price"], 105.0)
    assert s1["down_detected"] is False

    # Drop more than 10% -> allow entry (down_detected True)
    svc.update(93.45)
    e1 = svc.evaluate(93.45)
    assert e1["allow_entry"] is True
    assert e1["state"]["down_detected"] is True

    # Partial recovery (drop below 10%) should clear detection and block
    svc.update(100)
    e2 = svc.evaluate(100)
    assert e2["allow_entry"] is False
    assert "min_down_not_met" in e2["reasons"]
    assert e2["state"]["down_detected"] is False

    # New higher peak resets; still blocked without new drop
    svc.update(110)
    e3 = svc.evaluate(110)
    assert e3["allow_entry"] is False
    assert "min_down_not_met" in e3["reasons"]
    assert math.isclose(e3["state"]["peak_price"], 110.0)


def test_bocpd_guard_min_down_zero_pass_through():
    svc = BocpdGuardService(BocpdConfig(enabled=True, min_down=0.0))
    svc.update(100)
    e = svc.evaluate(100)
    assert e["allow_entry"] is True
    # Helpful breadcrumb reason for diagnostics
    assert "min_down_disabled" in e["reasons"]