import pytest
from httpx import AsyncClient
from backend.apps.api.main import app, API_KEY


@pytest.mark.asyncio
async def test_auth_rejects_without_key():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.post("/api/features/compute")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_auth_accepts_with_key(monkeypatch):
    async with AsyncClient(app=app, base_url="http://test", headers={"X-API-Key": API_KEY}) as ac:
        r = await ac.post("/api/features/compute")
        # May return no_data if DB empty, just ensure authorized path taken (not 401)
        assert r.status_code != 401