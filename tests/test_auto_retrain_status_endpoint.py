import pytest
from httpx import AsyncClient
from backend.apps.api.main import app, API_KEY

@pytest.mark.asyncio
async def test_auto_retrain_status(auth_header=None):
    headers = {"X-API-Key": API_KEY}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        resp = await ac.get("/api/training/auto/status", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "enabled" in body
    assert "config" in body
