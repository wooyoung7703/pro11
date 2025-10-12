from backend.apps.risk.service.risk_engine import RiskEngine, RiskLimits


def test_risk_allows_small_order():
    engine = RiskEngine(RiskLimits(max_notional=10000, max_daily_loss=1000, max_drawdown=0.3, atr_multiple=2))
    result = engine.evaluate_order("XRPUSDT", current_price=0.5, intended_size=100, atr=0.001)
    assert result["allowed"] is True


def test_risk_blocks_notional():
    engine = RiskEngine(RiskLimits(max_notional=1000, max_daily_loss=1000, max_drawdown=0.3, atr_multiple=2))
    result = engine.evaluate_order("XRPUSDT", current_price=1.0, intended_size=2000, atr=0.001)
    assert result["allowed"] is False
    assert "notional_limit" in result["reasons"]
