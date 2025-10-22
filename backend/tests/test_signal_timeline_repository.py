import asyncio
import json
import pytest

from backend.apps.trading.repository.signal_timeline_repository import SignalTimelineRepository


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.last_sql = None
        self.last_args = None

    async def fetch(self, sql, *args):  # noqa: D401
        self.last_sql = sql
        self.last_args = args
        # Simulate asyncpg returning list of dict-like records
        return self._rows


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _AcquireCtx(self._conn)


@pytest.mark.asyncio
async def test_fetch_timeline_maps_and_filters(monkeypatch):
    # Prepare mixed rows from three sources with details as JSON string and dict
    fake_rows = [
        {
            "id": 10,
            "ts": 1000.0,
            "source": "trading",
            "event": "created",
            "signal_type": "bottom",
            "symbol": None,
            "details": json.dumps({"id": 10, "status": "created"}),
        },
        {
            "id": 11,
            "ts": 900.0,
            "source": "autopilot",
            "event": "signal",
            "signal_type": "bottom",
            "symbol": "XRPUSDT",
            "details": {"signal": {"kind": "bottom", "symbol": "XRPUSDT"}},
        },
    ]
    conn = _FakeConn(fake_rows)
    pool = _FakePool(conn)

    async def _fake_init_pool():
        return pool

    monkeypatch.setattr(
        "backend.apps.trading.repository.signal_timeline_repository.init_pool", _fake_init_pool
    )

    repo = SignalTimelineRepository()
    out = await repo.fetch_timeline(limit=2, from_ts=800.0, to_ts=1100.0, signal_type="bottom")

    # Ensure SQL was called with our filters translated properly
    assert conn.last_args == (800.0, 1100.0, "bottom", 2)

    # Ensure mapping and JSON coercion works
    assert isinstance(out, list) and len(out) == 2
    assert out[0]["id"] == 10
    assert isinstance(out[0]["details"], dict) and out[0]["details"]["id"] == 10
    assert out[1]["symbol"] == "XRPUSDT"
