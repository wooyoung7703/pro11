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
    cfg = load_config()
    cfg.auto_retrain_drift_features = "ret_1,ret_5,ret_10"
    cfg.auto_retrain_drift_mode = "max_abs"
    cfg.auto_retrain_enabled = True
    cfg.auto_retrain_required_consecutive_drifts = 1
    cfg.auto_retrain_min_samples = 10
    cfg.auto_retrain_check_interval = 0
    cfg.auto_retrain_min_interval = 0
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler.CFG", cfg)
    from backend.apps.training.auto_retrain_scheduler import AutoRetrainState
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler.STATE", AutoRetrainState())

    # mapping with highest |z| at ret_10
    svc = DummyFeatureService({"ret_1": 1.0, "ret_5": 2.0, "ret_10": 3.5})

    # Monkeypatch training service to short-circuit run_training
    async def fake_run_training(self, limit=10):
        return {"status": "ok", "model_id": 99, "version": "v", "artifact_path": "p", "metrics": {"samples": 500, "auc": 0.6}}
    monkeypatch.setattr("backend.apps.training.training_service.TrainingService.run_training", fake_run_training)
    async def fake_load_recent(self, limit=10):
        return [object()] * limit
    monkeypatch.setattr("backend.apps.training.training_service.TrainingService.load_recent_features", fake_load_recent)
    # Monkeypatch repository create_job to capture feature
    created = {}
    async def fake_create_job(self, trigger, drift_feature, drift_z):
        created["feature"] = drift_feature
        created["z"] = drift_z
        return 1
    monkeypatch.setattr("backend.apps.training.repository.training_job_repository.TrainingJobRepository.create_job", fake_create_job)
    async def fake_mark_success(self, job_id, *args, **kwargs):
        return True
    async def fake_mark_error(self, job_id, *args, **kwargs):
        return True
    monkeypatch.setattr("backend.apps.training.repository.training_job_repository.TrainingJobRepository.mark_success", fake_mark_success)
    monkeypatch.setattr("backend.apps.training.repository.training_job_repository.TrainingJobRepository.mark_error", fake_mark_error)
    async def fake_audit(self, *args, **kwargs):
        return None
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler.LifecycleAuditRepository.log_retrain", fake_audit)
    async def fake_promote(model_id, metrics):
        return {"promoted": False, "reason": "skipped"}
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler.promote_if_better", fake_promote)

    # Monkeypatch lock acquisition to always succeed
    async def always_lock(key):
        return True
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler._try_acquire_lock", always_lock)
    async def release_lock(key):
        return True
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler._release_lock", release_lock)

    # Run loop once (schedule cancellation after first sleep)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(auto_retrain_loop(svc), timeout=0.2)

    assert created.get("feature") == "ret_10"
    assert abs(created.get("z") - 3.5) < 1e-6

@pytest.mark.asyncio
async def test_multi_feature_mean_top3(monkeypatch):
    cfg = load_config()
    cfg.auto_retrain_drift_features = "ret_1,ret_5,ret_10"
    cfg.auto_retrain_drift_mode = "mean_top3"
    cfg.auto_retrain_enabled = True
    cfg.auto_retrain_required_consecutive_drifts = 1
    cfg.auto_retrain_min_samples = 10
    cfg.auto_retrain_check_interval = 0
    cfg.auto_retrain_min_interval = 0
    cfg.drift_z_threshold = 2.5
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler.CFG", cfg)
    from backend.apps.training.auto_retrain_scheduler import AutoRetrainState
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler.STATE", AutoRetrainState())

    # z-scores: abs values top3 = 3.0,2.6,2.4 -> mean_abs = 2.666 > 2.5 -> trigger
    svc = DummyFeatureService({"ret_1": 3.0, "ret_5": 2.6, "ret_10": 2.4})

    async def fake_run_training(self, limit=10):
        return {"status": "ok", "model_id": 100, "version": "v", "artifact_path": "p", "metrics": {"samples": 600, "auc": 0.61}}
    monkeypatch.setattr("backend.apps.training.training_service.TrainingService.run_training", fake_run_training)
    async def fake_load_recent(self, limit=10):
        return [object()] * limit
    monkeypatch.setattr("backend.apps.training.training_service.TrainingService.load_recent_features", fake_load_recent)

    created = {}
    async def fake_create_job(self, trigger, drift_feature, drift_z):
        created["feature"] = drift_feature
        created["z"] = drift_z
        return 2
    monkeypatch.setattr("backend.apps.training.repository.training_job_repository.TrainingJobRepository.create_job", fake_create_job)
    async def fake_mark_success(self, job_id, *args, **kwargs):
        return True
    async def fake_mark_error(self, job_id, *args, **kwargs):
        return True
    monkeypatch.setattr("backend.apps.training.repository.training_job_repository.TrainingJobRepository.mark_success", fake_mark_success)
    monkeypatch.setattr("backend.apps.training.repository.training_job_repository.TrainingJobRepository.mark_error", fake_mark_error)
    async def fake_audit(self, *args, **kwargs):
        return None
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler.LifecycleAuditRepository.log_retrain", fake_audit)
    async def fake_promote(model_id, metrics):
        return {"promoted": False, "reason": "skipped"}
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler.promote_if_better", fake_promote)
    async def always_lock(key):
        return True
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler._try_acquire_lock", always_lock)
    async def release_lock(key):
        return True
    monkeypatch.setattr("backend.apps.training.auto_retrain_scheduler._release_lock", release_lock)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(auto_retrain_loop(svc), timeout=0.2)

    # Top feature still highest abs z
    assert created.get("feature") == "ret_1"
    assert abs(created.get("z") - 3.0) < 1e-6
