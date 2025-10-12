import asyncio
import types
from prometheus_client import REGISTRY
from backend.apps.api.main import update_risk_metrics, risk_engine, RISK_CURRENT_EQUITY, RISK_PEAK_EQUITY, RISK_DRAWDOWN_RATIO

async def run_update():
    # mutate risk engine session for predictable values
    risk_engine.session.current_equity = 9000.0
    risk_engine.session.peak_equity = 10000.0
    risk_engine.session.starting_equity = 10000.0
    # Add a position
    risk_engine.positions['XRPUSDT'] = types.SimpleNamespace(size=1000.0, entry_price=0.5)
    await update_risk_metrics()


def test_risk_metrics_update(event_loop):
    event_loop.run_until_complete(run_update())
    assert RISK_CURRENT_EQUITY._value.get() == 9000.0
    assert RISK_PEAK_EQUITY._value.get() == 10000.0
    # drawdown 10%
    assert abs(RISK_DRAWDOWN_RATIO._value.get() - 0.1) < 1e-6
