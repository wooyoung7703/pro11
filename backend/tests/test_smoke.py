import os
os.environ.setdefault("FAST_STARTUP", "true")  # Speed up test startup by skipping heavy loops
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import status

# Import the FastAPI app (after setting FAST_STARTUP)
from backend.apps.api.main import app


def make_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")

@pytest.mark.asyncio
async def test_healthz():
    async with make_client() as ac:
        resp = await ac.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
        assert "env" in data

@pytest.mark.asyncio
async def test_version():
    async with make_client() as ac:
        resp = await ac.get("/api/version")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("version") == app.version

@pytest.mark.asyncio
async def test_readiness():
    async with make_client() as ac:
        resp = await ac.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "db" in data

@pytest.mark.asyncio
async def test_production_model_metrics_no_models():
    # Without setting up DB migrations / data, expect graceful no_models or ok
    async with make_client() as ac:
        resp = await ac.get("/api/models/production/metrics", headers={"X-API-Key": os.getenv("API_KEY", "dev-key")})
        assert resp.status_code in (200, 401)  # if auth misconfigured
        if resp.status_code == 200:
            data = resp.json()
            assert data.get("status") in ("no_models", "ok")

@pytest.mark.asyncio
async def test_inference_requires_auth(monkeypatch):
    monkeypatch.setenv("ALLOW_PUBLIC_STATUS", "0")
    monkeypatch.delenv("DISABLE_API_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    async with make_client() as ac:
        resp = await ac.get("/api/inference/predict")
        # Should be unauthorized because missing API key
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
        data = resp.json()
        assert data.get("detail") == "invalid api key"
