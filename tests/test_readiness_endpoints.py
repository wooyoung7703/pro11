import pytest
from httpx import AsyncClient
from backend.apps.api.main import app

@pytest.mark.asyncio
async def test_liveness_and_readiness():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        live = await ac.get("/health/live")
        assert live.status_code == 200
        assert live.json().get("status") == "alive"
        ready = await ac.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json().get("status") in ("ready", "not_ready")
