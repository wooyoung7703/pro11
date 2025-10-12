import pytest
from backend.apps.risk.repository.risk_repository import RiskRepository
from backend.common.db.connection import init_pool


@pytest.mark.asyncio
async def test_risk_repository_save_load(monkeypatch):
    await init_pool()
    repo = RiskRepository("test_session")
    await repo.save_state(10000, 10000, 10000, 0, 0)
    await repo.save_position("XRPUSDT", 100, 0.5)
    state = await repo.load_state()
    positions = await repo.load_positions()
    assert state is not None
    assert len(positions) == 1
    assert positions[0]["symbol"] == "XRPUSDT"