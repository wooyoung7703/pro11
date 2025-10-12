import pytest
from backend.apps.trading.simulator import generate_mock_series, simulate_trading, SimConfig


def test_generate_mock_series_basic():
    prices, probs = generate_mock_series(n=100, start_price=1.0, drift=0.0, vol=0.01, prob_noise=0.05, seed=1)
    assert len(prices) == 100
    assert len(probs) == 100
    assert all(p > 0 for p in prices)
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_simulate_trading_mock_ok():
    prices, probs = generate_mock_series(n=200, seed=2)
    cfg = SimConfig(base_units=10.0, fee_rate=0.001, threshold=0.5, allow_scale_in=True, si_ratio=0.5, si_max_legs=2, si_min_price_move=0.002)
    res = simulate_trading(prices, probs, cfg)
    assert res["status"] == "ok"
    assert isinstance(res.get("events"), list)
    assert isinstance(res.get("trades"), list)
    assert isinstance(res.get("equity"), list)
    # some result sanity
    assert len(res["equity"]) == len(prices)
    # validations should be a list
    assert isinstance(res.get("validations"), list)


def test_simulate_custom_series_buy_and_exit():
    # simple series that guarantees one buy then exit
    prices = [1.0, 1.0, 1.0, 1.0]
    probs = [0.4, 0.6, 0.6, 0.4]
    cfg = SimConfig(base_units=1.0, threshold=0.5, fee_rate=0.0, allow_scale_in=False)
    res = simulate_trading(prices, probs, cfg)
    assert res["status"] == "ok"
    # expect at least one buy and one exit event
    kinds = [e["kind"] for e in res["events"]]
    assert "buy" in kinds
    assert "exit" in kinds