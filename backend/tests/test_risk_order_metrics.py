import pytest
from httpx import AsyncClient, ASGITransport
from backend.apps.api.main import app, risk_engine, RISK_ORDER_EVALS, RISK_ORDER_REJECTS

import os
API_KEY = os.getenv("API_KEY", "dev-key")

@pytest.mark.asyncio
async def test_risk_order_metrics(monkeypatch):
    # Ensure risk engine baseline state
    risk_engine.limits.max_notional = 10000
    # Clear existing positions
    risk_engine.positions.clear()
    # snapshot counters before
    before_allowed = RISK_ORDER_EVALS.labels(allowed="true")._value.get() if ("true",) in RISK_ORDER_EVALS._metrics else 0
    before_rejected = RISK_ORDER_EVALS.labels(allowed="false")._value.get() if ("false",) in RISK_ORDER_EVALS._metrics else 0

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Allowed order (within notional)
        resp = await ac.post("/api/risk/evaluate", params={"symbol":"XRPUSDT","price":0.5,"size":5000}, headers={"X-API-Key":API_KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert data["allowed"] is True
        # Rejected order (exceeds notional)
        resp2 = await ac.post("/api/risk/evaluate", params={"symbol":"XRPUSDT","price":5.0,"size":5000}, headers={"X-API-Key":API_KEY})
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["allowed"] is False
        assert "notional_limit" in data2["reasons"]

    # After metrics
    after_allowed = RISK_ORDER_EVALS.labels(allowed="true")._value.get()
    after_rejected = RISK_ORDER_EVALS.labels(allowed="false")._value.get()
    assert after_allowed == before_allowed + 1
    assert after_rejected == before_rejected + 1

    # Check reject reason counter
    # Access underlying metric family to find the reason label
    # (We can rely on direct label access too)
    reject_reason_val = RISK_ORDER_REJECTS.labels(reason="notional_limit")._value.get()
    assert reject_reason_val >= 1
