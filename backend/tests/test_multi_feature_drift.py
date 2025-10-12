import pytest
import asyncio
from types import SimpleNamespace
from backend.apps.training.auto_retrain_scheduler import auto_retrain_status, STATE
from backend.apps.training.auto_retrain_scheduler import auto_retrain_loop
from backend.apps.features.service.feature_service import FeatureService
from backend.common.config.base_config import load_config

# We'll monkeypatch FeatureService.compute_drift to return controlled z-scores.

class DummyFeatureService(FeatureService):
    def __init__(self, mapping):
        self.mapping = mapping
        self.symbol = "XRPUSDT"
        self.interval = "1m"
        self.last_success_ts = None
        self.last_error_ts = None
    async def compute_drift(self, feature: str, window: int = 200):
        z = self.mapping.get(feature, 0.0)
        return {"status": "ok", "z_score": z}

@pytest.mark.asyncio
async def test_multi_feature_max_abs(monkeypatch):
    # Setup config overrides
    monkeypatch.setenv("AUTO_RETRAIN_DRIFT_FEATURES", "ret_1,ret_5,ret_10")
    monkeypatch.setenv("AUTO_RETRAIN_DRIFT_MODE", "max_abs")
    monkeypatch.setenv("AUTO_RETRAIN_ENABLED", "true")
    monkeypatch.setenv("AUTO_RETRAIN_REQUIRED_CONSECUTIVE_DRIFTS", "1")
    monkeypatch.setenv("AUTO_RETRAIN_MIN_SAMPLES", "10")
    cfg = load_config()

    # mapping with highest |z| at ret_10
    svc = DummyFeatureService({"ret_1": 1.0, "ret_5": 2.0, "ret_10": 3.5})

    # Monkeypatch training service to short-circuit run_training
    async def fake_run_training(self, limit=10):
        return {"status": "ok", "model_id": 99, "version": "v", "artifact_path": "p", "metrics": {"samples": 500, "auc": 0.6}}
    monkeypatch.setattr("backend.apps.training.training_service.TrainingService.run_training", fake_run_training)
    # Monkeypatch repository create_job to capture feature
    created = {}
    async def fake_create_job(self, trigger, drift_feature, drift_z):
        created["feature"] = drift_feature
        created["z"] = drift_z
        return 1
    monkeypatch.setattr("backend.apps.training.repository.training_job_repository.TrainingJobRepository.create_job", fake_create_job)

    # Monkeypatch lock acquisition to always succeed
    async def always_lock(key):
        return True
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler._try_acquire_lock", always_lock)
    async def release_lock(key):
        return True
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler._release_lock", release_lock)

    # Run loop once (schedule cancellation after first sleep)
    async def runner():
        task = asyncio.create_task(auto_retrain_loop(svc))
        await asyncio.sleep(cfg.auto_retrain_check_interval + 0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await runner()

    assert created.get("feature") == "ret_10"
    assert abs(created.get("z") - 3.5) < 1e-6

@pytest.mark.asyncio
async def test_multi_feature_mean_top3(monkeypatch):
    monkeypatch.setenv("AUTO_RETRAIN_DRIFT_FEATURES", "ret_1,ret_5,ret_10")
    monkeypatch.setenv("AUTO_RETRAIN_DRIFT_MODE", "mean_top3")
    monkeypatch.setenv("AUTO_RETRAIN_ENABLED", "true")
    monkeypatch.setenv("AUTO_RETRAIN_REQUIRED_CONSECUTIVE_DRIFTS", "1")
    monkeypatch.setenv("AUTO_RETRAIN_MIN_SAMPLES", "10")
    monkeypatch.setenv("DRIFT_Z_THRESHOLD", "2.5")  # require mean_abs >= 2.5
    cfg = load_config()

    # z-scores: abs values top3 = 3.0,2.6,2.4 -> mean_abs = 2.666 > 2.5 -> trigger
    svc = DummyFeatureService({"ret_1": 3.0, "ret_5": 2.6, "ret_10": 2.4})

    async def fake_run_training(self, limit=10):
        return {"status": "ok", "model_id": 100, "version": "v", "artifact_path": "p", "metrics": {"samples": 600, "auc": 0.61}}
    monkeypatch.setattr("backend.apps.training.training_service.TrainingService.run_training", fake_run_training)

    created = {}
    async def fake_create_job(self, trigger, drift_feature, drift_z):
        created["feature"] = drift_feature
        created["z"] = drift_z
        return 2
    monkeypatch.setattr("backend.apps.training.repository.training_job_repository.TrainingJobRepository.create_job", fake_create_job)
    async def always_lock(key):
        return True
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler._try_acquire_lock", always_lock)
    async def release_lock(key):
        return True
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler._release_lock", release_lock)

    async def runner():
        task = asyncio.create_task(auto_retrain_loop(svc))
        await asyncio.sleep(cfg.auto_retrain_check_interval + 0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await runner()

    # Top feature still highest abs z
    assert created.get("feature") == "ret_1"
    assert abs(created.get("z") - 3.0) < 1e-6
