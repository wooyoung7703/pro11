import pytest
from httpx import AsyncClient
from fastapi import status
from backend.apps.api.main import app, API_KEY

@pytest.mark.asyncio
async def test_training_run_insufficient(monkeypatch):
    # Force insufficient data by mocking TrainingService.load_recent_features
    from backend.apps.training import training_service as ts_mod
    class DummyTS(ts_mod.TrainingService):
        async def load_recent_features(self, limit: int = 1000):  # type: ignore
            return []
    monkeypatch.setattr(ts_mod, 'TrainingService', DummyTS)
    headers = {"X-API-Key": API_KEY}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        resp = await ac.post("/api/training/run", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "insufficient_data"

@pytest.mark.asyncio
async def test_inference_predict(monkeypatch):
    from backend.apps.training import training_service as ts_mod
    class DummyTS(ts_mod.TrainingService):
        async def load_recent_features(self, limit: int = 1000):  # type: ignore
            return [{"ret_1": 0.5}]
    monkeypatch.setattr(ts_mod, 'TrainingService', DummyTS)
    headers = {"X-API-Key": API_KEY}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        resp = await ac.get("/api/inference/predict", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["prediction"] == 1
