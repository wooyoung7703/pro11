from __future__ import annotations
import os
import asyncio
import time
import pytest
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("FAST_STARTUP", "true")

from backend.apps.api.main import app
from backend.apps import api as api_pkg  # noqa: F401
from backend.apps.api import main as api_main


def make_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


pytestmark = pytest.mark.asyncio


@pytest.fixture
def api_key_header():
    return {"X-API-Key": os.getenv("API_KEY", "dev-key")}


async def test_post_tick_and_latest_history_happy(monkeypatch, api_key_header):
    # Stub insert_tick to avoid DB
    async def _stub_insert_tick(**kwargs):
        return 123

    # Generate synthetic sentiment ticks for last 90 minutes
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 90 * 60 * 1000
    rows = []
    for i in range(90):
        ts = start_ms + i * 60 * 1000
        rows.append({"ts": ts, "score_norm": ((i % 10) - 5) / 5.0})

    async def _stub_fetch_range(symbol: str, start_ts_ms: int, end_ts_ms: int, limit: int = 10000):
        return [r for r in rows if start_ts_ms <= r["ts"] <= end_ts_ms][:limit]

    monkeypatch.setattr(api_main, "sentiment_insert_tick", _stub_insert_tick)
    monkeypatch.setattr(api_main, "sentiment_fetch_range", _stub_fetch_range)

    async with make_client() as ac:
        # POST tick (just to exercise path)
        resp = await ac.post(
            "/api/sentiment/tick",
            headers=api_key_header,
            json={
                "symbol": "XRPUSDT",
                "ts": now_ms - 1000,
                "provider": "test",
                "count": 1,
                "score_raw": 0.1,
                "score_norm": 0.1,
                "meta": {"source": "unit"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["id"] == 123

        # GET latest
        resp = await ac.get(
            "/api/sentiment/latest",
            headers=api_key_header,
            params={"symbol": "XRPUSDT", "windows": "5,15,60"},
        )
        assert resp.status_code == 200
        latest = resp.json()
        assert latest["status"] == "ok"
        assert "ema" in latest and "5m" in latest["ema"]
        assert "score" in latest and isinstance(latest["score"], (int, float))

        # GET history
        resp = await ac.get(
            "/api/sentiment/history",
            headers=api_key_header,
            params={
                "symbol": "XRPUSDT",
                "start": start_ms,
                "end": now_ms,
                "step": "1m",
                "windows": "5,15",
                "fields": "t,score,count,pos_ratio,ema_5,ema_15",
            },
        )
        assert resp.status_code == 200
        hist = resp.json()
        assert hist["status"] == "ok"
        assert len(hist["items"]) > 0
        row = hist["items"][0]
        for k in ("t", "score", "count", "pos_ratio"):
            assert k in row
