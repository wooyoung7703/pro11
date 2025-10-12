import pytest
from backend.apps.training.auto_promotion import promote_if_better, PROMOTION_STATE
from backend.common.config.base_config import load_config

@pytest.mark.asyncio
async def test_promotion_disabled(monkeypatch):
    cfg = load_config()
    # Force disabled path by monkeypatching config attribute
    monkeypatch.setattr('backend.apps.training.auto_promotion.CFG', cfg)
    monkeypatch.setattr('backend.apps.training.auto_promotion.CFG', cfg)
    # Ensure flag false
    monkeypatch.setattr('backend.apps.training.auto_promotion.CFG', cfg)
    cfg.auto_promote_enabled = False  # type: ignore
    res = await promote_if_better(123, {"samples": 200})
    assert res["promoted"] is False
    assert res["reason"] == "disabled_or_invalid"
