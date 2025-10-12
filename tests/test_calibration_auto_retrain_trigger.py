import asyncio
import pytest
import httpx
from backend.apps.api.main import _streak_state, cfg
from backend.apps.training import auto_retrain_scheduler as sched
from backend.apps.training.auto_retrain_scheduler import auto_retrain_loop, STATE, CALIBRATION_RETRAIN_DELTA_BEFORE
from backend.apps.features.service.feature_service import FeatureService

@pytest.mark.asyncio
async def test_calibration_retrain_trigger(monkeypatch):
    # Align both config objects (api.main.cfg and scheduler.CFG)
    for c in (cfg, sched.CFG):
        c.auto_retrain_enabled = True
        c.calibration_retrain_enabled = True
        c.calibration_retrain_min_interval = 1
        c.auto_retrain_min_interval = 1
        c.auto_retrain_check_interval = 0.05

    # Provide fake feature service returning enough rows quickly
    svc = FeatureService(cfg.symbol, cfg.kline_interval)

    async def fake_compute_drift(feature: str, window: int):
        return {"status": "ok", "z_score": 0.0}  # no feature drift
    svc.compute_drift = fake_compute_drift  # type: ignore

    # Patch training service run to fast result
    from backend.apps.training import training_service as training_module
    async def fake_run_training(self, limit: int):  # type: ignore
        return {"status": "ok", "artifact_path": "x", "model_id": 1, "version": "1", "metrics": {"samples": limit}}
    training_module.TrainingService.run_training = fake_run_training  # type: ignore

    # Patch repository create_job to avoid DB (return incremental id)
    from backend.apps.training.repository import training_job_repository as repo_module
    async def fake_create_job(self, **kwargs):  # type: ignore
        return 999
    repo_module.TrainingJobRepository.create_job = fake_create_job  # type: ignore
    async def fake_mark_success(self, job_id, artifact_path, model_id, version, metrics):  # type: ignore
        return None
    repo_module.TrainingJobRepository.mark_success = fake_mark_success  # type: ignore
    async def fake_mark_error(self, job_id, error):  # type: ignore
        return None
    repo_module.TrainingJobRepository.mark_error = fake_mark_error  # type: ignore

    # Patch audit repo
    from backend.apps.model_registry.repository import lifecycle_audit_repository as audit_mod
    async def fake_log_retrain(self, **kwargs):  # type: ignore
        return None
    audit_mod.LifecycleAuditRepository.log_retrain = fake_log_retrain  # type: ignore

    # Patch promote_if_better to no-op
    from backend.apps.training import auto_promotion as promo_mod
    async def fake_promote_if_better(model_id, metrics):  # type: ignore
        return {"promoted": False, "reason": "noop"}
    promo_mod.promote_if_better = fake_promote_if_better  # type: ignore

    # Provide sufficient recent features to pass sample check
    async def fake_load_recent_features(self, limit: int):  # type: ignore
        return [ {"x":1} ] * limit
    from backend.apps.training.training_service import TrainingService
    TrainingService.load_recent_features = fake_load_recent_features  # type: ignore

    # Simulate recommendation state
    _streak_state.last_recommend = True
    _streak_state.last_snapshot = {"abs_drift": True, "rel_drift": False, "delta": 0.2}

    # Patch lock acquisition to bypass DB
    async def fake_try_lock(lock_key):
        return True
    async def fake_release_lock(lock_key):
        return None
    monkeypatch.setattr(sched, "_try_acquire_lock", fake_try_lock)
    monkeypatch.setattr(sched, "_release_lock", fake_release_lock)

    task = asyncio.create_task(auto_retrain_loop(svc))
    await asyncio.sleep(0.3)
    task.cancel()
    # Gracefully await cancellation (loop swallows CancelledError internally)
    try:
        await asyncio.wait_for(task, timeout=1)
    except asyncio.CancelledError:
        pass

    assert STATE.last_calibration_retrain_ts is not None
    assert STATE.last_calibration_reason is not None
    # delta_before gauge 가 설정되었는지 (0 초과) 확인
    # prometheus_client Gauge 는 _value.get() 로 내부 값 접근 가능
    try:
        val = CALIBRATION_RETRAIN_DELTA_BEFORE._value.get()  # type: ignore
        assert val > 0
    except Exception:
        # 만약 internals 변경되면 최소한 retrain_reason 에 delta fragment 포함 확인
        assert 'delta=' in (STATE.last_calibration_reason or '')
    # 라벨형 카운터 증가 확인 (calibration)
    # 상태 기반 간단 검증
    assert STATE.last_trigger_type == "calibration_auto"
