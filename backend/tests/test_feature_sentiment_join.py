from __future__ import annotations
import os
import asyncio
import time
import types
import pytest

os.environ.setdefault("FAST_STARTUP", "true")

from backend.apps.features.service.feature_service import FeatureService
from backend.apps.features.service import feature_service as fs_mod


pytestmark = pytest.mark.asyncio


class FakeConn:
    def __init__(self, open_time: int, close_time: int):
        self.open_time = open_time
        self.close_time = close_time
        self.executed = []

    async def fetch(self, sql: str, *args):
        # Return minimal OHLCV candles for FETCH_SQL (DESC ordering expected upstream)
        if "FROM ohlcv_candles" in sql and "ORDER BY open_time DESC" in sql:
            # Provide 3 rows with increasing close prices; order DESC
            rows = [
                (self.open_time + 120000, self.close_time + 120000, 103.0),
                (self.open_time + 60000, self.close_time + 60000, 102.0),
                (self.open_time, self.close_time, 101.0),
            ]
            return rows
        return []

    async def fetchrow(self, sql: str, *args):
        # META_UPSERT_SQL returns an id
        if "INSERT INTO feature_snapshot_meta" in sql:
            return {"id": 42}
        return None

    async def execute(self, sql: str, *args):
        # Capture VALUE_UPSERT_SQL writes
        if "INSERT INTO feature_snapshot_value" in sql:
            snapshot_id, feature_name, feature_value = args
            self.executed.append((feature_name, feature_value))
        return "OK"


class FakeAcquire:
    def __init__(self, conn: FakeConn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, conn: FakeConn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


async def test_sentiment_join_no_leak(monkeypatch):
    # Arrange candle times (epoch ms, minute boundary)
    base_minute = (int(time.time()) // 60) * 60 * 1000
    open_time = base_minute - 60000
    close_time = base_minute

    fake_conn = FakeConn(open_time, close_time)
    fake_pool = FakePool(fake_conn)

    # Monkeypatch DB pool
    from backend.common import db as db_pkg  # just to ensure package import
    monkeypatch.setattr(fs_mod, "init_pool", lambda: asyncio.Future())
    fut = fs_mod.init_pool()
    fut.set_result(fake_pool)

    # Monkeypatch compute_all to avoid dependency on price-based features
    monkeypatch.setattr(fs_mod, "compute_all", lambda prices: {})

    # Prepare sentiment points: one before close_time and one after (should be excluded)
    before_ts = close_time - 30000  # 30s before close
    after_ts = close_time + 30000   # 30s after close (must be excluded)
    rows = [
        {"ts": before_ts, "score_norm": 0.6},
        {"ts": after_ts, "score_norm": -0.9},
    ]

    async def _stub_fetch_range(symbol: str, start_ts_ms: int, end_ts_ms: int, limit: int = 10000):
        # Return both rows; service should filter > close_time out
        return [r for r in rows if start_ts_ms <= r["ts"] <= end_ts_ms or r["ts"] == after_ts]

    # Patch sentiment fetch used inside feature_service
    monkeypatch.setattr(fs_mod, "sentiment_fetch_range", _stub_fetch_range)

    svc = FeatureService(symbol="XRPUSDT", interval="1m", window=3)
    res = await svc.compute_and_store()
    assert isinstance(res, dict)

    # Collect sentiment writes
    writes = {k: v for (k, v) in fake_conn.executed}
    # The score should reflect only the 'before' value (0.6), not the 'after' (-0.9)
    assert "sent_score" in writes
    assert writes["sent_score"] is None or abs(writes["sent_score"] - 0.6) < 1e-6
    # EMA_5m might exist depending on env; check base fields exist
    assert "sent_cnt" in writes and writes["sent_cnt"] >= 1.0
    assert "sent_pos_ratio" in writes and 0.0 <= writes["sent_pos_ratio"] <= 1.0
