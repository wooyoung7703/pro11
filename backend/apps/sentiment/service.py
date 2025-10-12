from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Iterable
import math


def _alpha(n: int) -> float:
    return 2.0 / (n + 1.0)


def compute_ema(series: list[tuple[int, float]], window_n: int) -> list[tuple[int, float]]:
    """Compute EMA over (ts_ms, value) sorted by ts.
    Returns list of (ts_ms, ema).
    """
    if not series:
        return []
    a = _alpha(window_n)
    out: list[tuple[int, float]] = []
    ema = series[0][1]
    out.append((series[0][0], ema))
    for ts, v in series[1:]:
        ema = a * v + (1 - a) * ema
        out.append((ts, ema))
    return out


def rolling_std(series: list[float], window_n: int) -> list[float]:
    out: list[float] = []
    if window_n <= 1:
        return [0.0 for _ in series]
    from collections import deque
    q = deque(maxlen=window_n)
    for v in series:
        q.append(v)
        if len(q) < 2:
            out.append(0.0)
        else:
            m = sum(q) / len(q)
            var = sum((x - m) ** 2 for x in q) / (len(q) - 1)
            out.append(math.sqrt(var))
    return out


def pos_ratio(series: list[float]) -> list[float]:
    out: list[float] = []
    pos = 0
    for i, v in enumerate(series, start=1):
        if v > 0:
            pos += 1
        out.append(pos / i)
    return out


def deltas(series: list[float], k: int) -> list[float]:
    out = [0.0] * len(series)
    for i in range(k, len(series)):
        out[i] = series[i] - series[i - k]
    return out


def bucketize(points: list[tuple[int, float]], step_ms: int, pos_threshold: float = 0.0) -> list[tuple[int, float, int, float]]:
    """Group points into step_ms buckets and compute mean value, count, and positive ratio.
    Returns list of (bucket_ts, mean_value, count, pos_ratio).
    """
    if not points:
        return []
    out: list[tuple[int, float, int, float]] = []
    cur_bucket = (points[0][0] // step_ms) * step_ms
    acc: list[float] = []
    pos = 0
    for ts, v in points:
        b = (ts // step_ms) * step_ms
        if b != cur_bucket:
            if acc:
                mean_v = sum(acc) / len(acc)
                pr = pos / len(acc)
                out.append((cur_bucket, mean_v, len(acc), pr))
                acc = []
                pos = 0
            cur_bucket = b
        acc.append(v)
        if v > pos_threshold:
            pos += 1
    if acc:
        mean_v = sum(acc) / len(acc)
        pr = pos / len(acc)
        out.append((cur_bucket, mean_v, len(acc), pr))
    return out


def aggregate_with_windows(points: list[tuple[int, float]], step_ms: int, ema_windows: list[int], pos_threshold: float = 0.0) -> dict[str, list[tuple[int, float]]]:
    """Return dict series keyed by: score, count, pos_ratio, ema_{N}, d1, d5, vol_30.
    points must be sorted by ts.
    """
    buckets = bucketize(points, step_ms, pos_threshold=pos_threshold)
    result: dict[str, list[tuple[int, float]]] = {}
    # unpack
    tses = [t for t, _, _, _ in buckets]
    values = [v for _, v, _, _ in buckets]
    counts = [c for _, _, c, _ in buckets]
    posr = [p for _, _, _, p in buckets]
    result["score"] = list(zip(tses, values))
    result["count"] = list(zip(tses, [float(c) for c in counts]))
    result["pos_ratio"] = list(zip(tses, posr))
    # EMA windows
    for n in ema_windows:
        ema = compute_ema(list(zip(tses, values)), n)
        result[f"ema_{n}"] = ema
    # deltas
    d1_vals = deltas(values, 1)
    d5_vals = deltas(values, 5)
    result["d1"] = list(zip(tses, d1_vals))
    result["d5"] = list(zip(tses, d5_vals))
    # volatility (30 window)
    vol_vals = rolling_std(values, 30)
    result["vol_30"] = list(zip(tses, vol_vals))
    return result
