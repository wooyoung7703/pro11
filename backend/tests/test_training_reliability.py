import pytest
from backend.apps.training.training_service import TrainingService

@pytest.mark.asyncio
async def test_training_reliability_metrics(monkeypatch, tmp_path):
    svc = TrainingService(symbol="XRPUSDT", interval="1m", artifact_dir=str(tmp_path))

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
    async def _register_stub(**kwargs):
        return 1

    monkeypatch.setattr(svc.repo, "register", _register_stub)

    # Mock OHLCV fetch to provide candles aligned with feature rows
    async def fake_fetch_kline_recent(symbol: str, interval: str, limit: int = 1000):
        rows = fake_rows()
        candles = []
        for idx, r in enumerate(rows):
            ct = int(r["close_time"])
            base = 100.0 + (ct % 30)
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
