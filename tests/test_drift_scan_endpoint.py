import pytest
from httpx import AsyncClient
from backend.apps.api.main import app, API_KEY

@pytest.mark.asyncio
async def test_drift_scan_endpoint():
    headers = {"X-API-Key": API_KEY}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        resp = await ac.get("/api/features/drift/scan", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    if body["status"] == "ok":
        assert "results" in body
