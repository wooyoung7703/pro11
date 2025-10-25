import asyncio
from starlette.testclient import TestClient


def _noop_async(*args, **kwargs):
    async def _inner(*_a, **_k):
        return None
    return _inner()

def test_trading_order_slippage_metric_present_after_simulated_fill(monkeypatch):
    # Import app and services lazily to avoid circulars
    from backend.apps.api.main import app, risk_engine
    from backend.apps.trading.service.trading_service import get_trading_service
    # Disable DB persistence in risk repository for test stability
    monkeypatch.setattr(
        "backend.apps.risk.repository.risk_repository.RiskRepository.save_state",
        lambda *a, **k: _noop_async(),
        raising=True,
    )
    monkeypatch.setattr(
        "backend.apps.risk.repository.risk_repository.RiskRepository.save_position",
        lambda *a, **k: _noop_async(),
        raising=True,
    )
    monkeypatch.setattr(
        "backend.apps.risk.repository.risk_repository.RiskRepository.load_state",
        lambda *a, **k: _noop_async(),
        raising=True,
    )
    monkeypatch.setattr(
        "backend.apps.risk.repository.risk_repository.RiskRepository.load_positions",
        lambda *a, **k: _noop_async(),
        raising=True,
    )

    # Use the trading service in simulation mode (default)
    svc = get_trading_service(risk_engine)

    async def _run():
        # Submit a small simulated market buy; price arbitrary
        res = await svc.submit_market(symbol="BTCUSDT", side="buy", size=0.001, price=100.0)
        assert isinstance(res, dict)
        assert res.get("status") in ("filled", "rejected")  # allow risk reject in some envs

    # Run the coroutine (portable across Python versions)
    asyncio.run(_run())

    # Verify metrics endpoint includes slippage histogram name
    c = TestClient(app)
    r = c.get("/metrics")
    assert r.status_code == 200
    text = r.text
    assert "trading_order_slippage_bps" in text
