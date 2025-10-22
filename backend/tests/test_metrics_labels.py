import pytest
from httpx import AsyncClient, ASGITransport

from backend.apps.api.main import app


def make_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_metrics_exposes_eager_label_counters():
    async with make_client() as ac:
        r = await ac.get("/metrics")
        assert r.status_code == 200
        text = r.text
        assert "inference_calibration_eager_label_runs_total" in text
        # labeled variant should be present even if zero-initialized
        assert "inference_calibration_eager_label_runs_by_label_total" in text
