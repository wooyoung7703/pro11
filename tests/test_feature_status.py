import asyncio
import pytest
from httpx import AsyncClient
from backend.apps.api.main import app

@pytest.mark.asyncio
async def test_feature_status_endpoint():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Give scheduler a brief moment to possibly run once
        await asyncio.sleep(0.1)
        resp = await ac.get("/api/features/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data and data["running"] is True
        assert data["symbol"] == "XRPUSDT"
        assert "interval" in data
        assert "next_run_interval_seconds" in data
        # lock_held may be True or False depending on timing
        assert "lock_held" in data
