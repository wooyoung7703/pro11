import pytest
import httpx
from backend.apps.api.main import app, registry_repo
from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository

import os
API_KEY = os.getenv("API_KEY", "dev-key")

@pytest.mark.asyncio
async def test_production_calibration_endpoint(monkeypatch):
    # Insert a fake model row by monkeypatching fetch_latest
    # Must include self because we're monkeypatching an instance method; otherwise Python
    # passes the repo instance as first arg and causes multiple values for 'limit'.
    async def fake_fetch_latest(self, name: str, model_type: str, limit: int = 5):
        return [
            {
                "id": 1,
                "version": "1234567890",
                "status": "production",
                "artifact_path": "artifacts/models/bottom_predictor__1234567890.json",
                "metrics": {
                    "brier": 0.12,
                    "ece": 0.05,
                    "mce": 0.14,
                    "reliability_bins": [
                        {"bin": i, "count": 10, "mean_prob": 0.05 + i*0.09, "empirical": 0.05 + i*0.085, "abs_diff": 0.005}
                        for i in range(10)
                    ],
                },
            }
        ]

    monkeypatch.setattr(ModelRegistryRepository, "fetch_latest", fake_fetch_latest)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/api/models/production/calibration", headers={"X-API-Key": API_KEY})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        calib = data["calibration"]
        assert calib["brier"] == 0.12
        assert calib["ece"] == 0.05
        assert isinstance(calib["reliability_bins"], list)
        assert len(calib["reliability_bins"]) == 10
