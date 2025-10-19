import pytest
from backend.apps.api.main import app
from httpx import AsyncClient, ASGITransport

@pytest.mark.asyncio
async def test_production_metrics_no_models(monkeypatch):
    from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository
    async def _fetch_latest(self, name, model_type, limit):
        return []
    monkeypatch.setattr(ModelRegistryRepository, 'fetch_latest', _fetch_latest)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/models/production/metrics", headers={"X-API-Key":"dev-key"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "no_models"

@pytest.mark.asyncio
async def test_production_metrics_with_prod(monkeypatch, tmp_path):
    from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository
    class FakeRecord(dict):
        def __getattr__(self, k):
            return self[k]
    async def _fetch_latest(self, name, model_type, limit):
        rec = FakeRecord({
            "id": 42,
            "name": name,
            "version": "1234567890",
            "model_type": model_type,
            "status": "production",
            "artifact_path": str(tmp_path/"bottom_predictor__1234567890.json"),
            "metrics": {"auc":0.61, "accuracy":0.55, "samples":800}
        })
        return [rec]
    monkeypatch.setattr(ModelRegistryRepository, 'fetch_latest', _fetch_latest)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/models/production/metrics", headers={"X-API-Key":"dev-key"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["model"]["metrics"]["auc"] == 0.61
