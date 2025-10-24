import pytest
import time
from httpx import AsyncClient, ASGITransport
from backend.apps.api.main import app

@pytest.mark.asyncio
async def test_ml_cooldown_reset_clears_map():
    # Arrange: set a fake last-emit timestamp in the module-level map for a test symbol
    symbol = "TESTCOINUSDT"
    from backend.apps.trading.service import ml_signal_service as _mls
    _mls._LAST_EMIT_TS[symbol] = time.time()
    assert symbol in _mls._LAST_EMIT_TS

    # Act: call reset endpoint with the same symbol
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(f"/admin/ml/cooldown/reset", params={"symbol": symbol}, headers={"X-API-Key":"dev-key"})
        assert r.status_code == 200
        data = r.json()
        assert data.get("status") == "ok"
        assert data.get("cleared") == symbol

    # Assert: entry is removed
    assert _mls._LAST_EMIT_TS.get(symbol) is None
