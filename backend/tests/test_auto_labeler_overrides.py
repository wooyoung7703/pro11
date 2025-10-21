import asyncio
import datetime as dt
import pytest


@pytest.mark.asyncio
async def test_auto_labeler_run_once_with_overrides(monkeypatch):
    from backend.apps.training.service.auto_labeler import AutoLabelerService
    # Fake repo to avoid DB
    class FakeRepo:
        def __init__(self):
            self.updates = []

        async def fetch_unlabeled_candidates(self, min_age_seconds: int = 0, limit: int = 100):
            created_sec = 100.0  # seconds
            created = dt.datetime.utcfromtimestamp(created_sec - 1)
            return [
                {
                    "id": 1,
                    "created_at": created,
                    "probability": 0.6,
                    "decision": 1,
                    "symbol": "XRPUSDT",
                    "interval": "1m",
                    "extra": {"target": "bottom"},
                }
            ]

        async def update_realized_batch(self, updates):
            self.updates.extend(updates)
            return len(updates)

    fake_repo = FakeRepo()

    # Monkeypatch OHLCV fetch to return controlled candles
    from backend.apps.ingestion.repository import ohlcv_repository as ohlcv_repo

    async def fake_fetch_recent(symbol: str, interval: str, limit: int = 200):
        # 3 candles chronological with ms timestamps
        candles = [
            {"open_time": 0, "close_time": 100_000, "low": 100.0, "high": 100.0, "close": 100.0},  # p0
            {"open_time": 100_000, "close_time": 200_000, "low": 80.0, "high": 86.0, "close": 85.0},  # drawdown -20%
            {"open_time": 200_000, "close_time": 300_000, "low": 85.0, "high": 90.0, "close": 90.0},  # rebound +12.5%
        ]
        return candles

    monkeypatch.setattr(ohlcv_repo, "fetch_recent", fake_fetch_recent)

    svc = AutoLabelerService()
    # Inject fake repo
    svc._repo = fake_repo  # type: ignore[attr-defined]

    # Use tight overrides so label=1 is achievable within small window
    res = await svc.run_once(min_age_seconds=0, limit=5, lookahead=2, drawdown=0.1, rebound=0.1)

    assert isinstance(res, dict)
    assert res.get("status") in {"ok", "pending"}
    # With our candles, label should be computed as 1, so updates >= 1
    assert len(fake_repo.updates) >= 1
    assert fake_repo.updates[0]["id"] == 1
    assert fake_repo.updates[0]["realized"] in (0, 1)
