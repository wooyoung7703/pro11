from __future__ import annotations
import asyncio
import time
from prometheus_client import Counter, Gauge
import contextlib
from backend.common.db.connection import init_pool
from backend.common.config.base_config import load_config
from backend.apps.features.service.feature_service import FeatureService
from backend.apps.training.training_service import TrainingService
from backend.apps.training.repository.training_job_repository import TrainingJobRepository
from backend.apps.training.auto_promotion import promote_if_better
from backend.apps.model_registry.repository.lifecycle_audit_repository import LifecycleAuditRepository
from prometheus_client import Gauge as _Gauge, Counter as _Counter

CFG = load_config()

RETRAIN_CHECKS_TOTAL = Counter("auto_retrain_checks_total", "Total auto retrain drift checks")
RETRAIN_TRIGGERED_TOTAL = Counter("auto_retrain_triggered_total", "Training runs triggered automatically")
RETRAIN_LAST_RUN_TS = Gauge("auto_retrain_last_run_timestamp", "Unix ts of last auto retrain")
RETRAIN_CONSEC_DRIFTS = Gauge("auto_retrain_consecutive_drifts", "Current consecutive drift count")
RETRAIN_LOCK_ACQUIRE_TOTAL = Counter("auto_retrain_lock_acquire_total", "Attempts to acquire advisory lock")
RETRAIN_LOCK_ACQUIRED_TOTAL = Counter("auto_retrain_lock_acquired_total", "Successful advisory lock acquisitions")
RETRAIN_LOCK_HELD = Gauge("auto_retrain_lock_held", "1 if this instance holds the advisory lock else 0")
CALIBRATION_RETRAIN_TRIGGERED_TOTAL = _Counter("calibration_retrain_triggered_total", "Calibration-based retrain triggers")
CALIBRATION_LAST_RECOMMEND_TS = _Gauge("calibration_retrain_last_recommend_timestamp", "Last time calibration retrain fired")
# Cooldown 남은 시간을 노출 (초 단위). retrain 직후 min_gap 기준으로 감소, 0 이하면 0 유지.
CALIBRATION_RETRAIN_COOLDOWN_SECONDS = _Gauge("calibration_retrain_cooldown_seconds", "Seconds remaining in calibration retrain cooldown (0 if ready)")
CALIBRATION_RETRAIN_DELTA_BEFORE = _Gauge("calibration_retrain_delta_before", "Calibration ECE delta at the moment a calibration-based retrain is triggered")
RETRAIN_TRIGGERED_TYPE_TOTAL = Counter(
    "auto_retrain_triggered_type_total",
    "Auto retrain triggers by type",
    labelnames=["type"],  # type: drift|calibration
)
# Calibration CV degradation (새로운 강화 조건) 메트릭
CALIBRATION_CV_DEGRADATION_ACTIVE = Gauge(
    "calibration_cv_degradation_active",
    "1 if CV mean AUC degradation vs production exceeds threshold, else 0",
)
CALIBRATION_CV_DEGRADATION_RATIO = Gauge(
    "calibration_cv_degradation_ratio",
    "Ratio last_cv_mean_auc / production_auc (lower < 1 means degradation)",
)
CALIBRATION_CV_DEGRADATION_EVENTS = Counter(
    "calibration_cv_degradation_events_total",
    "CV degradation state transitions",
    labelnames=["state"],  # enter|exit
)

# 내부 상태 flag
_cv_degradation_state = {"active": False}

async def _try_acquire_lock(lock_key: int) -> bool:
    pool = await init_pool()
    async with pool.acquire() as conn:  # type: ignore
        RETRAIN_LOCK_ACQUIRE_TOTAL.inc()
        row = await conn.fetchrow("SELECT pg_try_advisory_lock($1) AS acquired", lock_key)
        acquired = bool(row["acquired"])  # type: ignore
        if acquired:
            RETRAIN_LOCK_ACQUIRED_TOTAL.inc()
            RETRAIN_LOCK_HELD.set(1)
        return acquired

async def _release_lock(lock_key: int) -> None:
    pool = await init_pool()
    async with pool.acquire() as conn:  # type: ignore
        await conn.fetchval("SELECT pg_advisory_unlock($1)", lock_key)
        RETRAIN_LOCK_HELD.set(0)

