import pytest
from httpx import AsyncClient, ASGITransport
from backend.apps.api.main import app

@pytest.mark.asyncio
async def test_exit_settings_soft_defaults():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = {"X-API-Key": "dev-key"}
        # enable_new_policy flag
        r = await ac.get("/admin/settings/exit.enable_new_policy", headers=headers)
        assert r.status_code == 200
        v = r.json()["item"]["value"]
        assert isinstance(v, bool)
        # trail.mode
        r = await ac.get("/admin/settings/exit.trail.mode", headers=headers)
        assert r.status_code == 200
        v = r.json()["item"]["value"]
        assert v in ("atr", "percent")
        # trail.percent
        r = await ac.get("/admin/settings/exit.trail.percent", headers=headers)
        assert r.status_code == 200
        v = r.json()["item"]["value"]
        assert isinstance(v, float)
        assert 0 <= v <= 0.5
        # time_stop.bars
        r = await ac.get("/admin/settings/exit.time_stop.bars", headers=headers)
        assert r.status_code == 200
        v = r.json()["item"]["value"]
        assert isinstance(v, int)
        assert v >= 0
        # partial.enabled
        r = await ac.get("/admin/settings/exit.partial.enabled", headers=headers)
        assert r.status_code == 200
        v = r.json()["item"]["value"]
        assert isinstance(v, bool)
        # cooldown.bars
        r = await ac.get("/admin/settings/exit.cooldown.bars", headers=headers)
        assert r.status_code == 200
        v = r.json()["item"]["value"]
        assert isinstance(v, int)
        assert v >= 0
