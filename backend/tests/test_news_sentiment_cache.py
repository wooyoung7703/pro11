import pytest, time
from backend.apps.news.repository.news_repository import NewsRepository

@pytest.mark.asyncio
async def test_news_sentiment_cache_hit_miss(monkeypatch):
    repo = NewsRepository()
    calls = {"count": 0}

    async def fake_init_pool():
        class DummyConn:
            async def fetch(self, *args, **kwargs):
                calls["count"] += 1
                # return few rows with sentiments
                return [
                    {"sentiment": 0.1},
                    {"sentiment": -0.2},
                    {"sentiment": 0.05},
                ]

        class DummyAcquire:
            def __init__(self, conn: DummyConn):
                self._conn = conn

            async def __aenter__(self):
                return self._conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class DummyPool:
            def acquire(self):
                return DummyAcquire(DummyConn())

        return DummyPool()

    monkeypatch.setattr("backend.apps.news.repository.news_repository.init_pool", fake_init_pool)

    # First call: miss -> underlying fetch executed
    r1 = await repo.sentiment_window_stats(minutes=60, symbol="XRPUSDT")
    assert r1["count"] == 3
    assert calls["count"] == 1

    # Second call: should be cache hit (no new fetch)
    r2 = await repo.sentiment_window_stats(minutes=60, symbol="XRPUSDT")
    assert r2 == r1
    assert calls["count"] == 1

    # Expire TTL manually (simulate)
    # Reduce internal ts so it appears old
    key = (60, "XRPUSDT")
    entry = repo._sent_cache.get(key)
    assert entry is not None
    entry["ts"] = entry["ts"] - (repo._sent_cache_ttl_sec + 1)

    r3 = await repo.sentiment_window_stats(minutes=60, symbol="XRPUSDT")
    assert calls["count"] == 2  # fetch again
    assert r3 == r1

@pytest.mark.asyncio
async def test_artifact_checksum_mismatch(tmp_path, monkeypatch):
    from backend.apps.model_registry.service.model_storage import LocalModelStorage

    storage = LocalModelStorage(base_dir=str(tmp_path))
    payload = {"sk_model": {"dummy": 1}, "metrics": {"auc": 0.7}}
    path = storage.save("demo_model", "v1", payload)

    # Load baseline (should be ok)
    d1 = storage.load("demo_model", "v1")
    assert d1 is not None
    assert d1.get("checksum_mismatch") in (None, False)

    # Corrupt file by editing JSON (change metrics value so checksum invalid)
    import json
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Modify metrics
    data["metrics"]["auc"] = 0.9
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

    d2 = storage.load("demo_model", "v1")
    assert d2 is not None
    assert d2.get("checksum_mismatch") is True
