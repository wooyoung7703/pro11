import pytest
from backend.apps.training.training_service import TrainingService

@pytest.mark.asyncio
async def test_training_reliability_metrics(monkeypatch):
    svc = TrainingService(symbol="XRPUSDT", interval="1m", artifact_dir="artifacts/models")

    def fake_rows():
        rows = []
        for i in range(250):
            rows.append({
                "open_time": i,
                "close_time": i+1,
                "ret_1": 0.001 * ((-1)**i),
                "ret_5": 0.002 + (i % 3) * 0.0001,
                "ret_10": 0.003 + (i % 5) * 0.0001,
                "rsi_14": 45 + (i % 20),
                "rolling_vol_20": 0.01 + (i % 10) * 0.0005,
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
    assert "reliability_bins" in metrics
    bins = metrics["reliability_bins"]
    assert len(bins) == 10
    # Each bin dict keys
    for b in bins:
        assert set(b.keys()) == {"bin","count","mean_prob","empirical","abs_diff"}
    # ECE range
    ece = metrics.get("ece")
    assert ece == ece
    assert 0 <= ece <= 1
    mce = metrics.get("mce")
    assert mce == mce
    assert 0 <= mce <= 1
