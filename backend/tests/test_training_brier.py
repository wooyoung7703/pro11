import asyncio
import numpy as np
import pytest
from backend.apps.training.training_service import TrainingService

@pytest.mark.asyncio
async def test_training_includes_brier(monkeypatch, tmp_path):
    svc = TrainingService(symbol="XRPUSDT", interval="1m", artifact_dir=str(tmp_path))

    # Mock load_recent_features to return synthetic data sufficient for training
    def fake_rows():
        rows = []
        # create 200 rows with engineered features and ret_1 alternating sign
        for i in range(201):
            rows.append({
                "open_time": i,
                "close_time": i+1,
                "ret_1": 0.001 * ((-1)**i),
                "ret_5": 0.002,
                "ret_10": 0.003,
                "rsi_14": 50 + (i % 10),
                "rolling_vol_20": 0.01 + (i % 5) * 0.001,
                "ma_20": 0.5,
                "ma_50": 0.5,
            })
        return rows

    async def mock_load_recent_features(limit: int = 1000):
        return fake_rows()

    monkeypatch.setattr(svc, "load_recent_features", mock_load_recent_features)
    async def _register_stub(**kwargs):
        return 1

    monkeypatch.setattr(svc.repo, "register", _register_stub)

    # Mock OHLCV fetch to avoid no_ohlcv path; align candles with fake_rows close_time
    async def fake_fetch_kline_recent(symbol: str, interval: str, limit: int = 1000):
        rows = fake_rows()
        candles = []
        for idx, r in enumerate(rows):
            ct = int(r["close_time"])
            base = 100.0 + (ct % 50)  # arbitrary base price
            # Create sparse deep-drops every 40 bars to ensure mixed labels within lookahead=30
            if idx % 40 == 0:
                low = base * 0.94
                high = base * 1.04
            else:
                low = base * 0.999  # shallow drop, below threshold
                high = base * 1.001  # shallow rebound
            candles.append({
                "close_time": ct,
                "open": base * 0.9995,
                "high": high,
                "low": low,
                "close": base,
            })
        # Repository returns DESC order; training reverses to chrono
        return list(reversed(candles))

    monkeypatch.setattr(
        "backend.apps.ingestion.repository.ohlcv_repository.fetch_recent",
        fake_fetch_kline_recent,
        raising=True,
    )

    result = await svc.run_training()
    assert result["status"] == "ok"
    metrics = result["metrics"]
    assert "brier" in metrics
    brier = metrics["brier"]
    assert brier == brier  # not NaN
    assert 0 <= brier <= 1  # probability score range
