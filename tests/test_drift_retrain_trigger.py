import asyncio, pytest
from backend.apps.training import auto_retrain_scheduler as sched
from backend.apps.training.auto_retrain_scheduler import auto_retrain_loop, STATE
from backend.apps.features.service.feature_service import FeatureService

@pytest.mark.asyncio
async def test_drift_retrain_trigger(monkeypatch):
    # 설정: drift 기준 retrain만 허용
    for c in (sched.CFG,):
        c.auto_retrain_enabled = True
        c.calibration_retrain_enabled = False
        c.auto_retrain_min_interval = 1
        c.auto_retrain_check_interval = 0.05
        c.auto_retrain_required_consecutive_drifts = 1
        c.drift_z_threshold = 3.0

    svc = FeatureService(sched.CFG.symbol, sched.CFG.kline_interval)
    # STATE 초기화 (직전 run 흔적 제거)
    STATE.consecutive_drifts = 0
    STATE.last_run_ts = None

    # z-score 5 로 drift 발생
    async def fake_compute_drift(feature: str, window: int):  # type: ignore
        return {"status": "ok", "z_score": 5.0}
    svc.compute_drift = fake_compute_drift  # type: ignore

    # 최근 feature rows 충분
    from backend.apps.training.training_service import TrainingService
    async def fake_load_recent_features(self, limit: int):  # type: ignore
        return [{"x":1}]*limit
    TrainingService.load_recent_features = fake_load_recent_features  # type: ignore

    # 빠른 training
    from backend.apps.training import training_service as training_module
    async def fake_run_training(self, limit: int):  # type: ignore
        return {"status": "ok", "artifact_path": "x", "model_id": 2, "version": "2", "metrics": {"samples": limit}}
    training_module.TrainingService.run_training = fake_run_training  # type: ignore

    # Repo/Audit/Promotion/Lock patch
    from backend.apps.training.repository import training_job_repository as repo_module
    async def fake_create_job(self, **kwargs): return 1001  # type: ignore
    async def fake_mark_success(self, *a, **k): return None  # type: ignore
    async def fake_mark_error(self, *a, **k): return None  # type: ignore
    repo_module.TrainingJobRepository.create_job = fake_create_job  # type: ignore
    repo_module.TrainingJobRepository.mark_success = fake_mark_success  # type: ignore
    repo_module.TrainingJobRepository.mark_error = fake_mark_error  # type: ignore

    from backend.apps.model_registry.repository import lifecycle_audit_repository as audit_mod
    async def fake_log_retrain(self, **kwargs): return None  # type: ignore
    audit_mod.LifecycleAuditRepository.log_retrain = fake_log_retrain  # type: ignore

    from backend.apps.training import auto_promotion as promo_mod
    async def fake_promote_if_better(model_id, metrics): return {"promoted": False, "reason": "noop"}  # type: ignore
    promo_mod.promote_if_better = fake_promote_if_better  # type: ignore

    async def fake_try_lock(lock_key): return True
    async def fake_release_lock(lock_key): return None
    monkeypatch.setattr(sched, "_try_acquire_lock", fake_try_lock)
    monkeypatch.setattr(sched, "_release_lock", fake_release_lock)

    task = asyncio.create_task(auto_retrain_loop(svc))
    # 약간 더 대기하여 loop 가 drift 평가 + retrain 실행 기회 확보
    await asyncio.sleep(0.6)
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1)
    except asyncio.CancelledError:
        pass

    assert STATE.last_run_ts is not None
    # 단순화: 상태 객체 last_trigger_type 으로 검증 (metrics internals 의존 회피)
    assert STATE.last_trigger_type == "drift_auto"