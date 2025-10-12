from __future__ import annotations
from collections import deque
from typing import List, Dict, Any, Deque, Optional

# Simple calculators operating on close prices & volumes

def calc_returns(prices: List[float], horizons=(1,5,10,15)) -> Dict[str, float | None]:
    out: Dict[str, float | None] = {}
    if not prices:
        return {f"ret_{h}": None for h in horizons}
    last = prices[-1]
    for h in horizons:
        if len(prices) > h:
            prev = prices[-1 - h]
            out[f"ret_{h}"] = (last - prev) / prev if prev != 0 else None
        else:
            out[f"ret_{h}"] = None
    return out

def calc_moving_averages(prices: List[float], windows=(20,50)) -> Dict[str, float | None]:
    out: Dict[str, float | None] = {}
    for w in windows:
        if len(prices) >= w:
            out[f"ma_{w}"] = sum(prices[-w:]) / w
        else:
            out[f"ma_{w}"] = None
    return out

def calc_rolling_vol(prices: List[float], window=20) -> Dict[str, float | None]:
    if len(prices) < window:
        return {"rolling_vol_20": None}
    import math
    segment = prices[-window:]
    mean = sum(segment)/window
    var = sum((p-mean)**2 for p in segment)/window
    return {"rolling_vol_20": math.sqrt(var)}

def calc_rsi(prices: List[float], period=14) -> Dict[str, float | None]:
    if len(prices) <= period:
        return {"rsi_14": None}
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        diff = prices[i] - prices[i-1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return {"rsi_14": 100.0}
    rs = gains / losses if losses != 0 else None
    if rs is None:
        return {"rsi_14": None}
    rsi = 100 - (100 / (1 + rs))
    return {"rsi_14": rsi}

def compute_all(prices: List[float]) -> Dict[str, float | None]:
    features: Dict[str, float | None] = {}
    features.update(calc_returns(prices))
    features.update(calc_moving_averages(prices))
    features.update(calc_rolling_vol(prices))
    features.update(calc_rsi(prices))
    return features
