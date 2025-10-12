import pytest
from httpx import AsyncClient
from backend.apps.api.main import app

API_KEY = "dev-key"

@pytest.mark.asyncio
async def test_feature_drift_auth_and_shape():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/api/features/drift")
        assert r.status_code == 401
        r2 = await ac.get("/api/features/drift", headers={"X-API-Key": API_KEY})
        assert r2.status_code == 200
        data = r2.json()
        assert "status" in data
        # When insufficient data present we expect that status
        assert data["status"] in ("ok", "insufficient_data", "insufficient_valid_points")
