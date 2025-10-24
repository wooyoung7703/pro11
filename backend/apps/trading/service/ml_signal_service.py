from __future__ import annotations
import time
from typing import Any, Dict, Optional
from backend.apps.trading.autopilot.models import AutopilotSignal
from backend.apps.trading.autopilot.service import AutopilotService
from backend.apps.trading.repository.signal_repository import TradingSignalRepository
from backend.apps.trading.service.trading_service import get_trading_service
from backend.apps.risk.service.risk_engine import RiskEngine
from backend.common.config.base_config import load_config
from backend.apps.training.training_service import TrainingService
import os

# Lazy cache for env file parsing to avoid repeated disk I/O
_ENV_CACHE: dict[str, str] | None = None

def _repo_root_path() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    # backend/apps/trading/service -> repo root is four levels up
    return os.path.abspath(os.path.join(here, '../../../..'))

def _load_env_files_into_cache() -> dict[str, str]:
    global _ENV_CACHE
    if _ENV_CACHE is not None:
        return _ENV_CACHE
    cache: dict[str, str] = {}
    def _read_file(p: str) -> None:
        try:
            if not os.path.exists(p):
                return
            with open(p, 'r', encoding='utf-8') as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k:
                        cache.setdefault(k, v)
        except Exception:
            pass
    # Priority: .env.private then .env across candidate roots
    candidates: list[str] = []
    try:
        candidates.append(os.path.abspath(os.path.join(os.getcwd(), '.env.private')))
        candidates.append(os.path.abspath(os.path.join(os.getcwd(), '.env')))
    except Exception:
        pass
    try:
        rr = _repo_root_path()
        candidates.append(os.path.join(rr, '.env.private'))
        candidates.append(os.path.join(rr, '.env'))
    except Exception:
        pass
    # Also relative to this file walking up
    here = os.path.abspath(os.path.dirname(__file__))
    for up in ('.','..','../..','../../..','../../../..'):
        candidates.append(os.path.abspath(os.path.join(here, up, '.env.private')))
        candidates.append(os.path.abspath(os.path.join(here, up, '.env')))
    # Dedup while preserving order
    seen = set()
    uniq = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    # Load
    for p in uniq:
        _read_file(p)
    _ENV_CACHE = cache
    return cache

def _get_env_with_fallback(name: str) -> str | None:
    v = os.getenv(name)
    if v is not None and str(v).strip() != '':
        return v
    # fallback to cache from env files
    try:
        cache = _load_env_files_into_cache()
        return cache.get(name)
    except Exception:
        return None

# Module-level last emit timestamps to persist cooldown across request-scoped service instances
_LAST_EMIT_TS: dict[str, float] = {}