class AutoRetrainState:
    def __init__(self):
        self.consecutive_drifts = 0
        self.last_run_ts: float | None = None
        self.last_job_id: int | None = None
        self.last_status: str | None = None
        self.last_error: str | None = None
        self.running_job: bool = False
        self.last_calibration_retrain_ts: float | None = None
        self.last_calibration_reason: str | None = None
        # 새 필드: 재훈련 직전 delta 및 효과 평가 시각
        self.last_calibration_delta_before: float | None = None
        self.last_calibration_effect_evaluated_ts: float | None = None
        self.last_trigger_type: str | None = None
        # 마지막 개선율 평가에 사용된 snapshot ts (중복 증가 방지)
        self.last_calibration_effect_snapshot_ts: float | None = None

STATE = AutoRetrainState()

async def auto_retrain_loop(feature_service: FeatureService):
    repo = TrainingJobRepository()
    audit_repo = LifecycleAuditRepository()
    training_service = TrainingService(CFG.symbol, CFG.kline_interval, CFG.model_artifact_dir)
    while True:
        start_sleep = CFG.auto_retrain_check_interval
        try:
            if not CFG.auto_retrain_enabled:
                await asyncio.sleep(start_sleep)
                continue
            RETRAIN_CHECKS_TOTAL.inc()
            # Skip if a job currently running
            if STATE.running_job:
                await asyncio.sleep(start_sleep)
                continue
            # Minimum interval since last run
            now = time.time()
            if STATE.last_run_ts and now - STATE.last_run_ts < CFG.auto_retrain_min_interval:
                await asyncio.sleep(start_sleep)
                continue
            # Multi-feature drift evaluation
            selected_feature = "ret_1"
            z_score = None
            drift_flag = False
            feature_list: list[str]
            if CFG.auto_retrain_drift_features:
                feature_list = [f.strip() for f in CFG.auto_retrain_drift_features.split(",") if f.strip()]
            else:
                feature_list = ["ret_1"]
            z_scores: list[tuple[str, float]] = []
            for feat in feature_list:
                stats = await feature_service.compute_drift(feature=feat, window=CFG.auto_retrain_drift_window)
                if stats.get("status") == "ok":
                    z = float(stats.get("z_score") or 0.0)
                    z_scores.append((feat, z))
            if z_scores:
                # Determine aggregate drift depending on mode
                if CFG.auto_retrain_drift_mode == "mean_top3" and len(z_scores) > 1:
                    # sort by absolute z, take top3
                    top = sorted(z_scores, key=lambda x: abs(x[1]), reverse=True)[:3]
                    mean_abs = sum(abs(z) for _, z in top) / len(top)
                    drift_flag = mean_abs >= CFG.drift_z_threshold
                    # pick the top feature for logging
                    selected_feature, z_score = top[0][0], top[0][1]
                else:  # default max_abs
                    top = max(z_scores, key=lambda x: abs(x[1]))
                    selected_feature, z_score = top
                    drift_flag = abs(z_score) >= CFG.drift_z_threshold
            if drift_flag:
                STATE.consecutive_drifts += 1
            else:
                STATE.consecutive_drifts = 0
            RETRAIN_CONSEC_DRIFTS.set(STATE.consecutive_drifts)
            # Calibration-based retrain path (independent of feature drift path, but shares loop & lock logic)
            calibration_trigger = False
            calibration_reason: str | None = None
            # CV degradation 계산 (production vs 최신 학습 CV mean AUC) - promotion 전 상태 개선 감지 목적
            cv_degradation_active = False
            cv_ratio = None
            try:
                # production 모델 AUC 조회 (registry 최신 production)
                from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository
                repo_reg = ModelRegistryRepository()
                rows_prod = await repo_reg.fetch_latest(CFG.auto_promote_model_name, "supervised", limit=5)
                prod_auc = None
                for r in rows_prod:
                    if r.get("status") == "production":
                        metrics_p = r.get("metrics") or {}
                        prod_auc = metrics_p.get("auc")
                        if isinstance(prod_auc, (int,float)):
                            break
                # 최근 staging (또는 마지막 성공 retrain) 중 CV mean AUC 추출
                last_cv_mean = None
                for r in rows_prod:
                    m = r.get("metrics") or {}
                    cv = m.get("cv_report") if isinstance(m.get("cv_report"), dict) else None
                    if cv:
                        auc_block = cv.get("auc") if isinstance(cv.get("auc"), dict) else None
                        mean_auc = auc_block.get("mean") if auc_block else None
                        if isinstance(mean_auc, (int,float)):
                            last_cv_mean = float(mean_auc)
                            break
                if isinstance(prod_auc, (int,float)) and isinstance(last_cv_mean, (int,float)) and prod_auc and prod_auc == prod_auc and last_cv_mean == last_cv_mean:
                    cv_ratio = last_cv_mean / prod_auc if prod_auc != 0 else None
                    if cv_ratio is not None:
                        CALIBRATION_CV_DEGRADATION_RATIO.set(cv_ratio)
                        # 임계: 환경변수/설정 기반 비율 (기본 0.95)
                        degradation_threshold_ratio = CFG.calibration_cv_degradation_min_ratio
                        if cv_ratio < degradation_threshold_ratio:
                            cv_degradation_active = True
                # 상태 전이 메트릭
                if cv_degradation_active and not _cv_degradation_state["active"]:
                    CALIBRATION_CV_DEGRADATION_EVENTS.labels(state="enter").inc()
                elif (not cv_degradation_active) and _cv_degradation_state["active"]:
                    CALIBRATION_CV_DEGRADATION_EVENTS.labels(state="exit").inc()
                _cv_degradation_state["active"] = cv_degradation_active
                CALIBRATION_CV_DEGRADATION_ACTIVE.set(1 if cv_degradation_active else 0)
            except Exception:
                # Silent ignore; metrics remain last
                pass
            if CFG.calibration_retrain_enabled:
                # Lazy import to avoid circular dependency
                try:
                    from backend.apps.api.main import _streak_state  # type: ignore
                except Exception:
                    _streak_state = None  # type: ignore
                rec = getattr(_streak_state, 'last_recommend', False) if _streak_state else False
                last_snapshot = getattr(_streak_state, 'last_snapshot', None) if _streak_state else None
                if rec and last_snapshot:
                    now_ts = time.time()
                    # Cooldown: respect both generic auto_retrain_min_interval and dedicated calibration interval
                    # Use max of the two intervals for safety
                    min_gap = max(CFG.auto_retrain_min_interval, CFG.calibration_retrain_min_interval)
                    # 업데이트: 현재 쿨다운 잔여시간 Gauge 설정
                    if STATE.last_calibration_retrain_ts is not None:
                        elapsed = now_ts - STATE.last_calibration_retrain_ts
                        remaining = max(min_gap - elapsed, 0)
                        try:
                            CALIBRATION_RETRAIN_COOLDOWN_SECONDS.set(remaining)
                        except Exception:
                            pass
                    else:
                        try:
                            CALIBRATION_RETRAIN_COOLDOWN_SECONDS.set(0)
                        except Exception:
                            pass
                    if (STATE.last_calibration_retrain_ts is None) or (now_ts - STATE.last_calibration_retrain_ts >= min_gap):
                        calibration_trigger = True
                        # Compose reason from snapshot flags & reasons
                        parts = []
                        if last_snapshot.get("abs_drift"):
                            parts.append("abs_drift")
                        if last_snapshot.get("rel_drift"):
                            parts.append("rel_drift")
                        if last_snapshot.get("delta") is not None:
                            parts.append(f"delta={last_snapshot.get('delta'):.4f}")
                        calibration_reason = ",".join(parts) or "calibration_recommend"
                        # retrain 직전 delta gauge 설정 (None 방지)
                        try:
                            dval = last_snapshot.get("delta")
                            if isinstance(dval, (int, float)):
                                CALIBRATION_RETRAIN_DELTA_BEFORE.set(float(dval))
                                STATE.last_calibration_delta_before = float(dval)
                        except Exception:
                            pass
                else:
                    # 추천이 없는 경우 쿨다운은 의미 없으므로 0 세팅 시도
                    try:
                        CALIBRATION_RETRAIN_COOLDOWN_SECONDS.set(0)
                    except Exception:
                        pass
            # Trigger only if threshold met (feature drift) OR calibration recommendation path
            feature_drift_trigger = drift_flag and STATE.consecutive_drifts >= CFG.auto_retrain_required_consecutive_drifts
            # 강화 조건: calibration_trigger 단독 OR (calibration 추천 + CV degradation 동반) OR (feature drift 기존 조건)
            combined_calib_trigger = False
            if calibration_trigger:
                # CV degradation이 active이면 reason 확장
                if _cv_degradation_state["active"]:
                    combined_calib_trigger = True
                    if calibration_reason:
                        calibration_reason += ",cv_degradation"
                    else:
                        calibration_reason = "cv_degradation"
                else:
                    # 기존 로직 유지 (추천 자체만으로도 retrain) — 정책상 강화하려면 여기서 combined만 허용 가능
                    combined_calib_trigger = True
            if feature_drift_trigger or combined_calib_trigger:
                # Extra data sufficiency check via training service
                rows = await training_service.load_recent_features(limit=CFG.auto_retrain_min_samples)
                if len(rows) < CFG.auto_retrain_min_samples:
                    # not enough data; do not reset consecutive on this case
                    await asyncio.sleep(start_sleep)
                    continue
                # Launch training
                # Acquire advisory lock to avoid duplicate cross-instance triggering
                acquired = await _try_acquire_lock(CFG.auto_retrain_lock_key)
                if not acquired:
                    # Another instance handling it; just sleep
                    await asyncio.sleep(start_sleep)
                    continue
                trigger_type = "calibration_auto" if combined_calib_trigger and not feature_drift_trigger else "drift_auto"
                job_id = await repo.create_job(trigger=trigger_type, drift_feature=selected_feature, drift_z=z_score)
                STATE.running_job = True
                try:
                    RETRAIN_TRIGGERED_TOTAL.inc()
                    STATE.last_trigger_type = trigger_type
                    try:
                        if trigger_type == "calibration_auto":
                            RETRAIN_TRIGGERED_TYPE_TOTAL.labels(type="calibration").inc()
                        else:
                            RETRAIN_TRIGGERED_TYPE_TOTAL.labels(type="drift").inc()
                    except Exception:
                        pass
                    if calibration_trigger:
                        CALIBRATION_RETRAIN_TRIGGERED_TOTAL.inc()
                    # Duration 측정 (manual endpoint 와 동일한 히스토그램 공유)
                    t_start = time.perf_counter()
                    # Bottom-only: always run bottom training
                    try:
                        result = await training_service.run_training_bottom(
                            limit=CFG.auto_retrain_min_samples,
                            lookahead=getattr(CFG, 'bottom_lookahead', 30),
                            drawdown=getattr(CFG, 'bottom_drawdown', 0.005),
                            rebound=getattr(CFG, 'bottom_rebound', 0.003),
                        )  # type: ignore[attr-defined]
                    except Exception:
                        # If bottom path fails unexpectedly, surface error status
                        result = {"status": "error:bottom_training_failed"}
                    elapsed = time.perf_counter() - t_start
                    try:
                        # Lazy import to avoid circular import at module load
                        from backend.apps.api.main import TRAINING_DURATION as _TD, TRAINING_RUNS as _TR, TRAINING_DURATION_LABELED as _TDL  # type: ignore
                        status_lbl_auto = result.get("status", "unknown")
                        _TD.observe(elapsed)
                        _TR.labels(status=status_lbl_auto).inc()
                        _TDL.labels(trigger=trigger_type, status=status_lbl_auto).observe(elapsed)
                    except Exception:  # pragma: no cover
                        pass
                    # Record training outcome for failure ratio metric (reuses API module helper if available)
                    try:
                        from backend.apps.api.main import _record_training_outcome  # type: ignore
                        status_lbl_auto = result.get("status", "unknown")
                        _record_training_outcome(status_lbl_auto)
                    except Exception:
                        pass
                    if result.get("status") == "ok":
                        await repo.mark_success(job_id, result["artifact_path"], result.get("model_id"), result["version"], result.get("metrics", {}), elapsed)
                        STATE.last_run_ts = time.time()
                        RETRAIN_LAST_RUN_TS.set(STATE.last_run_ts)
                        if calibration_trigger:
                            STATE.last_calibration_retrain_ts = STATE.last_run_ts
                            STATE.last_calibration_reason = calibration_reason
                            CALIBRATION_LAST_RECOMMEND_TS.set(STATE.last_run_ts)
                            # retrain 직후 즉시 쿨다운 Gauge 업데이트
                            try:
                                min_gap = max(CFG.auto_retrain_min_interval, CFG.calibration_retrain_min_interval)
                                CALIBRATION_RETRAIN_COOLDOWN_SECONDS.set(min_gap)
                            except Exception:
                                pass
                        STATE.last_status = "success"
                        # Attempt auto-promotion
                        try:
                            promo = await promote_if_better(result.get("model_id"), result.get("metrics", {}))
                            # Optionally we could store promotion meta; for now just append to STATE.last_error on failure reason
                            if not promo.get("promoted"):
                                # keep info for inspection
                                STATE.last_error = f"auto_promotion:{promo.get('reason')}"
                            # Promotion reason metrics
                            try:
                                from backend.apps.api.main import TRAINING_PROMOTION_REASON as _PROMO_REASON, _normalize_promotion_reason as _norm  # type: ignore
                                r_raw = promo.get("reason")
                                norm_r = _norm(bool(promo.get("promoted")), r_raw)
                                _PROMO_REASON.labels(reason=norm_r).inc()
                            except Exception:
                                pass
                        except Exception as pe:  # pragma: no cover
                            STATE.last_error = f"auto_promotion_exception:{pe}"
                        STATE.consecutive_drifts = 0  # reset after successful retrain
                        # Audit success
                        samples = (result.get("metrics") or {}).get("samples") if result.get("metrics") else None
                        await audit_repo.log_retrain(
                            job_id=job_id,
                            trigger=trigger_type,
                            status="success",
                            drift_feature=selected_feature,
                            drift_z=z_score,
                            model_id=result.get("model_id"),
                            version=result.get("version"),
                            artifact_path=result.get("artifact_path"),
                            samples=samples,
                            error=None,
                        )
                    else:
                        await repo.mark_error(job_id, f"train_failed:{result.get('status')}", elapsed)
                        STATE.last_status = "error"
                        await audit_repo.log_retrain(
                            job_id=job_id,
                            trigger=trigger_type,
                            status="error",
                            drift_feature=selected_feature,
                            drift_z=z_score,
                            model_id=None,
                            version=None,
                            artifact_path=None,
                            samples=None,
                            error=f"train_failed:{result.get('status')}",
                        )
                except Exception as e:  # pragma: no cover
                    await repo.mark_error(job_id, f"exception:{e}", None)
                    STATE.last_error = str(e)
                    STATE.last_status = "error"
                    await audit_repo.log_retrain(
                        job_id=job_id,
                        trigger=trigger_type,
                        status="error",
                        drift_feature=selected_feature,
                        drift_z=z_score,
                        model_id=None,
                        version=None,
                        artifact_path=None,
                        samples=None,
                        error=str(e),
                    )
                finally:
                    STATE.running_job = False
                    with contextlib.suppress(Exception):  # type: ignore
                        await _release_lock(CFG.auto_retrain_lock_key)
            await asyncio.sleep(start_sleep)
        except asyncio.CancelledError:  # graceful shutdown
            break
        except Exception as e:  # pragma: no cover
            STATE.last_error = str(e)
            await asyncio.sleep(start_sleep)

