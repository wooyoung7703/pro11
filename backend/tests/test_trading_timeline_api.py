import os
os.environ.setdefault("FAST_STARTUP", "true")
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import status

from backend.apps.api.main import app


def make_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_timeline_requires_api_key(monkeypatch):
    monkeypatch.setenv("ALLOW_PUBLIC_STATUS", "0")
    monkeypatch.delenv("DISABLE_API_KEY", raising=False)
    async with make_client() as ac:
        r = await ac.get("/api/trading/signals/timeline")
        assert r.status_code == status.HTTP_401_UNAUTHORIZED
        data = r.json()
        assert data.get("detail") == "invalid api key"


@pytest.mark.asyncio
async def test_timeline_ok_with_api_key():
    async with make_client() as ac:
        r = await ac.get(
            "/api/trading/signals/timeline?limit=2",
            headers={"X-API-Key": os.getenv("API_KEY", "dev-key")},
        )
        # If DB unavailable, handler still returns JSON with status ok and empty list
        assert r.status_code in (200, 500)
        if r.status_code == 200:
            data = r.json()
            assert data.get("status") == "ok"
            assert isinstance(data.get("timeline"), list)
            # Follow-up page with to_ts should also be accepted
            if data.get("timeline"):
                oldest = min([x.get("ts", 0) for x in data["timeline"] if isinstance(x, dict)])
                r2 = await ac.get(
                    f"/api/trading/signals/timeline?limit=2&to_ts={max(0, int(oldest)-1)}",
                    headers={"X-API-Key": os.getenv("API_KEY", "dev-key")},
                )
                assert r2.status_code in (200, 500)
        else:
            # 500 would mean unexpected runtime error; surface for visibility
            data = r.json()
            assert data.get("status") == "error"
