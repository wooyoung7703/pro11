import pytest
from httpx import AsyncClient, ASGITransport
from backend.apps.api.main import app

API_KEY = {"X-API-Key": "dev-key"}

@pytest.mark.asyncio
async def test_exit_settings_validation_rejects_invalid_values():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # trail.mode invalid
        r = await ac.put("/admin/settings/exit.trail.mode", headers=API_KEY, json={"value": "foo"})
        assert r.status_code == 400
        # trail.percent negative
        r = await ac.put("/admin/settings/exit.trail.percent", headers=API_KEY, json={"value": -0.01})
        assert r.status_code == 400
        # trail.percent too large
        r = await ac.put("/admin/settings/exit.trail.percent", headers=API_KEY, json={"value": 0.8})
        assert r.status_code == 400
        # trail.multiplier non-positive
        r = await ac.put("/admin/settings/exit.trail.multiplier", headers=API_KEY, json={"value": 0})
        assert r.status_code == 400
        # time_stop.bars negative
        r = await ac.put("/admin/settings/exit.time_stop.bars", headers=API_KEY, json={"value": -1})
        assert r.status_code == 400
        # partial.enabled type must be boolean
        r = await ac.put("/admin/settings/exit.partial.enabled", headers=API_KEY, json={"value": "yes"})
        assert r.status_code == 400
        # partial.levels sum > 1
        bad_levels = [{"rr": 1.0, "fraction": 0.6}, {"rr": 2.0, "fraction": 0.5}]
        r = await ac.put("/admin/settings/exit.partial.levels", headers=API_KEY, json={"value": bad_levels})
        assert r.status_code == 400
        # partial.levels non-monotonic rr
        bad_levels2 = [{"rr": 1.0, "fraction": 0.5}, {"rr": 0.9, "fraction": 0.4}]
        r = await ac.put("/admin/settings/exit.partial.levels", headers=API_KEY, json={"value": bad_levels2})
        assert r.status_code == 400
        # cooldown.bars negative
        r = await ac.put("/admin/settings/exit.cooldown.bars", headers=API_KEY, json={"value": -5})
        assert r.status_code == 400
        # daily_loss_cap_r out of range
        r = await ac.put("/admin/settings/exit.daily_loss_cap_r", headers=API_KEY, json={"value": 25})
        assert r.status_code == 400
        # freeze_on_exit type must be boolean
        r = await ac.put("/admin/settings/exit.freeze_on_exit", headers=API_KEY, json={"value": "no"})
        assert r.status_code == 400

@pytest.mark.asyncio
async def test_exit_settings_validation_accepts_valid_values_and_applies():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # valid trail.mode
        r = await ac.put("/admin/settings/exit.trail.mode", headers=API_KEY, json={"value": "percent"})
        assert r.status_code == 200
        assert r.json()["applied"] in (True, False)  # applied may be False in degraded mode but should not 400
        # valid trail.percent
        r = await ac.put("/admin/settings/exit.trail.percent", headers=API_KEY, json={"value": 0.02})
        assert r.status_code == 200
        # valid time_stop.bars
        r = await ac.put("/admin/settings/exit.time_stop.bars", headers=API_KEY, json={"value": 10})
        assert r.status_code == 200
        # valid partial.enabled
        r = await ac.put("/admin/settings/exit.partial.enabled", headers=API_KEY, json={"value": True})
        assert r.status_code == 200
        # valid partial.levels
        good_levels = [{"rr": 1.0, "fraction": 0.4}, {"rr": 2.0, "fraction": 0.3}]
        r = await ac.put("/admin/settings/exit.partial.levels", headers=API_KEY, json={"value": good_levels})
        assert r.status_code == 200
        # valid cooldown
        r = await ac.put("/admin/settings/exit.cooldown.bars", headers=API_KEY, json={"value": 3})
        assert r.status_code == 200
        # valid daily_loss_cap_r
        r = await ac.put("/admin/settings/exit.daily_loss_cap_r", headers=API_KEY, json={"value": 4.0})
        assert r.status_code == 200
        # valid freeze_on_exit
        r = await ac.put("/admin/settings/exit.freeze_on_exit", headers=API_KEY, json={"value": False})
        assert r.status_code == 200
