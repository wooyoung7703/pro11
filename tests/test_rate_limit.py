import pytest
from httpx import AsyncClient
from backend.apps.api.main import app, API_KEY, cfg, RATE_LIMIT_REQUESTS, _rate_state  # type: ignore

@pytest.mark.asyncio
async def test_inference_rate_limit(monkeypatch):
    # Lower inference limits for test determinism
    monkeypatch.setattr(cfg, 'rate_limit_enabled', True)
    monkeypatch.setattr(cfg, 'rate_limit_inference_rps', 1.0)  # 1 req/sec
    monkeypatch.setattr(cfg, 'rate_limit_inference_burst', 2)
    # Reset bucket state
    _rate_state.clear()
    headers = {"X-API-Key": API_KEY}
    blocked = 0
    allowed = 0
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Two quick calls should pass (burst=2), third should block
        for i in range(3):
            resp = await ac.get("/api/inference/predict", headers=headers)
            if i < 2:
                assert resp.status_code == 200, resp.text
                allowed += 1
            else:
                assert resp.status_code == 429, resp.text
                blocked += 1
    assert allowed == 2 and blocked == 1

@pytest.mark.asyncio
async def test_training_rate_limit(monkeypatch):
    monkeypatch.setattr(cfg, 'rate_limit_enabled', True)
    monkeypatch.setattr(cfg, 'rate_limit_training_rps', 0.5)  # allow one every 2s
    monkeypatch.setattr(cfg, 'rate_limit_training_burst', 1)
    _rate_state.clear()
    headers = {"X-API-Key": API_KEY}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        resp1 = await ac.post("/api/training/run", headers=headers)
        # First call allowed (may be insufficient_data or ok, status code 200)
        assert resp1.status_code == 200
        resp2 = await ac.post("/api/training/run", headers=headers)
        assert resp2.status_code == 429, resp2.text

@pytest.mark.asyncio
async def test_rate_limit_metrics_exposed(monkeypatch):
    monkeypatch.setattr(cfg, 'rate_limit_enabled', True)
    monkeypatch.setattr(cfg, 'rate_limit_inference_rps', 2.0)
    monkeypatch.setattr(cfg, 'rate_limit_inference_burst', 1)
    _rate_state.clear()
    headers = {"X-API-Key": API_KEY}
    async with AsyncClient(app=app, base_url="http://test") as ac:
        await ac.get("/api/inference/predict", headers=headers)  # allowed
        await ac.get("/api/inference/predict", headers=headers)  # likely blocked due to burst=1 and immediate retry
        m = await ac.get("/metrics")
    assert m.status_code == 200
    text = m.text
    assert 'rate_limit_requests_total' in text
    # we expect at least one allow or block label line
    assert 'rate_limit_requests_total{bucket="inference",action="allow"' in text or 'rate_limit_requests_total{bucket="inference",action="block"' in text
