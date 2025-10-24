import pytest
from httpx import AsyncClient, ASGITransport
from backend.apps.api.main import app

@pytest.mark.asyncio
async def test_soft_defaults_exist_for_ml_and_training():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # ML cooldown soft default
        r1 = await ac.get("/admin/settings/ml.signal.cooldown_sec", headers={"X-API-Key":"dev-key"})
        assert r1.status_code == 200
        v1 = r1.json()["item"]["value"]
        assert isinstance(v1, (int, float))
        assert v1 >= 0
        # training.min_labels
        r2 = await ac.get("/admin/settings/training.bottom.min_labels", headers={"X-API-Key":"dev-key"})
        assert r2.status_code == 200
        assert isinstance(r2.json()["item"]["value"], int)
        # training.min_train_labels
        r3 = await ac.get("/admin/settings/training.bottom.min_train_labels", headers={"X-API-Key":"dev-key"})
        assert r3.status_code == 200
        assert isinstance(r3.json()["item"]["value"], int)
        # training.ohlcv_fetch_cap
        r4 = await ac.get("/admin/settings/training.bottom.ohlcv_fetch_cap", headers={"X-API-Key":"dev-key"})
        assert r4.status_code == 200
        assert isinstance(r4.json()["item"]["value"], int)
    # ui.calibration.poll_interval_sec (UI soft default)
    r5 = await ac.get("/admin/settings/ui.calibration.poll_interval_sec", headers={"X-API-Key":"dev-key"})
    assert r5.status_code == 200
    assert isinstance(r5.json()["item"]["value"], int)
