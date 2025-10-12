import pytest
from httpx import AsyncClient
from backend.apps.api.main import app

API_KEY = "dev-key"

@pytest.mark.asyncio
async def test_export_features_requires_auth_and_csv():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/api/features/export")
        assert r.status_code == 401
        r2 = await ac.get("/api/features/export", headers={"X-API-Key": API_KEY})
        assert r2.status_code == 200
        assert r2.headers.get("content-type", "").startswith("text/csv")
        # Header line present
        first_line = r2.text.splitlines()[0]
        assert "symbol" in first_line and "open_time" in first_line
