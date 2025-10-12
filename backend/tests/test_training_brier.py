import asyncio
import numpy as np
import pytest
from backend.apps.training.training_service import TrainingService

@pytest.mark.asyncio
async def test_training_includes_brier(monkeypatch):
    svc = TrainingService(symbol="XRPUSDT", interval="1m", artifact_dir="artifacts/models")

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

    result = await svc.run_training()
    assert result["status"] == "ok"
    metrics = result["metrics"]
    assert "brier" in metrics
    brier = metrics["brier"]
    assert brier == brier  # not NaN
    assert 0 <= brier <= 1  # probability score range
