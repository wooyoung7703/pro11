from __future__ import annotations
import math
from backend.apps.training.service.bottom_labeler import compute_bottom_event_label, label_for_created_ts

# Synthetic candles: close=10 baseline, then dip and rebound
# Each item: {open, high, low, close, open_time, close_time}

def make_candle(open_time_ms, close_time_ms, o, h, l, c):
    return {"open_time": open_time_ms, "close_time": close_time_ms, "open": o, "high": h, "low": l, "close": c}


def test_bottom_labeler_positive_case():
    # t0 close=100 then drop to 95 (-5%) then rebound to 98 (+3.16% from 95)
    base_ct = 1_000_000
    candles = [
        make_candle(base_ct, base_ct+60_000, 100, 101, 99, 100),
        make_candle(base_ct+60_000, base_ct+120_000, 100, 100, 95, 96),  # min low 95
        make_candle(base_ct+120_000, base_ct+180_000, 96, 98, 96, 98),   # rebound to 98
        make_candle(base_ct+180_000, base_ct+240_000, 98, 99, 97, 98),
    ]
    y = compute_bottom_event_label(candles, 0, lookahead=3, drawdown=0.05, rebound=0.03)
    assert y == 1


def test_bottom_labeler_negative_no_drawdown():
    base_ct = 2_000_000
    candles = [
        make_candle(base_ct, base_ct+60_000, 100, 101, 99, 100),
        make_candle(base_ct+60_000, base_ct+120_000, 100, 101, 99, 100),  # no drop
        make_candle(base_ct+120_000, base_ct+180_000, 100, 101, 99, 100),
    ]
    y = compute_bottom_event_label(candles, 0, lookahead=2, drawdown=0.05, rebound=0.03)
    assert y == 0


def test_bottom_labeler_negative_no_rebound():
    base_ct = 3_000_000
    candles = [
        make_candle(base_ct, base_ct+60_000, 100, 101, 90, 91),  # drop >= 10%
        make_candle(base_ct+60_000, base_ct+120_000, 91, 92, 89, 90),    # no rebound >= 3%
        make_candle(base_ct+120_000, base_ct+180_000, 90, 91, 89, 90),
    ]
    y = compute_bottom_event_label(candles, 0, lookahead=2, drawdown=0.05, rebound=0.03)
    assert y == 0


def test_bottom_labeler_exact_thresholds():
    base_ct = 4_000_000
    candles = [
        make_candle(base_ct, base_ct+60_000, 100, 101, 95, 95),   # exactly -5%
        make_candle(base_ct+60_000, base_ct+120_000, 95, 97, 94, 97),  # rebound to 97 => (97-95)/95 = 2.105% < 3%
        make_candle(base_ct+120_000, base_ct+180_000, 97, 98, 96, 98),  # to 98 => 3.157% >= 3%
    ]
    y = compute_bottom_event_label(candles, 0, lookahead=2, drawdown=0.05, rebound=0.03)
    assert y == 1


def test_label_for_created_ts_alignment():
    base_ct = 5_000_000
    candles = [
        make_candle(base_ct, base_ct+60_000, 100, 101, 99, 100),
        make_candle(base_ct+60_000, base_ct+120_000, 100, 100, 95, 96),
        make_candle(base_ct+120_000, base_ct+180_000, 96, 98, 96, 98),
    ]
    # created_ts at first close_time -> index 0
    lb0 = label_for_created_ts(candles, created_ts_sec=(base_ct+60_000)/1000.0, lookahead=2, drawdown=0.05, rebound=0.03)
    assert lb0 == 1
    # created_ts before first candle -> None
    lb_none = label_for_created_ts(candles, created_ts_sec=(base_ct-1)/1000.0, lookahead=2, drawdown=0.05, rebound=0.03)
    assert lb_none is None
