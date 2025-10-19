from __future__ import annotations
import time
from typing import Any, Dict, Optional
from backend.common.config.base_config import load_config
import os
from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository
from backend.apps.model_registry.repository.lifecycle_audit_repository import LifecycleAuditRepository
from prometheus_client import Counter

CFG = load_config()

class PromotionState:
    def __init__(self):
        self.last_promotion_ts: Optional[float] = None
        # When True, bypass interval gating so unit tests can isolate specific failure reasons
        self.test_mode: bool = False

PROMOTION_STATE = PromotionState()

PROMOTION_ATTEMPTS = Counter("auto_promotion_attempts_total", "Auto promotion attempts")
PROMOTION_SUCCESS = Counter("auto_promotion_success_total", "Auto promotion successes")

# Bottom-only mode: prefer bottom_predictor; still allow ohlcv_sentiment when explicitly configured
ALT_MODEL_CANDIDATES = ["bottom_predictor", "ohlcv_sentiment_predictor"]

async def promote_if_better(new_model_id: Optional[int], new_metrics: Dict[str, Any]) -> dict:
    """Heuristic auto-promotion:
    - Enabled via AUTO_PROMOTE_ENABLED
    - Compares the new model against the current production within the SAME model family (name)
      determined from the registry row of new_model_id when available.
    - Requires >= auto_promote_min_sample_growth relative to current production samples
    - Enforces minimum interval since last promotion
    Returns decision metadata.
    """
    if (not CFG.auto_promote_enabled and not PROMOTION_STATE.test_mode) or not new_model_id:
        return {"promoted": False, "reason": "disabled_or_invalid"}
    now = time.time()
    if (not PROMOTION_STATE.test_mode) and PROMOTION_STATE.last_promotion_ts and now - PROMOTION_STATE.last_promotion_ts < CFG.auto_promote_min_interval:
        return {"promoted": False, "reason": "interval_not_elapsed"}
    repo = ModelRegistryRepository()
    audit_repo = LifecycleAuditRepository()
    PROMOTION_ATTEMPTS.inc()
    # Prefer to scope comparison to the same family as the new model
    latest = []
    used_name = None
    try:
        new_row = await repo.fetch_by_id(int(new_model_id)) if new_model_id is not None else None
    except Exception:
        new_row = None
    if new_row and new_row.get("name") and new_row.get("model_type"):
        used_name = str(new_row["name"])  # type: ignore[index]
        used_type = str(new_row["model_type"])  # type: ignore[index]
        latest = await repo.fetch_latest(used_name, used_type, limit=10)
    # Fallback: Try configured name and alternates
    if not latest:
        candidate_names = [CFG.auto_promote_model_name] if CFG.auto_promote_model_name else []
        for alt in ALT_MODEL_CANDIDATES:
            if alt not in candidate_names:
                candidate_names.append(alt)
        for name in candidate_names:
            rows = await repo.fetch_latest(name, "supervised", limit=10)
            if rows:
                latest = rows
                used_name = name
                break
    if not latest:
        return {"promoted": False, "reason": "no_existing_models", "candidates": candidate_names}
    prod_samples = None
    prod_model_id = None
    prod_auc = None
    prod_brier = None
    prod_ece = None
    for r in latest:
        if r["status"] == "production":
            prod_samples = (r["metrics"] or {}).get("samples") if r["metrics"] else None
            prod_auc = (r["metrics"] or {}).get("auc") if r["metrics"] else None
            prod_brier = (r["metrics"] or {}).get("brier") if r["metrics"] else None
            prod_ece = (r["metrics"] or {}).get("ece") if r["metrics"] else None
            prod_model_id = r["id"]
            break
    new_samples = new_metrics.get("samples") if new_metrics else None
    if prod_samples is not None and new_samples is not None:
        if new_samples < prod_samples * CFG.auto_promote_min_sample_growth:
            return {"promoted": False, "reason": "insufficient_sample_growth", "prod_samples": prod_samples, "new_samples": new_samples}
    # AUC improvement requirement (if production AUC exists and new has auc)
    new_auc = new_metrics.get("auc") if new_metrics else None
    if prod_auc is not None and new_auc is not None and prod_auc == prod_auc and new_auc == new_auc:  # not NaN
        # relative improvement (new - old)/abs(old)
        if abs(prod_auc) > 0:
            rel_improve = (new_auc - prod_auc) / abs(prod_auc)
        else:
            rel_improve = float("inf") if new_auc > prod_auc else 0.0
        if rel_improve < CFG.auto_promote_min_auc_improve:
            return {"promoted": False, "reason": "insufficient_auc_improvement", "prod_auc": prod_auc, "new_auc": new_auc, "rel_improve": rel_improve}
    # Additional multi-metric gating (Brier/ECE)
    # Lower Brier/ECE is better. Allow small degradation if AUC improves sufficiently.
    new_brier = new_metrics.get("brier") if new_metrics else None
    new_ece = new_metrics.get("ece") if new_metrics else None
    # Thresholds (env override or defaults)
    def _f(name: str, default: float) -> float:
        _v = os.getenv(name, str(default))
        if isinstance(_v, str) and '#' in _v:
            _v = _v.split('#', 1)[0].strip()
        try:
            return float(_v)
        except Exception:
            return default
    max_brier_degradation = _f("PROMOTION_MAX_BRIER_DEGRADATION", 0.01)  # absolute increase allowed
    max_ece_degradation = _f("PROMOTION_MAX_ECE_DEGRADATION", 0.01)      # absolute increase allowed
    require_non_worse_calibration = os.getenv("PROMOTION_REQUIRE_NON_WORSE_CALIBRATION", "false").lower() in ("1","true","yes")
    # Evaluate calibration metrics if incumbent has them
    if prod_brier is not None and new_brier is not None and prod_brier == prod_brier and new_brier == new_brier:
        brier_delta = new_brier - prod_brier  # positive => worse
        if require_non_worse_calibration and brier_delta > 0:
            return {"promoted": False, "reason": "brier_worse_blocked", "prod_brier": prod_brier, "new_brier": new_brier, "delta": brier_delta}
        if brier_delta > max_brier_degradation:
            return {"promoted": False, "reason": "brier_degradation_too_large", "prod_brier": prod_brier, "new_brier": new_brier, "delta": brier_delta, "allowed": max_brier_degradation}
    if prod_ece is not None and new_ece is not None and prod_ece == prod_ece and new_ece == new_ece:
        ece_delta = new_ece - prod_ece
        if require_non_worse_calibration and ece_delta > 0:
            return {"promoted": False, "reason": "ece_worse_blocked", "prod_ece": prod_ece, "new_ece": new_ece, "delta": ece_delta}
        if ece_delta > max_ece_degradation:
            return {"promoted": False, "reason": "ece_degradation_too_large", "prod_ece": prod_ece, "new_ece": new_ece, "delta": ece_delta, "allowed": max_ece_degradation}
    # Promote
    row = await repo.promote(new_model_id)
    if row:
        PROMOTION_SUCCESS.inc()
        PROMOTION_STATE.last_promotion_ts = now
        # Audit (best-effort)
        try:
            await audit_repo.log_promotion(
                model_id=new_model_id,
                previous_production_model_id=prod_model_id,
                decision="promoted",
                reason="promotion_success",
                samples_old=prod_samples,
                samples_new=new_samples,
            )
        except Exception:
            # Do not fail promotion on audit logging problem
            pass
        return {"promoted": True, "model_id": new_model_id, "prev_prod_id": prod_model_id}
    return {"promoted": False, "reason": "promotion_call_failed", "model_name": used_name}

__all__ = ["promote_if_better", "PROMOTION_STATE"]
