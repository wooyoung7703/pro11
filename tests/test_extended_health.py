import pytest
from httpx import AsyncClient
from backend.apps.api.main import app

@pytest.mark.asyncio
async def test_extended_health_keys():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r = await ac.get("/health/extended")
        assert r.status_code == 200
        data = r.json()
        for key in ["status", "environment", "db", "feature_scheduler", "ingestion", "thresholds", "timestamp"]:
            assert key in data
        assert "ok" in data["status"] or data["status"] in ("degraded", "error")
        assert "feature_lag_sec" in data["thresholds"]
        assert "ingestion_lag_sec" in data["thresholds"]
