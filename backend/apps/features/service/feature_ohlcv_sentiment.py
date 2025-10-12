from __future__ import annotations
from typing import List, Dict, Any, Optional
import math

# NOTE: 이 모듈은 기존 compute_all(prices) 보다 확장된 OHLCV + 뉴스 감성 집계를 포괄하는 피처를 산출합니다.
# 입력은 캔들 리스트(딕셔너리)와 뉴스 감성 요약(예: 최근 60m) 구조를 기대합니다.
# candles: [{open_time, close_time, open, high, low, close, volume}, ...] (시간 오름차순)
# sentiment_agg: { avg, count, pos, neg, neutral } (최근 60분 등 고정 윈도우)

DEFAULT_EMA_FAST = 12
DEFAULT_EMA_SLOW = 26
DEFAULT_ATR_PERIOD = 14
DEFAULT_RSI_PERIOD = 14


def _ema(series: List[float], period: int) -> Optional[float]:
    if len(series) < period:
        return None
    k = 2 / (period + 1)
    ema = series[0]
    for v in series[1:]:
        ema = v * k + ema * (1 - k)
    return ema

def _rsi(prices: List[float], period: int) -> Optional[float]:
    if len(prices) <= period:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        diff = prices[i] - prices[i-1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    if losses == 0:
        return 100.0
    rs = gains / losses if losses != 0 else None
    if rs is None:
        return None
    return 100 - (100 / (1 + rs))

def _atr(candles: List[Dict[str, float]], period: int) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs: List[float] = []
    for i in range(-period, 0):
        c = candles[i]
        prev = candles[i-1]
        high = c["high"]; low = c["low"]; close_prev = prev["close"]
        tr = max(high - low, abs(high - close_prev), abs(low - close_prev))
        trs.append(tr)
    return sum(trs) / period if trs else None

def _log_return(a: float, b: float) -> Optional[float]:
    try:
        if a > 0 and b > 0:
            return math.log(b / a)
    except Exception:
        return None
    return None


def build_ohlcv_sentiment_features(candles: List[Dict[str, Any]], sentiment_agg: Dict[str, Any]) -> Dict[str, Any]:
    """캔들(OHLCV) + 감성 요약을 조합한 파생 피처 산출.

    반환 키 (예시):
      close, ema_12, ema_26, ema_ratio, rsi_14, atr_14,
      log_ret_1, ret_5, ret_10, volume_mean_20, volume_zscore,
      sentiment_avg_60m, sentiment_pos_ratio, sentiment_neg_ratio, sentiment_net
    """
    out: Dict[str, Any] = {}
    if not candles:
        return out
    closes = [float(c.get("close")) for c in candles if c.get("close") is not None]
    highs = [float(c.get("high")) for c in candles if c.get("high") is not None]
    lows = [float(c.get("low")) for c in candles if c.get("low") is not None]
    vols = [float(c.get("volume")) for c in candles if c.get("volume") is not None]

    # Basic present value
    out["close"] = closes[-1] if closes else None

    # EMA fast/slow
    ema_fast = _ema(closes[-(DEFAULT_EMA_FAST*4):], DEFAULT_EMA_FAST) if closes else None
    ema_slow = _ema(closes[-(DEFAULT_EMA_SLOW*4):], DEFAULT_EMA_SLOW) if closes else None
    out["ema_12"] = ema_fast
    out["ema_26"] = ema_slow
    out["ema_ratio"] = (ema_fast / ema_slow) if (ema_fast and ema_slow and ema_slow != 0) else None

    # RSI
    out["rsi_14"] = _rsi(closes, DEFAULT_RSI_PERIOD)

    # ATR
    if len(candles) >= DEFAULT_ATR_PERIOD + 1:
        try:
            out["atr_14"] = _atr(candles, DEFAULT_ATR_PERIOD)
        except Exception:
            out["atr_14"] = None
    else:
        out["atr_14"] = None

    # Returns
    if len(closes) >= 2:
        out["log_ret_1"] = _log_return(closes[-2], closes[-1])
    else:
        out["log_ret_1"] = None
    def pct_ret(h: int) -> Optional[float]:
        if len(closes) > h:
            prev = closes[-1 - h]
            return (closes[-1] - prev) / prev if prev != 0 else None
        return None
    out["ret_5"] = pct_ret(5)
    out["ret_10"] = pct_ret(10)

    # Volume stats
    if len(vols) >= 20:
        last_vol = vols[-1]
        mean20 = sum(vols[-20:]) / 20
        out["volume_mean_20"] = mean20
        # std
        var20 = sum((v - mean20)**2 for v in vols[-20:]) / 20
        std20 = math.sqrt(var20)
        out["volume_zscore"] = ((last_vol - mean20) / std20) if std20 > 0 else None
    else:
        out["volume_mean_20"] = None
        out["volume_zscore"] = None

    # Sentiment aggregation features
    if sentiment_agg:
        avg = sentiment_agg.get("avg")
        pos = sentiment_agg.get("pos", 0)
        neg = sentiment_agg.get("neg", 0)
        cnt = sentiment_agg.get("count", 0)
        out["sentiment_avg_60m"] = avg if isinstance(avg, (int,float)) else None
        out["sentiment_pos_ratio"] = (pos / cnt) if cnt else None
        out["sentiment_neg_ratio"] = (neg / cnt) if cnt else None
        if isinstance(avg, (int,float)):
            out["sentiment_net"] = (pos - neg) / cnt if cnt else None
        else:
            out["sentiment_net"] = None
    return out
