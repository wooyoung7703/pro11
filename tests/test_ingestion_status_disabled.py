import pytest
from httpx import AsyncClient
from backend.apps.api.main import app

@pytest.mark.asyncio
async def test_ingestion_status_disabled():
    # Assuming default config has ingestion disabled (INGESTION_ENABLED not set)
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/api/ingestion/status")
        assert r.status_code == 200
        data = r.json()
        assert data.get("enabled") is False
