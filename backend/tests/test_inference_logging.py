import pytest
from httpx import AsyncClient, ASGITransport
from backend.apps.api.main import app

@pytest.mark.asyncio
async def test_inference_logs_endpoint(monkeypatch):
    from backend.apps.training.repository.inference_log_repository import InferenceLogRepository
    class FakeRecord(dict):
        def __getattr__(self, k):
            return self[k]
    async def _fetch_recent(self, limit=100, offset=0):
        return [FakeRecord({
            "id":1,
            "created_at":"2025-09-29T00:00:00Z",
            "symbol":"XRPUSDT",
            "interval":"1m",
            "model_name":"bottom_predictor",
            "model_version":"123",
            "probability":0.75,
            "decision":1,
            "threshold":0.5,
            "production":True
        })]
    monkeypatch.setattr(InferenceLogRepository, 'fetch_recent', _fetch_recent)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/inference/logs", headers={"X-API-Key":"dev-key"})
        assert r.status_code == 200
        data = r.json()
        assert data["logs"][0]["probability"] == 0.75

@pytest.mark.asyncio
async def test_inference_histogram_endpoint(monkeypatch):
    from backend.apps.training.repository.inference_log_repository import InferenceLogRepository
    async def _probability_histogram(self, window_seconds=3600):
        return [
            {"bucket":0, "count":1, "le":0.0},
            {"bucket":5, "count":2, "le":0.5},
            {"bucket":10, "count":0, "le":1.0},
        ]
    monkeypatch.setattr(InferenceLogRepository, 'probability_histogram', _probability_histogram)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/inference/probability/histogram", headers={"X-API-Key":"dev-key"})
        assert r.status_code == 200
        data = r.json()
        assert data["buckets"][1]["count"] == 2
