from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple


def _find_start_index(candles: List[Dict[str, Any]], created_ts_sec: float) -> Optional[int]:
    """Return index of first candle whose close_time (ms) >= created_ts_sec.

    candles must be chronological (ascending by open_time).
    """
    for i, c in enumerate(candles):
        try:
            ct_ms = float(c.get("close_time"))
            if (ct_ms / 1000.0) >= created_ts_sec:
                return i
        except Exception:
            continue
    return None


def compute_bottom_event_label(
    candles: List[Dict[str, Any]],
    start_idx: int,
    lookahead: int,
    drawdown: float,
    rebound: float,
) -> Optional[int]:
    """Compute bottom-event label for candles[start_idx] over the next lookahead bars.

    Definition:
      1) From price p0 = close[start_idx], within next H bars find minimum low Lmin.
         Require drop = (Lmin - p0)/p0 <= -drawdown.
      2) From the bar of that minimum to the end of the window, find maximum high Hmax.
         Require rebound = (Hmax - Lmin)/Lmin >= rebound.

    Returns 1 if both satisfied, 0 if not, or None if insufficient future bars.
    """
    n = len(candles)
    if start_idx < 0 or start_idx >= n:
        return None
    # need at least 1 future bar to evaluate
    end_idx = min(n - 1, start_idx + lookahead)
    if end_idx <= start_idx:
        return None
    try:
        p0 = float(candles[start_idx]["close"])  # reference close price
    except Exception:
        return None
    # find min low in (start_idx+1 .. end_idx)
    min_low = None
    min_idx = None
    for i in range(start_idx + 1, end_idx + 1):
        try:
            lo = float(candles[i]["low"])  # use low for drawdown detection
        except Exception:
            continue
        if min_low is None or lo < min_low:
            min_low = lo
            min_idx = i
    if min_low is None or min_idx is None:
        return None
    try:
        drop = (min_low - p0) / p0
    except Exception:
        return None
    if drop > (-abs(drawdown)):
        return 0
    # rebound: from min_idx to end_idx, max high
    max_high = None
    for i in range(min_idx, end_idx + 1):
        try:
            hi = float(candles[i]["high"])  # use high for rebound potential
        except Exception:
            continue
        if max_high is None or hi > max_high:
            max_high = hi
    if max_high is None:
        return 0
    try:
        rb = (max_high - min_low) / min_low
    except Exception:
        return 0
    return 1 if rb >= abs(rebound) else 0


def label_for_created_ts(
    candles_chrono: List[Dict[str, Any]],
    created_ts_sec: float,
    *,
    lookahead: int,
    drawdown: float,
    rebound: float,
) -> Optional[int]:
    """Convenience: locate start bar for created_ts and compute label.

    Returns 1/0, or None if not enough bars.
    """
    idx = _find_start_index(candles_chrono, created_ts_sec)
    if idx is None:
        return None
    return compute_bottom_event_label(candles_chrono, idx, lookahead, drawdown, rebound)


__all__ = [
    "compute_bottom_event_label",
    "label_for_created_ts",
]