def auto_retrain_status() -> dict:
    # cooldown 계산 (calibration 기준)
    cooldown_remaining = None
    next_earliest_ts = None
    if STATE.last_calibration_retrain_ts is not None:
        min_gap = max(CFG.auto_retrain_min_interval, CFG.calibration_retrain_min_interval)
        elapsed = time.time() - STATE.last_calibration_retrain_ts
        remaining = max(min_gap - elapsed, 0)
        cooldown_remaining = remaining
        next_earliest_ts = STATE.last_calibration_retrain_ts + min_gap
    return {
        "enabled": CFG.auto_retrain_enabled,
        "consecutive_drifts": STATE.consecutive_drifts,
        "last_run_ts": STATE.last_run_ts,
        "running_job": STATE.running_job,
        "last_status": STATE.last_status,
        "last_error": STATE.last_error,
        "calibration_retrain_enabled": CFG.calibration_retrain_enabled,
        "last_calibration_retrain_ts": STATE.last_calibration_retrain_ts,
        "last_calibration_reason": STATE.last_calibration_reason,
        "calibration_cooldown_remaining_seconds": cooldown_remaining,
        "calibration_next_earliest_retrain_ts": next_earliest_ts,
        "config": {
            "check_interval": CFG.auto_retrain_check_interval,
            "min_interval": CFG.auto_retrain_min_interval,
            "required_consecutive_drifts": CFG.auto_retrain_required_consecutive_drifts,
            "min_samples": CFG.auto_retrain_min_samples,
            "drift_z_threshold": CFG.drift_z_threshold,
            "calibration_retrain_min_interval": CFG.calibration_retrain_min_interval,
        },
    }

__all__ = ["auto_retrain_loop", "auto_retrain_status"]
