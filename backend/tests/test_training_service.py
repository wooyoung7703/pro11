import pytest
import numpy as np
from backend.apps.training.training_service import TrainingService

class DummyStorage(TrainingService):
    pass

# Helper to build synthetic feature rows

def make_rows(n: int):
    rows = []
    # We'll create mild oscillating ret_1 so both classes appear
    for i in range(n):
        sign = 1 if i % 2 == 0 else -1
        base = 0.001 * sign
        rows.append({
            "open_time": i,
            "close_time": i+1,
            "ret_1": base,
            "ret_5": base * 0.8,
            "ret_10": base * 1.2,
            "rsi_14": 50 + sign * 5,
            "rolling_vol_20": 0.01 + (i % 5) * 0.0005,
            "ma_20": 0.5 + 0.001 * i,
            "ma_50": 0.5 + 0.0005 * i,
        })
    return rows

@pytest.mark.asyncio
async def test_training_success(monkeypatch, tmp_path):
    svc = TrainingService(symbol="XRPUSDT", interval="1m", artifact_dir=str(tmp_path))

    rows = make_rows(200)

    async def _load_recent_features(limit=1000):
        return rows

    monkeypatch.setattr(svc, "load_recent_features", _load_recent_features)

    async def _register(**kwargs):
        return 1

    monkeypatch.setattr(svc.repo, "register", _register)

    # Mock OHLCV fetch to ensure labeling path has candles
    async def fake_fetch_kline_recent(symbol: str, interval: str, limit: int = 1000):
        candles = []
        for idx, r in enumerate(rows):
            ct = int(r["close_time"])
            base = 100.0 + (ct % 40)
            if idx % 40 == 0:
                low = base * 0.94
                high = base * 1.04
            else:
                low = base * 0.999
                high = base * 1.001
            candles.append({
                "close_time": ct,
                "open": base * 0.9995,
                "high": high,
                "low": low,
                "close": base,
            })
        return list(reversed(candles))

    monkeypatch.setattr(
        "backend.apps.ingestion.repository.ohlcv_repository.fetch_recent",
        fake_fetch_kline_recent,
        raising=True,
    )

    result = await svc.run_training(limit=200)
    assert result["status"] == "ok"
    metrics = result["metrics"]
    assert "auc" in metrics and "accuracy" in metrics
    assert metrics["samples"] > 0

@pytest.mark.asyncio
async def test_training_insufficient(monkeypatch, tmp_path):
    svc = TrainingService(symbol="XRPUSDT", interval="1m", artifact_dir=str(tmp_path))
    rows = make_rows(120)

    async def _load_recent_features_insufficient(limit=1000):
        return rows

    monkeypatch.setattr(svc, "load_recent_features", _load_recent_features_insufficient)
    result = await svc.run_training(limit=120)
    assert result["status"] == "insufficient_data"