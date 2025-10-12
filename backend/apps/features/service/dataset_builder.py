from __future__ import annotations
from typing import List, Dict, Any, Optional
from .feature_ohlcv_sentiment import build_ohlcv_sentiment_features

"""
Dataset Builder
---------------
목표: 최근 OHLCV 캔들와 대응 감성 집계를 이용해 (features, label) 샘플 시퀀스를 구성.
라벨 정의: horizon (기본=5) 만큼 미래 close 기준 수익률 ( (future_close - current_close)/current_close ) > 0 → 1 else 0

함수:
  build_samples(candles, sentiment_windows, horizon=5) -> List[Dict]
    candles: 시간 오름차순 리스트
    sentiment_windows: 각 캔들 close_time 키로 매핑된 감성 집계 dict (필요 시 최근값 fallback)

출력 각 행 구조 예:
  {
    'open_time': ..., 'close_time': ..., 'label': 0|1,
    'features': { ... build_ohlcv_sentiment_features output ... }
  }

미래 horizon 초과 구간(마지막 horizon 캔들) 은 label 계산 불가로 제외.
"""

def _select_sentiment(sentiment_windows: Dict[int, Dict[str, Any]], close_time: int) -> Dict[str, Any]:
    if not sentiment_windows:
        return {}
    if close_time in sentiment_windows:
        return sentiment_windows[close_time]
    # fallback: 가장 근접 과거 close_time 선택
    past_keys = [k for k in sentiment_windows.keys() if k <= close_time]
    if not past_keys:
        return {}
    nearest = max(past_keys)
    return sentiment_windows.get(nearest, {})


def build_samples(candles: List[Dict[str, Any]], sentiment_windows: Dict[int, Dict[str, Any]], horizon: int = 5,
                  min_candles_for_features: int = 60) -> List[Dict[str, Any]]:
    if horizon <= 0:
        raise ValueError("horizon must be positive")
    if len(candles) < min_candles_for_features + horizon:
        return []
    samples: List[Dict[str, Any]] = []
    # 슬라이딩 인덱스: i 위치 캔들을 기준으로 horizon 뒤 close 참조
    for i in range(min_candles_for_features, len(candles) - horizon):
        window = candles[:i+1]
        base_candle = window[-1]
        future_candle = candles[i + horizon]
        base_close = float(base_candle["close"]) if base_candle.get("close") is not None else None
        future_close = float(future_candle["close"]) if future_candle.get("close") is not None else None
        if not base_close or not future_close:
            continue
        ret = (future_close - base_close) / base_close if base_close != 0 else 0.0
        label = 1 if ret > 0 else 0
        sentiment_agg = _select_sentiment(sentiment_windows, base_candle.get("close_time"))
        feats = build_ohlcv_sentiment_features(window, sentiment_agg)
        samples.append({
            "open_time": base_candle.get("open_time"),
            "close_time": base_candle.get("close_time"),
            "label": label,
            "horizon_return": ret,
            **feats
        })
    return samples
