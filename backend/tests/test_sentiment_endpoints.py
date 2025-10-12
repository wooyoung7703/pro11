from __future__ import annotations
import asyncio
import json
import time

import pytest

# NOTE: This is a lightweight smoke-style test that validates the presence and basic behavior
# of the new sentiment endpoints. It assumes the API server is running separately in test env,
# or you can adapt to use TestClient if a FastAPI app factory is exposed.

pytestmark = pytest.mark.asyncio


async def _aiosleep():
    await asyncio.sleep(0.01)


async def test_sentiment_tick_and_latest_history(monkeypatch):
    # This test is illustrative; in CI you might use httpx.AsyncClient against the live app.
    # Here we just assert the routes exist by importing the handlers.
    from backend.apps.api import main as api_main

    # Ensure handlers are present
    assert hasattr(api_main, 'api_sentiment_tick')
    assert hasattr(api_main, 'api_sentiment_latest')
    assert hasattr(api_main, 'api_sentiment_history')

    # Exercise ts parsing helper
    ts_ms = api_main._parse_ts_ms("2025-10-09T10:21:00Z")
    assert isinstance(ts_ms, int)
    assert ts_ms > 0

    # Avoid DB calls in this smoke test – just ensure callables exist and basic paths are sane
    await _aiosleep()