class MLSignalService:
    def __init__(self, risk_engine: RiskEngine, autopilot: AutopilotService | None = None):
        self._risk = risk_engine
        self._repo = TradingSignalRepository()
        self._autopilot = autopilot
        self._cfg = load_config()
        # Deprecated: per-instance timestamp (replaced by module-level _LAST_EMIT_TS)
        self._last_emit_ts: float = 0.0

    def _effective_threshold(self) -> float:
        """Resolve the decision threshold with runtime override precedence:
        1) app.state.auto_inference_threshold_override (admin override)
        2) env ML_SIGNAL_THRESHOLD
        3) env INFERENCE_PROB_THRESHOLD
        4) config.ml_signal_threshold
        5) default 0.8
        """
        # 1) Try admin override from FastAPI app state (if available)
        try:
            from backend.apps.api.main import app as _app  # local import to avoid circulars at module import
            thr_ov = getattr(_app.state, 'auto_inference_threshold_override', None)
            if isinstance(thr_ov, (int, float)) and 0 < float(thr_ov) < 1:
                return float(thr_ov)
        except Exception:
            pass
        # 2) Direct env
        try:
            v = _get_env_with_fallback("ML_SIGNAL_THRESHOLD")
            if v is not None and str(v).strip() != "":
                f = float(v)
                if 0 < f < 1:
                    return f
        except Exception:
            pass
        # 3) Fallback env: INFERENCE_PROB_THRESHOLD
        try:
            v = _get_env_with_fallback("INFERENCE_PROB_THRESHOLD")
            if v is not None and str(v).strip() != "":
                f = float(v)
                if 0 < f < 1:
                    return f
        except Exception:
            pass
        # 4) Config
        try:
            f = float(self._cfg.ml_signal_threshold)
            if 0 < f < 1:
                return f
        except Exception:
            pass
        # 5) Default
        return 0.8

    async def _infer(self) -> Dict[str, Any]:
        svc = TrainingService(self._cfg.symbol, self._cfg.kline_interval, self._cfg.model_artifact_dir)
        thr = self._effective_threshold()
        # Directional baseline is deprecated; always use bottom predictor path.
        return await svc.predict_latest_bottom(threshold=thr)

    async def evaluate(self) -> Dict[str, Any]:
        """Return evaluation dict with prob/decision matching TrainingService schema."""
        res = await self._infer()
        now = time.time()
        # Enforce configured threshold at the service layer to ensure consistency,
        # regardless of downstream defaults in TrainingService.
        cfg_thr = self._effective_threshold()
        out: Dict[str, Any] = {
            "status": res.get("status"),
            "probability": res.get("probability"),
            "model_version": res.get("model_version"),
            "feature_close_time": res.get("feature_close_time"),
            "evaluated_at": now,
        }
        # If we have a numeric probability, recompute decision using the configured threshold
        prob = res.get("probability")
        if isinstance(prob, (int, float)):
            decision = 1 if float(prob) >= cfg_thr else -1
            out["decision"] = decision
            out["threshold"] = cfg_thr
        else:
            # Fallback to what downstream provided
            out["decision"] = res.get("decision")
            out["threshold"] = res.get("threshold", cfg_thr)
        return out

    async def trigger(self, *, auto_execute: bool, size: Optional[float] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        evaluation = await self.evaluate()
        evaluation.setdefault("executed", False)
        evaluation.setdefault("reasons", [])
        if evaluation.get("status") != "ok":
            evaluation.setdefault("signal_id", None)
            return evaluation
        decision = int(evaluation.get("decision") or 0)
        if decision <= 0:
            evaluation["triggered"] = False
            return evaluation
        # cooldown (persisted across requests via module-level map)
        # Resolve cooldown with runtime override precedence: app.state override > cfg
        cd = float(self._cfg.ml_signal_cooldown_sec or 0.0)
        try:
            from backend.apps.api.main import app as _app  # local import to avoid circular deps
            _ov = getattr(_app.state, 'ml_signal_cooldown_override', None)
            if isinstance(_ov, (int, float)) and float(_ov) >= 0:
                cd = float(_ov)
        except Exception:
            pass
        now = time.time()
        symbol = self._cfg.symbol
        last_ts = 0.0
        try:
            last_ts = float(_LAST_EMIT_TS.get(symbol, 0.0) or 0.0)
        except Exception:
            last_ts = 0.0
        if cd > 0 and (now - last_ts) < cd:
            evaluation["triggered"] = False
            evaluation["reasons"].append("cooldown")
            return evaluation
        evaluation["triggered"] = True
        price = None
        try:
            from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as _fetch
            recents = await _fetch(symbol, self._cfg.kline_interval, limit=1)
            price = float(recents[-1]['close']) if recents else None
        except Exception:
            price = None
        extra = {"evaluation": {k: evaluation.get(k) for k in ("probability","decision","threshold","model_version","evaluated_at") if evaluation.get(k) is not None}}
        try:
            row = await self._repo.insert_signal(signal_type="ml_buy", status="triggered", params={"symbol":symbol, "interval": self._cfg.kline_interval}, price=price, extra=extra)
            signal_id = (row or {}).get("id")
        except Exception:
            signal_id = None
        evaluation["signal_id"] = signal_id
        # Autopilot activation (always activate signal, even if not auto_execute)
        if self._autopilot:
            try:
                sig = AutopilotSignal(kind="ml_buy", symbol=symbol, confidence=float(evaluation.get("probability") or 0.5), reason=reason or "ml_buy", extra={"metrics": {"prob": evaluation.get("probability"), "threshold": evaluation.get("threshold")}, "evaluation": evaluation, "signal_id": signal_id}, emitted_ts=now)
                await self._autopilot.activate_signal(sig)
            except Exception:
                pass
        if not auto_execute:
            # mark cooldown when a signal is emitted regardless of execution
            try:
                _LAST_EMIT_TS[symbol] = now
            except Exception:
                pass
            return evaluation
        order_size = float(size) if size else float(self._cfg.ml_default_size or 1.0)
        if order_size <= 0:
            evaluation["reasons"].append("invalid_size")
            return evaluation
        trading = get_trading_service(self._risk)
        try:
            res = await trading.submit_market(symbol=symbol, side="buy", size=order_size, price=price or 0.0, reason=reason or "ml_buy")
        except Exception as e:
            evaluation["error"] = str(e)
            evaluation["reasons"].append("submit_error")
            status = "rejected"
            submit_res = {"status": status, "error": str(e)}
        else:
            submit_res = res
        evaluation["order_response"] = submit_res
        evaluation["executed"] = (str(submit_res.get("status") or "").lower() == "filled")
        if signal_id is not None:
            try:
                await self._repo.mark_result(signal_id=signal_id, status="filled" if evaluation["executed"] else str(submit_res.get("status")), order_side="buy", order_size=order_size, order_price=price, error=None if evaluation["executed"] else submit_res.get("error"), extra={"order_response": submit_res})
            except Exception:
                pass
        # Update module-level cooldown timestamp after successful signal handling
        try:
            _LAST_EMIT_TS[symbol] = now
        except Exception:
            pass
        return evaluation

__all__ = ["MLSignalService"]
