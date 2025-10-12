import pytest
from httpx import AsyncClient
from backend.apps.api.main import app

API_KEY = "dev-key"

@pytest.mark.asyncio
async def test_recent_features_endpoint_auth_and_shape():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Unauthorized first
        r = await ac.get("/api/features/recent")
        assert r.status_code == 401
        # Authorized
        r2 = await ac.get("/api/features/recent", headers={"X-API-Key": API_KEY})
        # 200 even if empty (no data yet)
        assert r2.status_code == 200
        data = r2.json()
        assert isinstance(data, list)
        # If data exists, check expected keys
        if data:
            sample = data[0]
            for key in ["symbol", "interval", "open_time", "close_time"]:
                assert key in sample
