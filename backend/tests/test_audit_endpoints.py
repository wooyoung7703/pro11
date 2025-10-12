import os
import pytest
from httpx import AsyncClient, ASGITransport
from backend.apps.api.main import app

API_KEY = os.getenv("API_KEY", "dev-key")

@pytest.mark.asyncio
async def test_retrain_events_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/training/retrain/events", headers={"X-API-Key": API_KEY})
    assert r.status_code == 200
    data = r.json()
    assert "events" in data
    assert "limit" in data

@pytest.mark.asyncio
async def test_promotion_events_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/models/promotion/events", headers={"X-API-Key": API_KEY})
    assert r.status_code == 200
    data = r.json()
    assert "events" in data
    assert "limit" in data
