import pytest
import asyncio
from httpx import AsyncClient
from backend.apps.api.main import app, API_KEY
from backend.common.db.connection import init_pool

@pytest.mark.asyncio
async def test_manual_labeler_run(monkeypatch):
    # If auto labeler disabled in config, skip
    from backend.common.config.base_config import load_config
    cfg = load_config()
    if not cfg.auto_labeler_enabled:
        pytest.skip("auto labeler disabled in config")
    await init_pool()
    async with AsyncClient(app=app, base_url="http://test") as ac:
        resp = await ac.post("/api/inference/labeler/run", headers={"X-API-Key": API_KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") in ("ok","no_candidates","pending","error")
