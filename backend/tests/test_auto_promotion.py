import pytest
from backend.apps.training.auto_promotion import promote_if_better, CFG, PROMOTION_STATE
from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository

class DummyRepo:
    def __init__(self, latest_rows, promote_ok=True):
        self._latest = latest_rows
        self._promote_ok = promote_ok
        self.promoted_id = None
    async def fetch_latest(self, name, model_type, limit=10):
        return self._latest
    async def promote(self, model_id: int):
        if self._promote_ok:
            self.promoted_id = model_id
            # return row-like dict
            return {"id": model_id, "status": "production", "metrics": self._latest[0]["metrics"] if self._latest else {}}
        return None

@pytest.mark.asyncio
async def test_promotion_success(monkeypatch):
    prod = {"id": 1, "status": "production", "metrics": {"samples": 1000, "auc": 0.52}}
    latest = [prod]
    new_metrics = {"samples": 1100, "auc": 0.53}
    repo = DummyRepo(latest_rows=latest)
    monkeypatch.setattr("backend.apps.training.auto_promotion.ModelRegistryRepository", lambda: repo)
    PROMOTION_STATE.test_mode = True
    res = await promote_if_better(new_model_id=2, new_metrics=new_metrics)
    assert res["promoted"] is True
    assert repo.promoted_id == 2

@pytest.mark.asyncio
async def test_promotion_auc_insufficient(monkeypatch):
    prod = {"id": 1, "status": "production", "metrics": {"samples": 1000, "auc": 0.52}}
    latest = [prod]
    new_metrics = {"samples": 1100, "auc": 0.525}  # small relative improvement
    repo = DummyRepo(latest_rows=latest)
    monkeypatch.setattr("backend.apps.training.auto_promotion.ModelRegistryRepository", lambda: repo)
    PROMOTION_STATE.test_mode = True
    res = await promote_if_better(new_model_id=2, new_metrics=new_metrics)
    assert res["promoted"] is False
    assert res["reason"] == "insufficient_auc_improvement"

@pytest.mark.asyncio
async def test_promotion_samples_insufficient(monkeypatch):
    prod = {"id": 1, "status": "production", "metrics": {"samples": 1000, "auc": 0.52}}
    latest = [prod]
    new_metrics = {"samples": 1020, "auc": 0.60}  # good AUC but low growth if threshold 1.05
    repo = DummyRepo(latest_rows=latest)
    monkeypatch.setattr("backend.apps.training.auto_promotion.ModelRegistryRepository", lambda: repo)
    PROMOTION_STATE.test_mode = True
    res = await promote_if_better(new_model_id=2, new_metrics=new_metrics)
    assert res["promoted"] is False
    assert res["reason"] == "insufficient_sample_growth"