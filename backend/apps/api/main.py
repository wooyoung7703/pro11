from __future__ import annotations
import asyncio
import random
import os
import math
import secrets
import time
import logging
from contextlib import asynccontextmanager
import contextlib
from typing import Optional, Sequence
from typing import TYPE_CHECKING

# Allow Prometheus metrics to be redefined safely during test imports
os.environ.setdefault("PROMETHEUS_DISABLE_CREATED_SERIES_CHECK", "True")
# Ensure python-multipart backend is present early to avoid PendingDeprecationWarning
try:  # pragma: no cover - best effort import
    import python_multipart  # type: ignore  # noqa: F401
except Exception:  # pragma: no cover
    pass
from fastapi import FastAPI, Depends, Header, HTTPException, status, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import Response, PlainTextResponse
from backend.common.config.base_config import load_config, reload_env_from_files
from backend.common.db.connection import init_pool, close_pool, pool_status, force_pool_retry, ensure_pool_background
from backend.apps.features.service.feature_service import FeatureService, feature_scheduler
from backend.apps.ingestion.ws.kline_consumer import KlineConsumer
from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository
from backend.apps.model_registry.service.artifact_cleanup import ArtifactCleanupService, periodic_artifact_cleanup
from backend.apps.model_registry.repository.lifecycle_audit_repository import LifecycleAuditRepository
from backend.apps.risk.service.risk_engine import RiskEngine, RiskLimits
from backend.apps.training.training_service import TrainingService
from backend.apps.training.auto_retrain_scheduler import auto_retrain_loop, auto_retrain_status
from backend.apps.training.repository.training_job_repository import TrainingJobRepository
from backend.apps.training.sentiment_mode import is_enabled as sentiment_enabled, status_dict as sentiment_status, enable as sentiment_enable, disable as sentiment_disable
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST, Counter as _PromCounter, Histogram as _PromHistogram, Gauge as _PromGauge, REGISTRY
from .metrics_seed import SEED_AUTO_SEED_TOTAL
import httpx
from backend.apps.news.service.news_service import NewsService
from backend.apps.news.repository.news_repository import NewsRepository
from backend.apps.training.repository.inference_log_repository import InferenceLogRepository
from backend.apps.training.service.inference_log_queue import get_inference_log_queue
from backend.apps.ingestion.backfill.gap_backfill_service import GapBackfillService  # gap backfill
from backend.apps.ingestion.backfill.year_backfill_service import start_year_backfill, get_year_backfill_status, cancel_year_backfill
from backend.common.db.schema_manager import ensure_all as ensure_all_schema
from backend.apps.trading.autopilot import AutopilotService
from backend.apps.trading.autopilot.models import AutopilotMode
from pathlib import Path
from backend.common.repository.settings_repository import SettingsRepository
from backend.apps.api.auth import require_api_key

# UI settings defaults (for convenience: avoid 404 for first-time reads)
UI_DEFAULTS: dict[str, object] = {
    # Model metrics panel
    "model_metrics_auto": True,
    "model_metrics_interval": 15,
    # Dashboard auto-orchestration prefs
    "dashboard.auto.LA": "30,40,60",
    "dashboard.auto.DD": "0.0075,0.01,0.015",
    "dashboard.auto.RB": "0.004,0.006,0.008",
    "dashboard.auto.posMin": 0.08,
    "dashboard.auto.posMax": 0.4,
    "dashboard.auto.backfillTarget": 600,
    "dashboard.auto.minInserted": 300,
    "dashboard.auto.modelWaitSec": 60,
    # Feature drift view prefs (v2)
    "feature_drift_prefs_v2": {
        "window": 200,
        "threshold": 3.0,
        "auto": True,
        "intervalSec": 60,
    },
    # OHLCV controls (admin UI)
    "ohlcv.interval": "1m",
    "ohlcv.limit": 500,
}

# Live trading defaults (served for reads if unset in DB to avoid noisy 404s in Admin UI)
LIVE_DEFAULTS: dict[str, object] = {
    "enabled": False,
    "cooldown_sec": 60.0,
    "base_size": 1.0,
    "trailing_take_profit_pct": 0.0,
    "max_holding_seconds": 0,
}

# Calibration defaults (DB-backed admin settings)
CALIB_DEFAULTS: dict[str, object] = {
    "live.window_seconds": 3600,
    "live.bins": 10,
    # Eager labeling assist when calibration window is empty
    "eager.enabled": True,
    "eager.limit": 500,
    "eager.min_age_seconds": 120,
    # Monitor thresholds for alerting/drift streaks
    "monitor.ece_abs": 0.05,
    "monitor.ece_rel": 0.5,
}

# Drift defaults (DB-backed admin settings)
DRIFT_DEFAULTS: dict[str, object] = {
    "window": 200,
    "threshold": 3.0,
    # Comma-separated feature list used by /api/features/drift/scan when not provided
    "features": "ret_1,ret_5,ret_10,rsi_14,rolling_vol_20,ma_20,ma_50",
    # Optional auto-scan loop (UI may use these)
    "auto.enabled": True,
    "auto.interval_sec": 60,
}

if TYPE_CHECKING:  # for type checkers only; avoids runtime import cycles
    from backend.apps.trading.service.low_buy_service import LowBuyService


def _get_or_create_metric(metric_cls, name: str, documentation: str, *, labelnames: Optional[Sequence[str]] = None, **kwargs):
    labels = tuple(labelnames or ())
    try:
        return metric_cls(name, documentation, labelnames=labels, **kwargs)
    except ValueError as exc:
        if "Duplicated timeseries" not in str(exc):
            raise
        existing = None
        if hasattr(REGISTRY, "_names_to_collectors"):
            existing = REGISTRY._names_to_collectors.get(name)
        if existing is not None:
            return existing  # type: ignore[return-value]
        raise


def Counter(name: str, documentation: str, labelnames: Optional[Sequence[str]] = None, **kwargs):
    return _get_or_create_metric(_PromCounter, name, documentation, labelnames=labelnames, **kwargs)


def Gauge(name: str, documentation: str, labelnames: Optional[Sequence[str]] = None, **kwargs):
    return _get_or_create_metric(_PromGauge, name, documentation, labelnames=labelnames, **kwargs)


def Histogram(name: str, documentation: str, labelnames: Optional[Sequence[str]] = None, **kwargs):
    return _get_or_create_metric(_PromHistogram, name, documentation, labelnames=labelnames, **kwargs)


GAP_PERSIST_LOAD_TOTAL = Counter("kline_gap_persist_load_total", "Number of persisted gap load operations", ["symbol"])
GAP_PERSIST_LOAD_ERRORS_TOTAL = Counter("kline_gap_persist_load_errors_total", "Errors while loading persisted gaps", ["symbol"])
GAP_PERSIST_SAVE_ERRORS_TOTAL = Counter("kline_gap_persist_save_errors_total", "Errors while saving new gap segments", ["symbol"])

# Auto inference loop metrics
AUTO_INFER_RUNS = Counter("inference_auto_runs_total", "Auto inference loop run count")
AUTO_INFER_ERRORS = Counter("inference_auto_errors_total", "Auto inference loop errors")
AUTO_INFER_LAST_SUCCESS = Gauge("inference_auto_last_success_timestamp", "Last successful auto inference run (unix ts)")
AUTO_INFER_LAST_ERROR = Gauge("inference_auto_last_error_timestamp", "Last errored auto inference run (unix ts)")

# --- Runtime setting application helpers --------------------------------------
async def apply_runtime_setting(key: Optional[str], value: Optional[object]) -> bool:
    """Apply a DB-backed setting to the running process.

    Recognized keys (namespaced):
      - inference.auto.threshold (float 0..1)
      - labeler.interval (seconds, float)
      - labeler.min_age_seconds (int)
      - labeler.batch_limit (int)
      - labeler.bottom.lookahead (int)
      - labeler.bottom.drawdown (float)
      - labeler.bottom.rebound (float)
      - live_trading.enabled (bool)
      - live_trading.cooldown_sec (float)
      - live_trading.base_size (float)
    Returns True if applied, False if unknown key or invalid value.
    """
    if not key:
        return False
    k = str(key).strip().lower()
    try:
        # Labeler runtime: delegate to service
        if k.startswith("labeler."):
            from backend.apps.training.service.auto_labeler import get_auto_labeler_service
            svc = get_auto_labeler_service()
            if k == "labeler.interval":
                try:
                    svc.apply_runtime_config(interval=float(value))
                    return True
                except Exception:
                    return False
            if k == "labeler.min_age_seconds":
                try:
                    svc.apply_runtime_config(min_age_seconds=int(value))
                    return True
                except Exception:
                    return False
            if k == "labeler.batch_limit":
                try:
                    svc.apply_runtime_config(batch_limit=int(value))
                    return True
                except Exception:
                    return False
            if k == "labeler.bottom.lookahead":
                try:
                    svc.apply_runtime_config(lookahead=int(value))
                    return True
                except Exception:
                    return False
            if k == "labeler.bottom.drawdown":
                try:
                    svc.apply_runtime_config(drawdown=float(value))
                    return True
                except Exception:
                    return False
            if k == "labeler.bottom.rebound":
                try:
                    svc.apply_runtime_config(rebound=float(value))
                    return True
                except Exception:
                    return False
        # Inference auto threshold override
        if k == "inference.auto.threshold":
            try:
                v = float(value)
                if 0 < v < 1:
                    app.state.auto_inference_threshold_override = v
                    return True
            except Exception:
                return False
            return False
        # Live trading toggles
        if k == "live_trading.enabled":
            try:
                app.state.live_trading_enabled = bool(value)
                return True
            except Exception:
                return False
        if k == "live_trading.cooldown_sec":
            try:
                app.state.live_trading_cooldown_sec = float(value)  # used at call-site as cooldown seconds
                return True
            except Exception:
                return False
        if k == "live_trading.base_size":
            try:
                app.state.live_trading_base_size = float(value)
                return True
            except Exception:
                return False
        if k == "live_trading.trailing_take_profit_pct":
            try:
                app.state.live_trailing_take_profit_pct = float(value)
                return True
            except Exception:
                return False
        if k == "live_trading.max_holding_seconds":
            try:
                app.state.live_max_holding_seconds = int(value)
                return True
            except Exception:
                return False
        # Risk limits (applied in-memory; also re-applied on startup from DB)
        if k.startswith("risk."):
            try:
                v = float(value)
            except Exception:
                return False
            try:
                if k == "risk.max_notional":
                    risk_engine.limits.max_notional = v
                    return True
                if k == "risk.max_daily_loss":
                    risk_engine.limits.max_daily_loss = v
                    return True
                if k == "risk.max_drawdown":
                    risk_engine.limits.max_drawdown = v
                    return True
                if k == "risk.atr_multiple":
                    risk_engine.limits.atr_multiple = v
                    return True
            except Exception:
                return False
        # Calibration runtime overrides
        if k == "calibration.live.window_seconds":
            try:
                app.state.calibration_live_window_seconds = int(value)
                return True
            except Exception:
                return False
        if k == "calibration.live.bins":
            try:
                app.state.calibration_live_bins = int(value)
                return True
            except Exception:
                return False
        if k == "calibration.eager.enabled":
            try:
                app.state.calibration_eager_enabled = bool(value)
                return True
            except Exception:
                return False
        if k == "calibration.eager.limit":
            try:
                app.state.calibration_eager_limit = int(value)
                return True
            except Exception:
                return False
        if k == "calibration.eager.min_age_seconds":
            try:
                app.state.calibration_eager_min_age_seconds = int(value)
                return True
            except Exception:
                return False
        if k == "calibration.monitor.ece_abs":
            try:
                app.state.calibration_monitor_ece_abs = float(value)
                return True
            except Exception:
                return False
        if k == "calibration.monitor.ece_rel":
            try:
                app.state.calibration_monitor_ece_rel = float(value)
                return True
            except Exception:
                return False
        # Drift runtime overrides
        if k == "drift.window":
            try:
                app.state.drift_window = int(value)
                return True
            except Exception:
                return False
        if k == "drift.threshold":
            try:
                app.state.drift_threshold = float(value)
                return True
            except Exception:
                return False
        if k == "drift.features":
            try:
                # Expect comma-separated string
                app.state.drift_features = str(value)
                return True
            except Exception:
                return False
        # ML signal cooldown (seconds) runtime override
        # Allows DB-admin to tune cooldown without restart
        if k in ("ml.signal.cooldown_sec", "ml_signal_cooldown_sec"):
            try:
                v = float(value)
                if v >= 0:
                    app.state.ml_signal_cooldown_override = v
                    # Also set env for any config reload paths
                    try:
                        os.environ["ML_SIGNAL_COOLDOWN_SEC"] = str(v)
                    except Exception:
                        pass
                    return True
            except Exception:
                return False
        # Training thresholds and caps (runtime overrides for faster iteration/demo)
        # These mutate the module-level config instance used by TrainingService (backend.apps.training.training_service.CFG)
        # and set process env so freshly loaded configs (e.g., preview endpoints) observe the change.
        if k == "training.bottom.min_train_labels":
            try:
                import backend.apps.training.training_service as _ts  # local import to avoid circulars
                v = int(value)  # type: ignore[arg-type]
                if v > 0:
                    setattr(_ts.CFG, "bottom_min_train_labels", int(v))
                    # No canonical env var exists for min-train; keep CFG-only
                    return True
            except Exception:
                return False
        if k == "training.bottom.min_labels":
            try:
                import backend.apps.training.training_service as _ts  # local import
                v = int(value)  # type: ignore[arg-type]
                if v > 0:
                    _ts.CFG.bottom_min_labels = int(v)  # promotion threshold used by services that read CFG
                    # Also set env so any fresh load_config() reflects this value
                    os.environ["BOTTOM_MIN_LABELS"] = str(int(v))
                    return True
            except Exception:
                return False
        if k == "training.bottom.ohlcv_fetch_cap":
            try:
                import backend.apps.training.training_service as _ts  # local import
                v = int(value)  # type: ignore[arg-type]
                if v >= 300:
                    setattr(_ts.CFG, "bottom_ohlcv_fetch_cap", int(v))
                    os.environ["BOTTOM_OHLCV_FETCH_CAP"] = str(int(v))
                    return True
            except Exception:
                return False
    except Exception:
        return False
    return False

async def auto_inference_loop(*, interval_override: Optional[float] = None):
    """Background auto inference loop.

    interval_override: if supplied ( > 0 ), use this interval instead of config.
    We re-read config each iteration only for non-overridden case to allow env reload on restart, not hot.
    """
    cfg = load_config()
    base_interval = cfg.inference_auto_loop_interval
    interval = max(1.0, interval_override if (isinstance(interval_override, (int,float)) and interval_override and interval_override > 0) else base_interval)
    svc = _training_service()
    log_queue = get_inference_log_queue()
    while True:  # cancelled on shutdown lifespan.
        start = time.time()
        try:
            # Allow runtime threshold override for auto loop
            thr_override = getattr(app.state, 'auto_inference_threshold_override', None)
            thr_use = None
            if isinstance(thr_override, (int, float)) and 0 < float(thr_override) < 1:
                thr_use = float(thr_override)
            # Bottom-only predictor
            if hasattr(svc, 'predict_latest_bottom'):
                res = await svc.predict_latest_bottom(threshold=thr_use)  # type: ignore[attr-defined]
            else:
                res = await svc.predict_latest(threshold=thr_use)
            _target_tag = 'bottom'
            status = res.get("status")
            # Only log successful inference decisions with probability
            if status == "ok" and isinstance(res.get("probability"), (int,float)):
                try:
                    # Use model family name consistent with target (avoid env-config mismatch)
                    _model_name = 'bottom_predictor'
                    enq_ok = await log_queue.enqueue({
                        "symbol": cfg.symbol,
                        "interval": cfg.kline_interval,
                        "model_name": _model_name,
                        "model_version": str(res.get("model_version")),
                        "probability": float(res.get("probability")),
                        "decision": int(res.get("decision")),
                        "threshold": float(res.get("threshold")),
                        "production": bool(res.get("used_production")),
                        "extra": {"auto_loop": True, "target": _target_tag},
                    })
                    if not enq_ok:
                        # Fallback: synchronous insert
                        repo = InferenceLogRepository()
                        with contextlib.suppress(Exception):
                            await repo.bulk_insert([
                                {
                                    "symbol": cfg.symbol,
                                    "interval": cfg.kline_interval,
                                    "model_name": _model_name,
                                    "model_version": str(res.get("model_version")),
                                    "probability": float(res.get("probability")),
                                    "decision": int(res.get("decision")),
                                    "threshold": float(res.get("threshold")),
                                    "production": bool(res.get("used_production")),
                                    "extra": {"auto_loop": True, "fallback": True, "target": _target_tag},
                                }
                            ])
                except Exception:
                    pass
            # Live trading bridge (optional)
            try:
                if getattr(app.state, 'live_trading_enabled', False) and status == "ok":
                    decision = res.get("decision")
                    # long-only simple rule: buy on decision=1, flat on decision=0
                    symbol = cfg.symbol; interval = cfg.kline_interval
                    now = time.time()
                    cooldown = float(getattr(app.state, 'live_trading_cooldown_sec', 60))
                    last_ts = float(getattr(app.state, 'live_trading_last_ts', 0.0))
                    # Always fetch current price once per loop
                    try:
                        recents = await _ohlcv_fetch_recent(symbol, interval, limit=1)
                        cur_price = float(recents[-1]['close']) if recents else None
                    except Exception:
                        cur_price = None
                    if cur_price:
                        svc_trade = get_trading_service(risk_engine)
                        base_size = float(getattr(app.state, 'live_trading_base_size', 1.0))
                        # current position size for symbol
                        pos_size = float(risk_engine.positions.get(symbol).size) if symbol in risk_engine.positions else 0.0
                        # Entry honors global cooldown; Exit may bypass cooldown for safety
                        can_use_cooldown = (now - last_ts) >= cooldown
                        forced_exit_done = False
                        # Forced exits: trailing TP and max holding (independent of decision)
                        try:
                            if pos_size > 0:
                                # Update trailing peak state
                                trail_map = getattr(app.state, 'live_trailing', {})
                                st = trail_map.get(symbol)
                                if not st:
                                    st = {"peak": float(cur_price), "entry_ts": float(now)}
                                else:
                                    try:
                                        if float(cur_price) > float(st.get("peak", 0.0)):
                                            st["peak"] = float(cur_price)
                                    except Exception:
                                        st["peak"] = float(cur_price)
                                trail_map[symbol] = st
                                app.state.live_trailing = trail_map

                                trailing_pct = float(getattr(app.state, 'live_trailing_take_profit_pct', 0.0) or 0.0)
                                max_hold_sec = int(getattr(app.state, 'live_max_holding_seconds', 0) or 0)

                                # Compute breakeven for ROI>0 guard used by trailing TP
                                try:
                                    fee_mode = (cfg.trading_fee_mode or 'taker').lower()
                                    fee_rate = float(cfg.trading_fee_taker) if fee_mode == 'taker' else float(cfg.trading_fee_maker)
                                except Exception:
                                    fee_rate = 0.001
                                try:
                                    entry_price = float(risk_engine.positions.get(symbol).entry_price) if symbol in risk_engine.positions else None
                                except Exception:
                                    entry_price = None
                                breakeven = None
                                if isinstance(entry_price, (int,float)) and entry_price > 0:
                                    breakeven = entry_price * (1.0 + 2.0 * max(0.0, fee_rate))

                                # Trailing TP
                                if not forced_exit_done and trailing_pct > 0 and st.get('peak', 0) > 0 and breakeven is not None and float(cur_price) >= breakeven:
                                    try:
                                        peak = float(st.get('peak', float(cur_price)))
                                        pullback = (peak - float(cur_price)) / peak if peak > 0 else 0.0
                                    except Exception:
                                        pullback = 0.0
                                    if pullback >= trailing_pct:
                                        try:
                                            # Mark exit signal timestamp for scale-in freeze semantics
                                            app.state.live_last_exit_signal_ts = now
                                        except Exception:
                                            pass
                                        slice_sec = int(getattr(app.state, 'exit_slice_seconds', 0) or 0)
                                        if slice_sec <= 0:
                                            await svc_trade.submit_market(symbol=symbol, side="sell", size=pos_size, price=cur_price)
                                        else:
                                            parts = 3
                                            slice_size = pos_size / parts
                                            for i in range(parts):
                                                await svc_trade.submit_market(symbol=symbol, side="sell", size=slice_size if i < parts - 1 else (pos_size - slice_size*(parts-1)), price=cur_price)
                                                if i < parts - 1:
                                                    await asyncio.sleep(slice_sec)
                                        app.state.live_trading_last_ts = now
                                        forced_exit_done = True
                                        # cleanup state on exit
                                        try:
                                            scale_map = getattr(app.state, 'live_scale_in', {})
                                            if symbol in scale_map:
                                                del scale_map[symbol]
                                            app.state.live_scale_in = scale_map
                                        except Exception:
                                            pass
                                        try:
                                            trail_map = getattr(app.state, 'live_trailing', {})
                                            if symbol in trail_map:
                                                del trail_map[symbol]
                                            app.state.live_trailing = trail_map
                                        except Exception:
                                            pass

                                # Max holding seconds
                                if not forced_exit_done and max_hold_sec > 0:
                                    try:
                                        entry_ts = float(st.get('entry_ts', now))
                                    except Exception:
                                        entry_ts = now
                                    if (now - entry_ts) >= max_hold_sec:
                                        try:
                                            app.state.live_last_exit_signal_ts = now
                                        except Exception:
                                            pass
                                        slice_sec = int(getattr(app.state, 'exit_slice_seconds', 0) or 0)
                                        if slice_sec <= 0:
                                            await svc_trade.submit_market(symbol=symbol, side="sell", size=pos_size, price=cur_price)
                                        else:
                                            parts = 3
                                            slice_size = pos_size / parts
                                            for i in range(parts):
                                                await svc_trade.submit_market(symbol=symbol, side="sell", size=slice_size if i < parts - 1 else (pos_size - slice_size*(parts-1)), price=cur_price)
                                                if i < parts - 1:
                                                    await asyncio.sleep(slice_sec)
                                        app.state.live_trading_last_ts = now
                                        forced_exit_done = True
                                        try:
                                            scale_map = getattr(app.state, 'live_scale_in', {})
                                            if symbol in scale_map:
                                                del scale_map[symbol]
                                            app.state.live_scale_in = scale_map
                                        except Exception:
                                            pass
                                        try:
                                            trail_map = getattr(app.state, 'live_trailing', {})
                                            if symbol in trail_map:
                                                del trail_map[symbol]
                                            app.state.live_trailing = trail_map
                                        except Exception:
                                            pass
                        except Exception:
                            # keep loop stable on any forced-exit computation error
                            pass
                        # ENTRY
                        if (decision == 1) and (pos_size <= 0) and can_use_cooldown:
                            try:
                                res_entry = await svc_trade.submit_market(symbol=symbol, side="buy", size=base_size, price=cur_price)
                                # Update cooldown only if order filled
                                try:
                                    if isinstance(res_entry, dict) and str(res_entry.get("status")) == "filled":
                                        app.state.live_trading_last_ts = now
                                        # reset scale-in state for new position
                                        try:
                                            scale_map = getattr(app.state, 'live_scale_in', {})
                                            # Initialize last_ts to now so first scale-in also respects si_cooldown
                                            # Anchor price set to current price so price gate measures from here
                                            scale_map[symbol] = {"legs_used": 0, "last_ts": float(now), "anchor_price": float(cur_price)}
                                            app.state.live_scale_in = scale_map
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                            except Exception:
                                # swallow any transient submit errors to keep loop stable
                                pass
                        # EXIT (bypass cooldown if configured)
                        # Policy: even when model says HOLD, if net-profit is satisfied, allow exit (configurable)
                        # And when model suggests exit, still require net-profit if configured
                        try:
                            entry_price_for_exit = float(risk_engine.positions.get(symbol).entry_price) if symbol in risk_engine.positions else None
                        except Exception:
                            entry_price_for_exit = None
                        try:
                            fee_mode2 = (cfg.trading_fee_mode or 'taker').lower()
                            fee_rate2 = float(cfg.trading_fee_taker) if fee_mode2 == 'taker' else float(cfg.trading_fee_maker)
                        except Exception:
                            fee_rate2 = 0.001
                        # Profit check mode: 'net' uses breakeven incl. fees; 'gross' uses entry price
                        profit_mode = str(getattr(app.state, 'exit_profit_check_mode', 'net')).lower()
                        breakeven2 = None
                        if isinstance(entry_price_for_exit, (int, float)) and entry_price_for_exit > 0:
                            if profit_mode == 'gross':
                                breakeven2 = float(entry_price_for_exit)
                            else:
                                breakeven2 = float(entry_price_for_exit) * (1.0 + 2.0 * max(0.0, fee_rate2))
                        # Force-exit toggle: when net profit is positive, close unconditionally
                        exit_force_on_net = bool(getattr(app.state, 'exit_force_on_net_profit', True))
                        breakeven_known_force = isinstance(breakeven2, (int, float)) and float(breakeven2) > 0
                        net_profit_positive = False
                        try:
                            if breakeven_known_force and isinstance(cur_price, (int, float)):
                                net_profit_positive = float(cur_price) >= float(breakeven2)
                        except Exception:
                            net_profit_positive = False
                        if (pos_size > 0) and (not forced_exit_done) and exit_force_on_net and net_profit_positive:
                            try:
                                app.state.live_last_exit_signal_ts = now
                            except Exception:
                                pass
                            slice_sec_fx = int(getattr(app.state, 'exit_slice_seconds', 0) or 0)
                            if slice_sec_fx <= 0:
                                await svc_trade.submit_market(
                                    symbol=symbol,
                                    side="sell",
                                    size=pos_size,
                                    price=cur_price,
                                    reason=("exit_force_profit_gross" if profit_mode == 'gross' else "exit_force_profit_net")
                                )
                            else:
                                parts = 3
                                slice_size = pos_size / parts
                                for i in range(parts):
                                    await svc_trade.submit_market(
                                        symbol=symbol,
                                        side="sell",
                                        size=slice_size if i < parts - 1 else (pos_size - slice_size*(parts-1)),
                                        price=cur_price,
                                        reason=("exit_force_profit_gross" if profit_mode == 'gross' else "exit_force_profit_net")
                                    )
                                    if i < parts - 1:
                                        await asyncio.sleep(slice_sec_fx)
                            app.state.live_trading_last_ts = now
                            forced_exit_done = True
                            # cleanup state on exit
                            try:
                                scale_map = getattr(app.state, 'live_scale_in', {})
                                if symbol in scale_map:
                                    del scale_map[symbol]
                                app.state.live_scale_in = scale_map
                            except Exception:
                                pass
                            try:
                                trail_map = getattr(app.state, 'live_trailing', {})
                                if symbol in trail_map:
                                    del trail_map[symbol]
                                app.state.live_trailing = trail_map
                            except Exception:
                                pass
                        # Config flags
                        exit_allow_on_hold = bool(getattr(app.state, 'exit_allow_profit_take_on_hold', True))
                        exit_bypass_cd = bool(getattr(app.state, 'exit_bypass_cooldown', True))
                        require_net = bool(getattr(app.state, 'exit_require_net_profit', True))
                        # Net/Gross profit check based on configured mode
                        breakeven_known = isinstance(breakeven2, (int, float)) and float(breakeven2) > 0
                        net_profit_ok2 = True
                        if require_net:
                            try:
                                if breakeven_known:
                                    net_profit_ok2 = isinstance(cur_price, (int, float)) and float(cur_price) >= float(breakeven2)
                                else:
                                    # Breakeven unknown: allow decision-driven EXIT/FLAT to avoid getting stuck; HOLD+profit path remains guarded
                                    net_profit_ok2 = False
                            except Exception:
                                net_profit_ok2 = False
                        should_exit_by_signal = (decision in (0, -1))
                        # New: treat HOLD+net-profit as its own exit path that ignores cooldown
                        should_exit_on_hold_profit = bool(exit_allow_on_hold and net_profit_ok2)
                        should_consider_exit = (should_exit_by_signal or should_exit_on_hold_profit)
                        if (pos_size > 0) and (not forced_exit_done) and should_consider_exit:
                            try:
                                # mark last exit signal time for scale-in freeze semantics
                                try:
                                    app.state.live_last_exit_signal_ts = now
                                except Exception:
                                    pass
                                # If exiting due to HOLD+net-profit, bypass cooldown unconditionally (safety/lock-in)
                                if should_exit_on_hold_profit or can_use_cooldown or exit_bypass_cd:
                                    # Optional guard: only exit if net profit after fees is positive
                                    # Relaxation: if breakeven unknown, allow decision-driven EXIT/FLAT anyway to prevent deadlock
                                    if require_net and not net_profit_ok2 and not (should_exit_by_signal and not breakeven_known):
                                        raise Exception('exit_skipped_net_profit_guard')
                                    # Exit slicing support
                                    slice_sec = int(getattr(app.state, 'exit_slice_seconds', 0) or 0)
                                    exit_filled_any = False
                                    # Determine reason tag for auditing
                                    exit_reason = None
                                    if should_exit_on_hold_profit and not should_exit_by_signal:
                                        exit_reason = "exit_hold_profit"
                                    elif should_exit_by_signal and not breakeven_known and require_net:
                                        exit_reason = "exit_signal_no_breakeven"
                                    if slice_sec <= 0:
                                        res_exit = await svc_trade.submit_market(symbol=symbol, side="sell", size=pos_size, price=cur_price, reason=exit_reason)
                                        try:
                                            exit_filled_any = bool(isinstance(res_exit, dict) and str(res_exit.get("status")) == "filled")
                                        except Exception:
                                            exit_filled_any = False
                                    else:
                                        # Split into up to 3 slices to reduce slippage impact
                                        parts = 3
                                        slice_size = pos_size / parts
                                        for i in range(parts):
                                            res_exit = await svc_trade.submit_market(symbol=symbol, side="sell", size=slice_size if i < parts - 1 else (pos_size - slice_size*(parts-1)), price=cur_price, reason=exit_reason)
                                            try:
                                                if isinstance(res_exit, dict) and str(res_exit.get("status")) == "filled":
                                                    exit_filled_any = True
                                            except Exception:
                                                pass
                                            if i < parts - 1:
                                                await asyncio.sleep(slice_sec)
                                    if exit_filled_any:
                                        app.state.live_trading_last_ts = now
                                    # clear scale-in state on close
                                    try:
                                        scale_map = getattr(app.state, 'live_scale_in', {})
                                        if symbol in scale_map:
                                            del scale_map[symbol]
                                        app.state.live_scale_in = scale_map
                                    except Exception:
                                        pass
                            except Exception:
                                # suppress guard and fill errors; loop continues
                                pass
                        # Scale-in honors its own cooldown (independent of global cooldown)
                        if decision == 1 and pos_size > 0:
                            # Consider scale-in
                            try:
                                allow_scale = bool(getattr(app.state, 'live_allow_scale_in', False))
                                if allow_scale:
                                    # Optional freeze-on-exit: if previous inference suggested exit/flat, block scale-in
                                    try:
                                        if bool(getattr(app.state, 'live_scale_in_freeze_on_exit', True)):
                                            last_exit_sig = float(getattr(app.state, 'live_last_exit_signal_ts', 0.0) or 0.0)
                                            si_cool = float(getattr(app.state, 'live_scale_in_cooldown_sec', getattr(app.state, 'live_trading_cooldown_sec', 60)))
                                            if last_exit_sig > 0 and (time.time() - last_exit_sig) < si_cool:
                                                raise Exception('scalein_frozen_after_exit_signal')
                                    except Exception:
                                        pass
                                    max_legs = int(getattr(app.state, 'live_scale_in_max_legs', 0))
                                    ratio = float(getattr(app.state, 'live_scale_in_size_ratio', 1.0))
                                    # separate cooldown for scale-in (fallback to general cooldown)
                                    si_cool = float(getattr(app.state, 'live_scale_in_cooldown_sec', cooldown))
                                    # gates: price and optional probability delta
                                    min_drop = float(getattr(app.state, 'live_scale_in_min_price_move', 0.0))  # relative drop e.g., 0.005 => 0.5%
                                    prob_gate = float(getattr(app.state, 'live_scale_in_prob_delta_gate', 0.0))
                                    gate_mode = str(getattr(app.state, 'live_scale_in_gate_mode', 'or')).lower()
                                    # scale-in state
                                    scale_map = getattr(app.state, 'live_scale_in', {})
                                    st = scale_map.get(symbol, {"legs_used": 0, "last_ts": 0.0})
                                    legs_used = int(st.get("legs_used", 0))
                                    last_si_ts = float(st.get("last_ts", 0.0))
                                    if legs_used < max_legs and (now - last_si_ts) >= si_cool:
                                        # compute conditions
                                        entry_price = float(risk_engine.positions.get(symbol).entry_price) if symbol in risk_engine.positions else None
                                        # Use anchor price for price gate if available; fallback to entry_price
                                        try:
                                            anchor_price = float(st.get("anchor_price")) if st.get("anchor_price") is not None else None
                                        except Exception:
                                            anchor_price = None
                                        ref_price = anchor_price if isinstance(anchor_price, (int,float)) and anchor_price > 0 else entry_price
                                        # Price gate: min_drop <= 0 disables price gate (auto-pass)
                                        gate_price_ok = True
                                        if isinstance(ref_price, (int,float)) and isinstance(cur_price, (int,float)) and ref_price > 0:
                                            try:
                                                drop = (float(ref_price) - float(cur_price)) / float(ref_price)
                                                gate_price_ok = True if min_drop <= 0 else (drop >= min_drop)
                                            except Exception:
                                                gate_price_ok = (min_drop <= 0)
                                        else:
                                            # If we cannot compute, only pass if gate disabled
                                            gate_price_ok = (min_drop <= 0)
                                        # Probability delta gate
                                        gate_prob_ok = True
                                        try:
                                            # latest decision/delta computed above in loop
                                            delta = None
                                            if isinstance(res, dict) and isinstance(res.get('probability'), (int,float)) and isinstance(res.get('threshold'), (int,float)):
                                                delta = float(res['probability']) - float(res['threshold'])
                                            if isinstance(prob_gate, (int,float)) and prob_gate > 0 and isinstance(delta, (int,float)):
                                                gate_prob_ok = (delta >= float(prob_gate))
                                        except Exception:
                                            gate_prob_ok = (prob_gate <= 0)
                                        # Combine
                                        active = []
                                        if min_drop > 0:
                                            active.append(gate_price_ok)
                                        if isinstance(prob_gate, (int,float)) and prob_gate > 0:
                                            active.append(gate_prob_ok)
                                        gate_any_ok = True if not active else (all(active) if gate_mode == 'and' else any(active))
                                        if gate_any_ok:
                                            size_in = max(0.0, base_size * ratio)
                                            if size_in > 0:
                                                try:
                                                    reason = f"scale_in_leg:{legs_used+1}/{max_legs}"
                                                    await svc_trade.submit_market(symbol=symbol, side="buy", size=size_in, price=cur_price, reason=reason)
                                                    # Reset price gate anchor to current price to avoid immediate back-to-back adds
                                                    st = {"legs_used": legs_used + 1, "last_ts": now, "anchor_price": float(cur_price)}
                                                    scale_map[symbol] = st
                                                    app.state.live_scale_in = scale_map
                                                except Exception:
                                                    pass
                            except Exception:
                                pass
            except Exception:
                # isolate trading errors from inference loop stability
                pass
            AUTO_INFER_RUNS.inc()
            AUTO_INFER_LAST_SUCCESS.set(time.time())
        except asyncio.CancelledError:  # graceful shutdown
            raise
        except Exception:
            AUTO_INFER_ERRORS.inc()
            AUTO_INFER_LAST_ERROR.set(time.time())
        # Sleep remaining time (simple fixed interval)
        elapsed = time.time() - start
        await asyncio.sleep(max(0.0, interval - elapsed))
from backend.apps.training.service.auto_labeler import get_auto_labeler_service
from backend.apps.training.repository.inference_log_repository import InferenceLogRepository
from backend.apps.training.service.calibration_utils import compute_calibration
from backend.apps.trading.service.trading_service import get_trading_service
from prometheus_client import Summary
from pydantic import BaseModel, Field
from typing import List, Any, Dict, Union
from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as fetch_kline_recent  # canonical ohlcv_candles
from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as _ohlcv_fetch_recent  # reuse for delta
from backend.apps.trading.simulator import generate_mock_series, simulate_trading, SimConfig
from starlette.responses import StreamingResponse
from starlette.requests import Request as StarletteRequest


class RegisterModelRequest(BaseModel):
    name: str
    version: str
    model_type: str
    status: Optional[str] = None
    artifact_path: Optional[str] = None
    metrics: Optional[dict] = None


class MetricsAppendRequest(BaseModel):
    metrics: dict

class SimulateRequest(BaseModel):
    mode: str = "mock"  # mock | custom
    # mock params
    n: Optional[int] = None
    start_price: Optional[float] = None
    drift: Optional[float] = None
    vol: Optional[float] = None
    prob_noise: Optional[float] = None
    seed: Optional[int] = None
    # custom arrays
    prices: Optional[List[float]] = None
    probs: Optional[List[float]] = None
    # trading cfg
    base_units: Optional[float] = None
    fee_rate: Optional[float] = None
    threshold: Optional[float] = None
    cooldown_sec: Optional[float] = None
    allow_scale_in: Optional[bool] = None
    si_ratio: Optional[float] = None
    si_max_legs: Optional[int] = None
    si_min_price_move: Optional[float] = None
    si_cooldown_sec: Optional[float] = None
    exit_require_net_profit: Optional[bool] = None
    exit_slice_seconds: Optional[float] = None

class RealizedUpdate(BaseModel):
    id: int
    realized: int  # Accepts 0/1 or -1/1 values

class RealizedBatchRequest(BaseModel):
    updates: List[RealizedUpdate]

registry_repo = ModelRegistryRepository()
risk_engine = RiskEngine(
    limits=RiskLimits(
        max_notional=50_000,  # example
        max_daily_loss=2_000,
        max_drawdown=0.2,
        atr_multiple=2.0,
    )
)

cfg = load_config()

LOW_BUY_ENABLED = str(os.getenv("LOW_BUY_ENABLED", "1")).lower() in {"1", "true", "yes", "on"}
_low_buy_service: Optional["LowBuyService"] = None

try:
    _autopilot_mode = AutopilotMode(str(getattr(cfg, "autopilot_mode", "paper")).lower())
except Exception:
    _autopilot_mode = AutopilotMode.PAPER
_autopilot_service = AutopilotService(symbol=cfg.symbol, risk_engine=risk_engine, mode=_autopilot_mode)


def get_low_buy_service() -> "LowBuyService":
    global _low_buy_service
    if _low_buy_service is None:
        from backend.apps.trading.service.low_buy_service import LowBuyService  # local import to avoid circulars
        _low_buy_service = LowBuyService(risk_engine, autopilot=_autopilot_service)
    return _low_buy_service

# Inference & model metrics
INFERENCE_REQUESTS = Counter(
    "inference_requests_total",
    "Total inference requests",
    labelnames=["status"],
)
INFERENCE_LATENCY = Histogram(
    "inference_latency_seconds",
    "Inference latency in seconds",
    buckets=(0.001,0.005,0.01,0.025,0.05,0.1,0.25,0.5,1.0,2.0)
)
INFERENCE_PROB = Histogram(
    "inference_probability",
    "Distribution of predicted positive class probabilities",
    buckets=(0.0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0)
)
INFERENCE_ARTIFACT_NOT_FOUND = Counter(
    "inference_artifact_not_found_total",
    "Inference attempts where referenced artifact file was not found (artifact_not_found status)"
)
SEED_FALLBACK_ACTIVE = Gauge(
    "inference_seed_fallback_active",
    "1 if current inference response used seed baseline fallback (no artifact), else 0",
)
SEED_FALLBACK_TOTAL = Counter(
    "inference_seed_fallback_total",
    "Total count of seed baseline fallback inference responses",
)
SEED_FALLBACK_DURATION = Gauge(
    "inference_seed_fallback_duration_seconds",
    "Continuous seconds the system has remained in seed fallback state (resets to 0 when normal model used).",
)
SEED_FALLBACK_LAST_EXIT = Gauge(
    "inference_seed_fallback_last_exit_timestamp",
    "Unix timestamp (seconds) when system last exited seed fallback (0 if never).",
)
SEED_FALLBACK_INSTABILITY_RATIO = Gauge(
    "inference_seed_fallback_instability_ratio",
    "Fraction (0-1) of time spent in seed fallback within rolling window.",
    labelnames=["window"],
)
SEED_FALLBACK_INSTABILITY_WINDOW = Gauge(
    "inference_seed_fallback_instability_window_seconds",
    "Window size in seconds used for instability ratio calculation.",
)

 
MODEL_PROD_AUC = Gauge("model_production_auc", "Current production model AUC")
MODEL_PROD_ACC = Gauge("model_production_accuracy", "Current production model accuracy")
MODEL_PROD_VERSION = Gauge("model_production_version_timestamp", "Production model version as unix timestamp (or 0 if not numeric)")
MODEL_PROD_BRIER = Gauge("model_production_brier", "Current production model Brier score (lower is better)")
MODEL_PROD_ECE = Gauge("model_production_ece", "Current production model Expected Calibration Error (lower is better)")

# Live calibration gauges for realized outcomes window
INFERENCE_LIVE_BRIER = Gauge(
    "inference_live_brier",
    "Live window Brier score over realized outcomes (lower is better)",
)
INFERENCE_LIVE_ECE = Gauge(
    "inference_live_ece",
    "Live window Expected Calibration Error over realized outcomes (lower is better)",
)
INFERENCE_LIVE_MCE = Gauge(
    "inference_live_mce",
    "Live window Maximum Calibration Error over realized outcomes",
)
INFERENCE_LIVE_ECE_DELTA = Gauge(
    "inference_live_ece_delta",
    "Absolute difference between live ECE and production (validation) ECE",
)
CALIBRATION_DRIFT_EVENTS = Counter(
    "inference_calibration_drift_events_total",
    "Number of detected calibration drift events (ECE gap exceeded thresholds)",
    labelnames=["type"],  # type: abs|rel
)
CALIBRATION_DRIFT_STREAK_ABS = Gauge(
    "inference_calibration_drift_streak_abs",
    "Current consecutive absolute ECE drift events streak",
)
CALIBRATION_DRIFT_STREAK_REL = Gauge(
    "inference_calibration_drift_streak_rel",
    "Current consecutive relative ECE drift events streak",
)
CALIBRATION_LAST_LIVE_ECE = Gauge(
    "inference_calibration_last_live_ece",
    "Most recent live window ECE",
)
CALIBRATION_LAST_PROD_ECE = Gauge(
    "inference_calibration_last_prod_ece",
    "Current production model ECE used for comparison",
)
CALIBRATION_RETRAIN_RECOMMEND = Gauge(
    "calibration_retrain_recommendation",
    "1 if current calibration status recommends retraining, else 0",
)
CALIBRATION_RETRAIN_RECOMMEND_EVENTS = Counter(
    "calibration_retrain_recommendation_events_total",
    "Counts transitions for retrain recommendation state",
    labelnames=["state"],  # state: enter|exit
)
CALIBRATION_RETRAIN_DELTA_IMPROVEMENT = Gauge(
    "calibration_retrain_delta_improvement",
    "Relative improvement (delta_before - current_delta)/delta_before after last calibration retrain (0-1, negative if worse)",
)
CALIBRATION_RETRAIN_EFFECT_EVAL_TS = Gauge(
    "calibration_retrain_effect_evaluated_timestamp",
    "Unix ts when calibration retrain effect last evaluated",
)
CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS = Counter(
    "calibration_retrain_improvement_events_total",
    "Calibration retrain effect evaluation events by result",
    labelnames=["result"],  # result: improved|worsened|no_change
)

# Eager labeling attempts triggered by live calibration endpoint
CALIBRATION_EAGER_LABEL_RUNS = Counter(
    "inference_calibration_eager_label_runs_total",
    "Number of eager label runs triggered by the live calibration endpoint when window has no realized labels",
)
CALIBRATION_EAGER_LABEL_RUNS_LABELED = Counter(
    "inference_calibration_eager_label_runs_by_label_total",
    "Eager label run attempts by symbol/interval/result (attempted|skipped_lock|error)",
    ["symbol", "interval", "result"],
)

# Idempotent task starter usable from anywhere in this module (e.g., admin endpoints)
from typing import Callable, Awaitable, Optional, Union  # noqa: E402
TaskFactory = Union[Callable[[], Awaitable[Any]], Callable[[], asyncio.Task]]

def _start_task_once(attr: str, factory: Optional[TaskFactory], *, label: Optional[str] = None) -> bool:  # type: ignore[name-defined]
    try:
        existing = getattr(app.state, attr, None)
        if existing and not getattr(existing, 'done', lambda: True)():
            return False
        if not factory:
            return False
        task = asyncio.create_task(factory())  # type: ignore[arg-type]
        setattr(app.state, attr, task)
        return True
    except Exception:
        try:
            if label:
                app.state.degraded_components.append(f"{label}_start_fail")
        except Exception:
            pass
        return False

# News metrics
NEWS_FETCH_RUNS = Counter(
    "news_fetch_runs_total",
    "News fetch loop executions",
    labelnames=["source"],
)
NEWS_FETCH_ERRORS = Counter(
    "news_fetch_errors_total",
    "News fetch errors",
    labelnames=["source"],
)
NEWS_ARTICLES_INGESTED = Counter(
    "news_articles_ingested_total",
    "Number of news articles ingested",
    labelnames=["source"],
)
NEWS_ARTICLES_TOTAL = Gauge(
    "news_articles_total",
    "Total persistent news articles (current row count in news_articles table)",
)
NEWS_ARTICLES_SOURCE_TOTAL = Gauge(
    "news_articles_source_total",
    "Persistent news articles per source (current counts)",
    labelnames=["source"],
)
NEWS_INGESTION_LAG = Gauge(
    "news_ingestion_lag_seconds",
    "Seconds since last ingested news article (synthetic demo)",
    labelnames=["source"],
)
FEATURE_DRIFT_SCANS = Counter(
    "feature_drift_scans_total",
    "Total feature drift scan invocations",
)
FEATURE_DRIFT_EVENTS = Counter(
    "feature_drift_events_total",
    "Counts drift state transitions",
    labelnames=["event"],  # event: entered|exited
)
FEATURE_DRIFT_ACTIVE = Gauge(
    "feature_drift_active_count",
    "Number of features currently classified as drift in last scan",
)
NEWS_SENTIMENT_AVG = Gauge(
    "news_sentiment_average",
    "Average sentiment over recent window (default 60m synthetic demo)",
    labelnames=["source", "window"],
)
NEWS_SENTIMENT_SAMPLES = Gauge(
    "news_sentiment_sample_count",
    "Sample count used for sentiment average (recent window)",
    labelnames=["source", "window"],
)

# Per-source RSS fetcher metrics (latency & article counts)
NEWS_SOURCE_FETCH_LATENCY = Histogram(
    "news_source_fetch_latency_seconds",
    "Latency per individual news source fetch operation",
    labelnames=["source"],
    buckets=(0.05,0.1,0.25,0.5,1,2,5,10)
)
NEWS_SOURCE_FETCH_ARTICLES = Counter(
    "news_source_articles_total",
    "Articles yielded by individual news source fetch operations (pre-dedup / pre-insert)",
    labelnames=["source"],
)
NEWS_SOURCE_FETCH_ERRORS = Counter(
    "news_source_fetch_errors_total",
    "Errors raised during individual source fetch attempts",
    labelnames=["source"],
)
NEWS_SOURCE_FETCH_TRUNCATED = Counter(
    "news_source_fetch_truncated_total",
    "Fetch results truncated due to fetch size cap",
    labelnames=["source"],
)
NEWS_SOURCE_DEDUP_DROPPED = Counter(
    "news_source_dedup_dropped_total",
    "Articles dropped due to duplicate hash per source (before DB insert)",
    labelnames=["source"],
)
NEWS_SOURCE_DEDUP_INSERTS = Counter(
    "news_source_dedup_inserts_total",
    "Articles kept (unique) per source prior to DB insert",
    labelnames=["source"],
)
NEWS_SOURCE_DEDUP_RATIO = Gauge(
    "news_source_dedup_ratio",
    "Unique / Raw ratio per source (process lifetime approximation)",
    labelnames=["source"],
)
NEWS_SOURCE_DEDUP_RATIO_ROLLING = Gauge(
    "news_source_dedup_ratio_rolling",
    "Rolling window unique/raw ratio per source",
    labelnames=["source","window"],
)
NEWS_SOURCE_BACKOFF_SKIPS = Counter(
    "news_source_backoff_skips_total",
    "Fetch attempts skipped due to active backoff",
    labelnames=["source"],
)
NEWS_SOURCE_DISABLED_SKIPS = Counter(
    "news_source_disabled_skips_total",
    "Fetch attempts skipped because source disabled",
    labelnames=["source"],
)
NEWS_INGEST_RAW_RATIO = Gauge(
    "news_ingest_raw_to_inserted_ratio",
    "Approx ratio inserted_articles / raw_articles (process lifetime approximation)",
)
NEWS_DEDUP_HASH_PRELOAD = Gauge(
    "news_dedup_hash_preload_count",
    "Number of recent hashes preloaded from persistent buffer on startup",
)
NEWS_SOURCE_HEALTH_SCORE = Gauge(
    "news_source_health_score",
    "Composite health score (0-1) per news source (latency, errors/backoff, dedup efficiency)",
    labelnames=["source"],
)
NEWS_SOURCE_HEALTH_EVENTS = Counter(
    "news_source_health_events_total",
    "Health score threshold crossing events (drop|recover)",
    labelnames=["source","event"],
)

# Stall detection metrics (per-source ingest freshness)
NEWS_SOURCE_LAST_INGEST_TS = Gauge(
    "news_source_last_ingest_timestamp",
    "Last successful unique article ingest timestamp (unix seconds) per source",
    labelnames=["source"],
)
NEWS_SOURCE_STALLED = Gauge(
    "news_source_stalled",
    "Whether source currently considered stalled (1) or not (0) based on inactivity threshold",
    labelnames=["source"],
)
NEWS_SOURCE_STALL_EVENTS = Counter(
    "news_source_stall_events_total",
    "Stall state transition events (stall|recover)",
    labelnames=["source","event"],
)

# Dedup anomaly detection metrics
NEWS_SOURCE_DEDUP_RATIO_GAUGE = Gauge(
    "news_source_dedup_ratio_latest",
    "Latest per-poll dedup unique/raw ratio (kept/raw) for a source",
    labelnames=["source"],
)
NEWS_SOURCE_DEDUP_ANOMALY = Gauge(
    "news_source_dedup_anomaly",
    "1 when dedup anomaly detected for source (low ratio streak or volatility spike)",
    labelnames=["source"],
)
NEWS_SOURCE_DEDUP_ANOMALY_EVENTS = Counter(
    "news_source_dedup_anomaly_events_total",
    "Dedup anomaly state transitions",
    labelnames=["source","event"],  # event=enter|exit|volatility|low_ratio
)
NEWS_SOURCE_DEDUP_VOLATILITY = Gauge(
    "news_source_dedup_ratio_volatility",
    "Rolling volatility (stddev) of dedup ratio over configured window",
    labelnames=["source"],
)

# System health high-level gauges
SYSTEM_OVERALL_STATUS = Gauge(
    "system_overall_status",
    "High-level overall system health (0=error,1=degraded,2=ok)",
)
SYSTEM_COMPONENT_HEALTH = Gauge(
    "system_component_health",
    "Per-component health (0=error,1=degraded,2=ok, -1=unknown/skipped)",
    labelnames=["component"],
)

# ---------------------------------------------------------------------------
# OHLCV WebSocket Flush Listener Metrics (append broadcast debug)
# ---------------------------------------------------------------------------
try:  # pragma: no cover
    OHLCV_WS_FLUSH_LISTENER_RUNS = Counter(
        "ohlcv_ws_flush_listener_runs_total",
        "Number of flush listener executions that attempted append broadcast",
        ["symbol"],
    )
    OHLCV_WS_FLUSH_LISTENER_ERRORS = Counter(
        "ohlcv_ws_flush_listener_errors_total",
        "Errors inside flush listener prior to or during append broadcast",
        ["symbol"],
    )
except Exception:  # pragma: no cover
    OHLCV_WS_FLUSH_LISTENER_RUNS = None  # type: ignore
    OHLCV_WS_FLUSH_LISTENER_ERRORS = None  # type: ignore

# Risk metrics gauges
RISK_CURRENT_EQUITY = Gauge("risk_current_equity", "Risk engine current equity")
RISK_PEAK_EQUITY = Gauge("risk_peak_equity", "Risk engine peak equity")
RISK_DRAWDOWN_RATIO = Gauge("risk_drawdown_ratio", "Current drawdown ratio (0-1)")
RISK_NOTIONAL_EXPOSURE = Gauge("risk_notional_exposure", "Sum of absolute notional exposure across positions")
RISK_NOTIONAL_UTILIZATION = Gauge("risk_notional_utilization", "Notional exposure divided by max notional limit (0-1)")
RISK_DAILY_LOSS_RATIO = Gauge("risk_daily_loss_ratio", "(Starting equity - current equity) / max daily loss (0-1, can exceed)")

# Risk order evaluation counters
RISK_ORDER_EVALS = Counter(
    "risk_order_evaluations_total",
    "Total risk order evaluations",
    labelnames=["allowed"],
)
RISK_ORDER_REJECTS = Counter(
    "risk_order_reject_total",
    "Risk order rejections by reason",
    labelnames=["reason"],
)
TRADING_ORDERS = Counter(
    "trading_orders_total",
    "Total trading orders submitted",
    labelnames=["status"],
)
TRADING_FILLS = Counter(
    "trading_fills_total",
    "Filled trading orders",
    labelnames=["symbol"],
)
TRADING_REJECTS = Counter(
    "trading_order_rejects_total",
    "Rejected trading orders",
    labelnames=["reason"],
)
TRAINING_RUNS = Counter(
    "training_runs_total",
    "Training run attempts",
    labelnames=["status"],
)
TRAINING_DURATION = Histogram(
    "training_run_duration_seconds",
    "Training run wall time",
    buckets=(0.5,1,2,5,10,20,40,80,160)
)
TRAINING_DURATION_LABELED = Histogram(
    "training_run_duration_labeled_seconds",
    "Training run wall time partitioned by trigger & status (manual|drift_auto|calibration_auto)",
    labelnames=["trigger","status"],
    buckets=(0.5,1,2,5,10,20,40,80,160)
)
TRAINING_LAST_METRIC = Gauge(
    "training_last_metric_value",
    "Last training run core metric value (AUC by default)",
    labelnames=["metric"],
)
TRAINING_AUTO_PROMOTIONS = Counter(
    "training_auto_promotions_total",
    "Automatic model promotions after training",
    labelnames=["result"],  # promoted|skipped
)
TRAINING_PROMOTION_REASON = Counter(
    "training_promotion_reason_total",
    "Counts auto-promotion decision reasons (normalized categories)",
    labelnames=["reason"],
)
TRAINING_PROMOTION_AUDIT = Counter(
    "training_promotion_audit_total",
    "Number of promotion audit records inserted",
    labelnames=["decision"],
)
TRAINING_FAILURE_RATIO = Gauge(
    "training_failure_ratio",
    "Fraction of training runs whose status != 'ok' (0-1)",
)
TRAINING_FAILURE_TOTAL = Gauge(
    "training_failure_total_cached",
    "Cached count of failed training runs (derived)",
)
TRAINING_SUCCESS_TOTAL = Gauge(
    "training_success_total_cached",
    "Cached count of successful training runs (derived)",
)

_training_outcome_state = {"success": 0, "failure": 0}
def _record_training_outcome(status: str):
    """Update cached success/failure counts & ratio gauges.

    status: expected 'ok' for success. Anything else counts as failure.
    """
    try:
        if status == "ok":
            _training_outcome_state["success"] += 1
        else:
            _training_outcome_state["failure"] += 1
        succ = _training_outcome_state["success"]
        fail = _training_outcome_state["failure"]
        total = succ + fail
        TRAINING_SUCCESS_TOTAL.set(succ)
        TRAINING_FAILURE_TOTAL.set(fail)
        if total > 0:
            TRAINING_FAILURE_RATIO.set(fail / total)
    except Exception:
        pass

# Rate limiting metrics
RATE_LIMIT_REQUESTS = Counter(
    "rate_limit_requests_total",
    "Requests evaluated by rate limiter",
    labelnames=["bucket","action"],  # action: allow|block
)

# Rate limiting helpers (declared early for dependency availability)
import threading
from typing import Tuple
_rate_lock = threading.Lock()
_rate_state: dict[Tuple[str,str,str], dict] = {}
def _rate_limit_check(bucket: str, key: str, rps: float, burst: int) -> bool:
    if rps <= 0:
        return True
    now = time.time()
    interval = 1.0 / rps
    with _rate_lock:
        st = _rate_state.get((bucket,key, str(rps)+":"+str(burst)))
        if not st:
            st = {"tokens": burst, "last_ts": now}
            _rate_state[(bucket,key, str(rps)+":"+str(burst))] = st
        elapsed = now - st["last_ts"]
        if elapsed > 0:
            add = elapsed / interval
            if add > 0:
                st["tokens"] = min(burst, st["tokens"] + add)
        st["last_ts"] = now
        if st["tokens"] >= 1:
            st["tokens"] -= 1
            return True
        return False
def require_rate_limit(bucket: str):
    async def _dep(request: Request, api_key: str = Depends(require_api_key)):
        if not cfg.rate_limit_enabled:
            return True
        client_host = request.client.host if request.client else "local"
        if bucket == "training":
            allowed = _rate_limit_check(bucket, f"{client_host}:{api_key}", cfg.rate_limit_training_rps, cfg.rate_limit_training_burst)
        else:
            allowed = _rate_limit_check(bucket, f"{client_host}:{api_key}", cfg.rate_limit_inference_rps, cfg.rate_limit_inference_burst)
        try:
            RATE_LIMIT_REQUESTS.labels(bucket=bucket, action="allow" if allowed else "block").inc()
        except Exception:
            pass
        if not allowed:
            retry_after = 1.0 / (cfg.rate_limit_training_rps if bucket=="training" else cfg.rate_limit_inference_rps)
            raise HTTPException(status_code=429, detail="rate_limited", headers={"Retry-After": f"{retry_after:.2f}"})
        return True
    return _dep

# Sentiment mode supervisory metrics
SENTIMENT_MODE_ENABLED = Gauge(
    "sentiment_mode_enabled",
    "1 if sentiment feature pipeline is enabled, else 0 (auto or manual disable)",
)
SENTIMENT_MODE_DISABLE_EVENTS = Counter(
    "sentiment_mode_disable_events_total",
    "Counts sentiment mode disable events by reason",
    labelnames=["reason"],  # reason: failure_streak|manual|other
)

async def update_risk_metrics():
    try:
        sess = risk_engine.session
        # Basic equity metrics
        RISK_CURRENT_EQUITY.set(sess.current_equity)
        RISK_PEAK_EQUITY.set(sess.peak_equity)
        drawdown = 0.0
        if sess.peak_equity > 0:
            drawdown = (sess.peak_equity - sess.current_equity) / sess.peak_equity
        RISK_DRAWDOWN_RATIO.set(max(0.0, drawdown))
        # Notional exposure
        total_notional = 0.0
        for pos in risk_engine.positions.values():
            if pos.size != 0 and pos.entry_price > 0:
                total_notional += abs(pos.size * pos.entry_price)
        RISK_NOTIONAL_EXPOSURE.set(total_notional)
        if risk_engine.limits.max_notional > 0:
            RISK_NOTIONAL_UTILIZATION.set(min(1.0, total_notional / risk_engine.limits.max_notional))
        else:
            RISK_NOTIONAL_UTILIZATION.set(0.0)
        # Daily loss ratio
        daily_loss = sess.starting_equity - sess.current_equity
        if risk_engine.limits.max_daily_loss > 0:
            RISK_DAILY_LOSS_RATIO.set(daily_loss / risk_engine.limits.max_daily_loss)
        else:
            RISK_DAILY_LOSS_RATIO.set(0.0)
    except Exception as e:
        logging.getLogger("api").warning("risk metrics update failed: %s", e)

async def risk_metrics_loop(interval: float = 5.0):
    while True:
        await update_risk_metrics()
        await asyncio.sleep(interval)

async def refresh_production_metrics():
    repo = ModelRegistryRepository()
    rows = await repo.fetch_latest(cfg.auto_promote_model_name, "supervised", limit=5)
    prod = None
    for r in rows:
        if r["status"] == "production":
            prod = r
            break
    if not prod and rows:
        prod = rows[0]  # fallback to latest staging to have some visibility
    if not prod:
        return
    metrics = prod.get("metrics") or {}
    auc = metrics.get("auc")
    acc = metrics.get("accuracy")
    brier = metrics.get("brier")
    ece = metrics.get("ece")
    if isinstance(auc, (int, float)) and auc == auc:  # not NaN
        MODEL_PROD_AUC.set(auc)
    if isinstance(acc, (int, float)) and acc == acc:
        MODEL_PROD_ACC.set(acc)
    if isinstance(brier, (int, float)) and brier == brier:
        MODEL_PROD_BRIER.set(brier)
    if isinstance(ece, (int, float)) and ece == ece:
        MODEL_PROD_ECE.set(ece)
    # version may be a timestamp string
    version = prod.get("version")
    # Try to parse version into a timestamp-like number for display: accept raw number or leading numeric segment
    try:
        vnum: float | None = None
        if isinstance(version, (int, float)):
            vnum = float(version)
        elif isinstance(version, str):
            import re as _re
            m = _re.match(r"^(\d+(?:\.\d+)?)", version.strip())
            if m:
                vnum = float(m.group(1))
        if vnum is not None:
            MODEL_PROD_VERSION.set(vnum)
        else:
            MODEL_PROD_VERSION.set(0.0)
    except Exception:
        MODEL_PROD_VERSION.set(0.0)

async def production_metrics_loop(interval: float = 60.0):
    """Periodic loop to refresh production model metrics gauges.

    Runs best-effort; swallows exceptions and keeps looping.
    """
    while True:
        try:
            await refresh_production_metrics()
        except Exception as e:  # noqa: BLE001
            logging.getLogger("api").debug("production_metrics_loop_error err=%s", e)
        await asyncio.sleep(max(5.0, float(interval)))


async def low_buy_signal_loop(interval: Optional[float] = None):
    if not LOW_BUY_ENABLED or not getattr(cfg, "low_buy_auto_loop_enabled", True):
        return
    configured_interval = interval if interval is not None else getattr(cfg, "low_buy_auto_loop_interval", 20.0)
    sleep_interval = max(5.0, float(configured_interval or 20.0))
    ttl_setting = getattr(cfg, "low_buy_signal_ttl", 120.0)
    signal_ttl = max(sleep_interval * 3.0, float(ttl_setting or 120.0))
    svc = get_low_buy_service()
    params = {"symbol": cfg.symbol, "interval": cfg.kline_interval}
    last_signal_ts = 0.0
    auto_execute_enabled = bool(getattr(cfg, "low_buy_auto_execute_enabled", False))
    allow_paper_auto = bool(getattr(cfg, "low_buy_auto_execute_paper", True))
    while True:
        loop_started = time.time()
        try:
            evaluation = await svc.evaluate(dict(params))
            if evaluation.get("triggered"):
                should_execute = auto_execute_enabled
                try:
                    mode = _autopilot_service.strategy_mode
                    if not should_execute and mode == AutopilotMode.PAPER and allow_paper_auto:
                        should_execute = True
                    if should_execute and mode == AutopilotMode.PAPER and not allow_paper_auto:
                        should_execute = False
                except Exception:
                    pass
                await svc.trigger(dict(params), auto_execute=should_execute, reason="auto_loop")
                last_signal_ts = time.time()
            else:
                now = time.time()
                if last_signal_ts and (now - last_signal_ts) >= signal_ttl:
                    try:
                        await _autopilot_service.clear_signal("low_buy_stale")
                    except Exception:
                        pass
                    last_signal_ts = 0.0
        except Exception as e:  # noqa: BLE001
            logger.warning("low_buy_signal_loop_error:%s", e)
        elapsed = time.time() - loop_started
        await asyncio.sleep(max(2.0, sleep_interval - elapsed))


async def low_buy_exit_loop(interval: Optional[float] = None):
    if not LOW_BUY_ENABLED or not getattr(cfg, "low_buy_exit_loop_enabled", True):
        return
    configured_interval = interval if interval is not None else getattr(cfg, "low_buy_exit_loop_interval", 10.0)
    sleep_interval = max(3.0, float(configured_interval or 10.0))
    take_profit_pct = max(0.0, float(getattr(cfg, "low_buy_take_profit_pct", 0.0) or 0.0))
    stop_loss_pct = max(0.0, float(getattr(cfg, "low_buy_stop_loss_pct", 0.0) or 0.0))
    symbol = cfg.symbol
    svc = get_trading_service(risk_engine)
    while True:
        loop_started = time.time()
        try:
            pos = risk_engine.positions.get(symbol) or risk_engine.positions.get(symbol.upper())
            size = float(getattr(pos, "size", 0.0)) if pos else 0.0
            entry_price = float(getattr(pos, "entry_price", 0.0)) if pos else 0.0
            if pos and size > 0 and entry_price > 0:
                recents = await _ohlcv_fetch_recent(symbol, cfg.kline_interval, limit=1)
                price = float(recents[-1]['close']) if recents else None
                if price and price > 0:
                    pnl_pct = (price - entry_price) / entry_price
                    exit_reason = None
                    if take_profit_pct and pnl_pct >= take_profit_pct:
                        exit_reason = "take_profit"
                    elif stop_loss_pct and pnl_pct <= -stop_loss_pct:
                        exit_reason = "stop_loss"
                    if exit_reason:
                        try:
                            await svc.submit_market(
                                symbol=symbol,
                                side="sell",
                                size=size,
                                price=price,
                                reason=f"low_buy_{exit_reason}",
                            )
                        except Exception as submit_err:  # noqa: BLE001
                            logger.warning("low_buy_exit_submit_error:%s", submit_err)
                        else:
                            with contextlib.suppress(Exception):
                                await _autopilot_service.clear_signal(f"low_buy_{exit_reason}")
            else:
                # No active position; opportunistically clear stale signals tagged as filled
                state_signal = getattr(_autopilot_service, "_state", None)
                active_signal = getattr(state_signal, "active_signal", None)
                if active_signal and (active_signal.extra or {}).get("status") == "filled":
                    with contextlib.suppress(Exception):
                        await _autopilot_service.clear_signal("low_buy_position_flat")
        except Exception as e:  # noqa: BLE001
            logger.warning("low_buy_exit_loop_error:%s", e)
        elapsed = time.time() - loop_started
        await asyncio.sleep(max(2.0, sleep_interval - elapsed))

@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore
    """Application lifespan with optional FAST_STARTUP mode.

    FAST_STARTUP (env var): when true we start only the minimum set of tasks
    to serve HTTP + basic metrics quickly. Heavy background loops are skipped
    initially and can be enabled later via an admin endpoint. This reduces
    cold-start churn / reload races under Uvicorn --reload in dev.
    """
    fast = os.getenv("FAST_STARTUP", "false").lower() in ("1","true","yes","on")
    app.state.fast_startup = fast
    # Optional selective eager components even under FAST_STARTUP (comma separated)
    eager_env = os.getenv("FAST_STARTUP_EAGER_COMPONENTS", "")
    eager_components: set[str] = set()
    if eager_env.strip():
        for part in eager_env.split(","):
            p = part.strip().lower()
            if p:
                eager_components.add(p)
    app.state.fast_startup_eager = eager_components
    db_pool = None
    try:
        db_pool = await asyncio.wait_for(init_pool(), timeout=8.0)
    except Exception as e:
        logging.getLogger("api").warning("DB pool init during startup failed or timed out: %s (starting in degraded mode)", e)
        db_pool = None
    app.state.degraded_components = []
    app.state.skipped_components = []
    # Seed baseline model if registry empty for target name (only if DB pool is ready)
    if db_pool is not None:
        try:
            repo_seed = ModelRegistryRepository()
            seed_name = "bottom_predictor"
            existing = await repo_seed.fetch_latest(seed_name, "supervised", limit=1)
            if not existing:
                baseline_metrics = {
                    "auc": 0.50,
                    "accuracy": 0.50,
                    "brier": 0.25,
                    "ece": 0.05,
                    "mce": 0.05,
                    "seed_baseline": True,
                }
                version = "seed"
                try:
                    await repo_seed.register(
                        name=seed_name,
                        version=version,
                        model_type="supervised",
                        status="production",
                        artifact_path=None,
                        metrics=baseline_metrics,
                    )
                    logging.getLogger("api").info("Seed baseline model inserted name=%s version=%s", seed_name, version)
                    try:
                        SEED_AUTO_SEED_TOTAL.labels(source="startup", result="success").inc()
                    except Exception:
                        pass
                except Exception as se:  # noqa: BLE001
                    logging.getLogger("api").warning("Seed baseline model insert failed: %s", se)
                    try:
                        SEED_AUTO_SEED_TOTAL.labels(source="startup", result="error").inc()
                    except Exception:
                        pass
        except Exception as outer_seed_err:  # noqa: BLE001
            logging.getLogger("api").debug("Baseline seed check failed (will skip): %s", outer_seed_err)
    # Ensure minimum schema exists (tables used by runtime logging and metrics)
    # Call unconditionally; ensure_all_schema can fallback to a direct connection if pool isn't ready.
    try:
        with contextlib.suppress(Exception):
            await ensure_all_schema()
    except Exception:
        app.state.degraded_components.append("schema_ensure_exception")
    # Risk engine (lightweight, but DB dependent)
    try:
        if db_pool is not None:
            await risk_engine.load()
            # After loading from DB, if env requests a specific baseline, persist it once
            try:
                _env_start = os.getenv("RISK_STARTING_EQUITY")
                if _env_start:
                    try:
                        _env_start_val = float(_env_start)
                        # Only apply if differs to avoid unnecessary writes
                        if math.isfinite(_env_start_val) and _env_start_val > 0 and abs((_env_start_val) - float(risk_engine.session.starting_equity)) > 1e-9:
                            await risk_engine.reset_equity(_env_start_val, reset_pnl=True, touch_peak=True)
                    except Exception:
                        pass
            except Exception:
                pass
        else:
            app.state.degraded_components.append("risk_engine_db")
    except Exception as e:
        logging.getLogger("api").warning("risk_engine.load failed: %s", e)
        app.state.degraded_components.append("risk_engine_exception")
    # If DB wasn't ready at startup, schedule a background retry to load risk state/positions
    try:
        if (db_pool is None) or ("risk_engine_db" in app.state.degraded_components):
            async def _retry_load_risk_engine(max_attempts: int = 30, delay_sec: float = 10.0):
                import asyncio as _aio
                log = logging.getLogger("api")
                for attempt in range(1, max_attempts + 1):
                    try:
                        p = await _aio.wait_for(init_pool(), timeout=5.0)
                        if p is None:
                            raise RuntimeError("db_pool_none")
                        await risk_engine.load()
                        log.info("risk_engine loaded successfully on retry attempt %d", attempt)
                        # clear degraded flag if present
                        try:
                            if "risk_engine_db" in app.state.degraded_components:
                                app.state.degraded_components.remove("risk_engine_db")
                        except Exception:
                            pass
                        return
                    except Exception as re:
                        log.warning("risk_engine retry %d failed: %s", attempt, re)
                        await _aio.sleep(delay_sec)
                log.warning("risk_engine retry exhausted without success")
            asyncio.create_task(_retry_load_risk_engine())
    except Exception:
        pass
    async def _wait_for_db_pool(timeout: float = 15.0, interval: float = 0.5):
        """Wait until the asyncpg pool is ready before proceeding.

        Returns the pool once available or None if the timeout expires.
        """
        deadline = time.monotonic() + timeout
        pool_candidate = db_pool if db_pool is not None else await init_pool()
        if pool_candidate is not None:
            return pool_candidate
        await ensure_pool_background()
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            pool_candidate = await init_pool()
            if pool_candidate is not None:
                return pool_candidate
        return None

    def _schedule_db_component(flag: str, starter):
        async def _runner():
            pool_candidate = await _wait_for_db_pool()
            if pool_candidate is None:
                return
            try:
                with contextlib.suppress(ValueError):
                    if flag in getattr(app.state, 'degraded_components', []):
                        app.state.degraded_components.remove(flag)
            except Exception:
                pass
            try:
                starter()
            except Exception as e:  # noqa: BLE001
                logging.getLogger("api").warning("delayed_start_failed component=%s err=%s", flag, e)
        asyncio.create_task(_runner())

    # --- Idempotent task helper -------------------------------------------------
    def _start_task_once(attr: str, factory: Optional[TaskFactory], *, label: Optional[str] = None):  # type: ignore[name-defined]
        """Start an asyncio task only if not already running.

        Returns True if a new task was started, else False.
        """
        if not factory:
            return False
        existing = getattr(app.state, attr, None)
        if existing and not getattr(existing, 'done', lambda: True)():  # task already running
            return False
        try:
            task = asyncio.create_task(factory())  # type: ignore[arg-type]
            setattr(app.state, attr, task)
            return True
        except Exception as e:  # noqa: BLE001
            logging.getLogger("api").warning("task_start_failed attr=%s err=%s", attr, e)
            if label:
                app.state.degraded_components.append(f"{label}_start_fail")
            return False

    # Always start (idempotent) risk metrics loop
    _start_task_once("risk_metrics_task", lambda: risk_metrics_loop(interval=cfg.risk_metrics_interval), label="risk_metrics")
    # Kick an immediate refresh so MODEL_PROD_* gauges are non-zero when possible
    try:
        await refresh_production_metrics()
    except Exception:
        pass
    # Background production metrics loop (skip only in strict fast mode unless explicitly eager)
    try:
        start_prod_metrics = (not fast) or ("production_metrics" in app.state.fast_startup_eager)
        if start_prod_metrics:
            _start_task_once(
                "production_metrics_task",
                lambda: production_metrics_loop(interval=getattr(cfg, "production_metrics_interval", 60.0)),
                label="production_metrics",
            )
        else:
            app.state.skipped_components.append("production_metrics")
            # Even when skipping the loop in FAST_STARTUP, schedule a one-time refresh after DB is ready
            try:
                _schedule_db_component("production_metrics", lambda: asyncio.create_task(refresh_production_metrics()))
            except Exception:
                pass
    except Exception:
        app.state.degraded_components.append("production_metrics_start_fail")
    # Load and apply DB-backed runtime settings once DB is available
    async def _load_and_apply_settings_once():
        try:
            pool_candidate = await _wait_for_db_pool()
            if pool_candidate is None:
                return
            repo = SettingsRepository()
            items = await repo.get_all()
            for it in items:
                try:
                    await apply_runtime_setting(it.get("key"), it.get("value"))
                except Exception:
                    pass
        except Exception:
            pass
    asyncio.create_task(_load_and_apply_settings_once())
    # Low-buy / ML signal loops (optional)
    try:
        source = str(getattr(cfg, "signal_source", "low_buy")).lower()
        if source == "low_buy":
            if LOW_BUY_ENABLED:
                if getattr(cfg, "low_buy_auto_loop_enabled", False) and not fast:
                    _start_task_once(
                        "low_buy_loop_task",
                        lambda: low_buy_signal_loop(interval=getattr(cfg, "low_buy_auto_loop_interval", 20.0)),
                        label="low_buy_loop",
                    )
                elif getattr(cfg, "low_buy_auto_loop_enabled", False) and fast:
                    app.state.skipped_components.append("low_buy_loop")
                if getattr(cfg, "low_buy_exit_loop_enabled", False) and not fast:
                    _start_task_once(
                        "low_buy_exit_loop_task",
                        lambda: low_buy_exit_loop(interval=getattr(cfg, "low_buy_exit_loop_interval", 10.0)),
                        label="low_buy_exit_loop",
                    )
                elif getattr(cfg, "low_buy_exit_loop_enabled", False) and fast:
                    app.state.skipped_components.append("low_buy_exit_loop")
            else:
                app.state.skipped_components.append("low_buy_disabled")
        elif source == "ml":
            try:
                from backend.apps.trading.service.ml_signal_service import MLSignalService  # noqa: F401
            except Exception:
                app.state.degraded_components.append("ml_signal_import_fail")
            else:
                if getattr(cfg, "ml_auto_loop_enabled", False) and not fast:
                    async def _ml_loop():
                        from backend.apps.trading.service.ml_signal_service import MLSignalService
                        svc = MLSignalService(risk_engine, autopilot=_autopilot_service)
                        interval = getattr(cfg, "ml_auto_loop_interval", 20.0)
                        sleep_interval = max(5.0, float(interval or 20.0))
                        while True:
                            start = time.time()
                            try:
                                auto_exec = bool(getattr(cfg, "ml_auto_execute_enabled", True))
                                allow_paper = bool(getattr(cfg, "ml_auto_execute_paper", True))
                                should = auto_exec
                                try:
                                    mode = _autopilot_service.strategy_mode
                                    if not should and mode == AutopilotMode.PAPER and allow_paper:
                                        should = True
                                    if should and mode == AutopilotMode.PAPER and not allow_paper:
                                        should = False
                                except Exception:
                                    pass
                                await svc.trigger(auto_execute=should, reason="ml_auto_loop")
                            except Exception as e:
                                logger.warning("ml_signal_loop_error:%s", e)
                            elapsed = time.time() - start
                            await asyncio.sleep(max(2.0, sleep_interval - elapsed))
                    _start_task_once("ml_signal_loop_task", _ml_loop, label="ml_signal_loop")
                elif getattr(cfg, "ml_auto_loop_enabled", False) and fast:
                    app.state.skipped_components.append("ml_signal_loop")
                # Also enable the same exit loop for ML-driven entries
                if getattr(cfg, "low_buy_exit_loop_enabled", False) and not fast:
                    _start_task_once(
                        "low_buy_exit_loop_task",
                        lambda: low_buy_exit_loop(interval=getattr(cfg, "low_buy_exit_loop_interval", 10.0)),
                        label="low_buy_exit_loop",
                    )
                elif getattr(cfg, "low_buy_exit_loop_enabled", False) and fast:
                    app.state.skipped_components.append("low_buy_exit_loop")
    except Exception:
        app.state.degraded_components.append("low_buy_loop_start_fail")
    # Feature service always created (light), scheduler maybe skipped
    feature_service = FeatureService(cfg.symbol, cfg.kline_interval)
    app.state.feature_service = feature_service
    if not fast:
        if db_pool is not None:
            started = _start_task_once("feature_task", lambda: feature_scheduler(feature_service, interval_seconds=cfg.feature_sched_interval), label="feature_scheduler")
            if not started:
                # do not double append degraded, only if truly failed (already running is fine)
                pass
        else:
            if "feature_scheduler_db" not in app.state.degraded_components:
                app.state.degraded_components.append("feature_scheduler_db")
            _schedule_db_component("feature_scheduler_db", lambda: _start_task_once("feature_task", lambda: feature_scheduler(feature_service, interval_seconds=cfg.feature_sched_interval), label="feature_scheduler"))
    else:
        app.state.skipped_components.append("feature_scheduler")
    # Ingestion
    if cfg.ingestion_enabled and not fast:
        if not getattr(app.state, 'kline_task', None):
            consumer = KlineConsumer(cfg.symbol, cfg.kline_interval, batch_size=max(1, cfg.kline_consumer_batch_size))
            app.state.kline_consumer = consumer
            # Flush listener -> WebSocket append broadcast (best-effort)
            try:  # pragma: no cover
                if ohlcv_ws_manager:
                    async def _on_flush(batch):
                        from prometheus_client import Counter
                        try:
                            BROADCAST_APPEND_ATTEMPTS = Counter("ohlcv_ws_broadcast_append_attempts_total", "WS append broadcast attempts", ["symbol"])  # lazy define
                            BROADCAST_APPEND_SENT = Counter("ohlcv_ws_broadcast_append_sent_total", "WS append messages successfully queued", ["symbol"])  # lazy define
                        except Exception:
                            BROADCAST_APPEND_ATTEMPTS = None
                            BROADCAST_APPEND_SENT = None
                        # Transform Binance style batch (already closed) into public candle objects
                        out = []
                        if OHLCV_WS_FLUSH_LISTENER_RUNS:
                            with contextlib.suppress(Exception):
                                OHLCV_WS_FLUSH_LISTENER_RUNS.labels(cfg.symbol.lower()).inc()
                        for k in batch:
                            try:
                                out.append({
                                    "open_time": k.get("t"),
                                    "close_time": k.get("T"),
                                    "open": float(k.get("o")) if k.get("o") is not None else None,
                                    "high": float(k.get("h")) if k.get("h") is not None else None,
                                    "low": float(k.get("l")) if k.get("l") is not None else None,
                                    "close": float(k.get("c")) if k.get("c") is not None else None,
                                    "volume": float(k.get("v")) if k.get("v") is not None else None,
                                    "is_closed": True,
                                })
                            except Exception:
                                pass
                        if out:
                            if BROADCAST_APPEND_ATTEMPTS:
                                with contextlib.suppress(Exception):
                                    BROADCAST_APPEND_ATTEMPTS.labels(cfg.symbol.lower()).inc()
                            try:
                                await ohlcv_ws_manager.broadcast_append(out)
                                if BROADCAST_APPEND_SENT:
                                    with contextlib.suppress(Exception):
                                        BROADCAST_APPEND_SENT.labels(cfg.symbol.lower()).inc()
                            except Exception:
                                if OHLCV_WS_FLUSH_LISTENER_ERRORS:
                                    with contextlib.suppress(Exception):
                                        OHLCV_WS_FLUSH_LISTENER_ERRORS.labels(cfg.symbol.lower()).inc()
                                pass
                    consumer.add_flush_listener(_on_flush)
            except Exception:
                pass
            started_consumer = _start_task_once("kline_task", consumer.start, label="ingestion")
            # Gap Orchestrator startup (after consumer) - allow opt-out via FAST_STARTUP_EAGER_COMPONENTS
            if started_consumer:
                try:  # pragma: no cover
                    from backend.apps.ingestion.backfill.gap_orchestrator_service import GapOrchestratorService
                    orch = GapOrchestratorService(consumer, cfg.kline_interval, poll_interval=30.0, concurrency=2)
                    await orch.start()
                    app.state.gap_orchestrator = orch
                except Exception as _e:
                    logging.getLogger("api").warning("gap_orchestrator_start_failed err=%s", _e)
    elif cfg.ingestion_enabled and fast:
        app.state.skipped_components.append("ingestion")
    # Auto retrain
    if cfg.auto_retrain_enabled and not fast:
        if db_pool is not None:
            _start_task_once("auto_retrain_task", lambda: auto_retrain_loop(feature_service), label="auto_retrain")
        else:
            if "auto_retrain_db" not in app.state.degraded_components:
                app.state.degraded_components.append("auto_retrain_db")
            _schedule_db_component("auto_retrain_db", lambda: _start_task_once("auto_retrain_task", lambda: auto_retrain_loop(feature_service), label="auto_retrain"))
    elif cfg.auto_retrain_enabled and fast:
        app.state.skipped_components.append("auto_retrain")
    # Inference log queue (lightweight always)
    log_queue = get_inference_log_queue()
    await log_queue.start()
    # Auto labeler
    start_labeler = False
    if cfg.auto_labeler_enabled:
        # Start when not FAST_STARTUP, or when explicitly eager under FAST_STARTUP
        if (not fast) or ("auto_labeler" in getattr(app.state, 'fast_startup_eager', set())):
            start_labeler = True
    if start_labeler:
        if not getattr(get_auto_labeler_service(), '_running', False):
            try:
                await get_auto_labeler_service().start()
            except Exception as e:  # noqa: BLE001
                app.state.degraded_components.append("auto_labeler_start_fail")
                logging.getLogger("api").warning("auto_labeler_start_failed err=%s", e)
    elif cfg.auto_labeler_enabled and fast:
        app.state.skipped_components.append("auto_labeler")
    # Calibration monitor
    if cfg.calibration_monitor_enabled and not fast:
        if db_pool is not None:
            _start_task_once("calibration_monitor_task", calibration_monitor_loop, label="calibration_monitor")
        else:
            if "calibration_monitor_db" not in app.state.degraded_components:
                app.state.degraded_components.append("calibration_monitor_db")
            _schedule_db_component("calibration_monitor_db", lambda: _start_task_once("calibration_monitor_task", calibration_monitor_loop, label="calibration_monitor"))
    elif cfg.calibration_monitor_enabled and fast:
        app.state.skipped_components.append("calibration_monitor")
    # News service (real RSS only; synthetic fallback removed)
    app.state.news_service = NewsService(symbol=cfg.symbol)
    # Hard purge legacy synthetic source (demo_feed) once on startup (idempotent & logged)
    async def _retry_news_schema_purge(delay: float = 3.0):  # background retry helper
        await asyncio.sleep(delay)
        try:
            _repo = NewsRepository()
            try:
                await _repo.ensure_schema()  # type: ignore
            except Exception as re:  # noqa: BLE001
                logging.getLogger("api").warning("news_schema_retry_failed err=%s", re)
                return
            try:
                purged = await _repo.delete_by_source('demo_feed')  # type: ignore
                if purged:
                    logging.getLogger("api").info("purged_legacy_demo_feed_retry rows=%s", purged)
            except Exception:
                pass
        except Exception as outer:  # noqa: BLE001
            logging.getLogger("api").debug("news_schema_retry_wrapper_err err=%s", outer)
    try:
        repo = NewsRepository()
        try:
            await repo.ensure_schema()  # may use fallback direct connect
        except Exception as _e:  # noqa: BLE001
            logging.getLogger("api").warning("news_schema_ensure_failed err=%s (will retry)", _e)
            # schedule background retry so startup continues
            try:
                asyncio.create_task(_retry_news_schema_purge())
            except Exception:
                pass
        else:
            # initial ensure succeeded -> attempt purge
            try:
                deleted = await repo.delete_by_source('demo_feed')  # type: ignore
                if deleted:
                    logging.getLogger("api").info("purged_legacy_demo_feed rows=%s", deleted)
            except Exception as _ie:  # noqa: BLE001
                logging.getLogger("api").warning("demo_feed_purge_failed err=%s", _ie)
    except Exception as _outer:  # noqa: BLE001
        logging.getLogger("api").debug("demo_feed_purge_wrapper_err err=%s", _outer)
    # Resolve poll interval: prefer attr on cfg, else env NEWS_POLL_INTERVAL, else default 90
    try:
        news_poll_interval = getattr(cfg, 'news_poll_interval')  # type: ignore[attr-defined]
    except Exception:
        news_poll_interval = None
    if not isinstance(news_poll_interval, (int, float)):
        with contextlib.suppress(Exception):
            _v = os.getenv('NEWS_POLL_INTERVAL', '90')
            if isinstance(_v, str) and '#' in _v:
                _v = _v.split('#', 1)[0].strip()
            news_poll_interval = float(_v)
    if not isinstance(news_poll_interval, (int, float)):
        news_poll_interval = 90
    app.state.news_poll_interval = news_poll_interval
    if (not fast) or ("news_ingestion" in eager_components):
        _start_task_once("news_task", lambda: news_loop(app.state.news_service, interval=news_poll_interval), label="news_ingestion")
        # If it would otherwise have been skipped but we started eagerly, ensure not in skipped list
        if fast and "news_ingestion" in app.state.skipped_components:
            with contextlib.suppress(ValueError):
                app.state.skipped_components.remove("news_ingestion")
        # Optional automatic bootstrap (non-fast only or explicitly eager) - runs in background fire & forget
        try:
            if not fast:
                _v = os.getenv('NEWS_STARTUP_BOOTSTRAP_DAYS', '0')
                if isinstance(_v, str) and '#' in _v:
                    _v = _v.split('#', 1)[0].strip()
                bs_days = int(float(_v))
                _v = os.getenv('NEWS_STARTUP_BOOTSTRAP_ROUNDS', '0')
                if isinstance(_v, str) and '#' in _v:
                    _v = _v.split('#', 1)[0].strip()
                bs_rounds = int(float(_v))
                _bsv = os.getenv('NEWS_STARTUP_BOOTSTRAP_INTERVAL', '5')
                if isinstance(_bsv, str) and '#' in _bsv:
                    _bsv = _bsv.split('#', 1)[0].strip()
                bs_interval = float(_bsv)
                bs_min_total = os.getenv('NEWS_STARTUP_BOOTSTRAP_MIN_ARTICLES')
                min_total_val = int(bs_min_total) if (bs_min_total and bs_min_total.isdigit()) else None
                if bs_days > 0 and bs_rounds > 0:
                    async def _maybe_bootstrap():
                        svc: NewsService = getattr(app.state, 'news_service')
                        # quick check current total articles - if already have some, skip
                        try:
                            latest_ts = await svc.repo.latest_article_ts()  # type: ignore
                        except Exception:
                            latest_ts = None
                        if latest_ts is not None:
                            return
                        await svc.bootstrap_backfill(
                            rounds=bs_rounds,
                            sleep_seconds=bs_interval,
                            recent_days=bs_days,
                            min_total=min_total_val,
                            per_round_backfill_days=bs_days,
                        )
                    _start_task_once("news_bootstrap_task", _maybe_bootstrap, label="news_bootstrap")
        except Exception as _e:  # noqa: BLE001
            logging.getLogger("api").warning("startup_bootstrap_schedule_failed err=%s", _e)
    else:
        app.state.skipped_components.append("news_ingestion")
    # Promotion alert background loop
    if not fast and cfg.promotion_alert_enabled and cfg.promotion_alert_webhook_url:
        try:
            _start_task_once("promotion_alert_task", promotion_alert_loop, label="promotion_alert")
        except Exception as e:  # noqa: BLE001
            app.state.degraded_components.append("promotion_alert_start_fail")
            logging.getLogger("api").warning("promotion_alert loop start failed: %s", e)
    # Seed instability ratio loop (always safe/lightweight)
    _start_task_once("seed_instability_task", seed_instability_loop, label="seed_instability")
    # Start system health metrics loop
    _start_task_once("system_health_task", system_health_metrics_loop, label="system_health")
    # Auto inference loop
    # Auto inference loop (respect runtime override enable flag if set)
    runtime_enabled = getattr(app.state, 'auto_inference_enabled_override', None)
    should_start = False
    if runtime_enabled is None:
        should_start = cfg.inference_auto_loop_enabled
    else:
        should_start = bool(runtime_enabled)
    if should_start:
        interval_override = getattr(app.state, 'auto_inference_interval_override', None)
        _start_task_once("auto_inference_task", lambda: auto_inference_loop(interval_override=interval_override), label="auto_inference")
    # Optional: automatically run fast_startup upgrade without UI if requested via env
    try:
        if fast and os.getenv("FAST_STARTUP_AUTO_UPGRADE", "false").lower().strip() in ("1","true","yes","on"):
            async def _auto_upgrade():
                try:
                    # short delay to let app.state fill in skipped components
                    await asyncio.sleep(1.0)
                    if getattr(app.state, 'fast_startup', False) and getattr(app.state, 'skipped_components', []):
                        await admin_fast_startup_upgrade()
                        logging.getLogger("api").info("FAST_STARTUP_AUTO_UPGRADE executed")
                except Exception as e:  # noqa: BLE001
                    logging.getLogger("api").warning("FAST_STARTUP_AUTO_UPGRADE failed: %s", e)
            asyncio.create_task(_auto_upgrade())
    except Exception:
        pass
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            await log_queue.stop()
        if cfg.auto_labeler_enabled:
            # Suppress cancellation during clean shutdown of auto labeler
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await get_auto_labeler_service().stop()
        for attr in [
            "feature_task",
            "kline_task",
            "auto_retrain_task",
            "risk_metrics_task",
            "calibration_monitor_task",
            "news_task",
            "promotion_alert_task",
            "seed_instability_task",
            "low_buy_loop_task",
        ]:
            t = getattr(app.state, attr, None)
            if t:
                t.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await t
        # system health loop
        t_sys = getattr(app.state, 'system_health_task', None)
        if t_sys:
            t_sys.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await t_sys
        consumer = getattr(app.state, "kline_consumer", None)
        if consumer:
            with contextlib.suppress(Exception):
                await consumer.stop()
        await close_pool()
app = FastAPI(title="XRP1 Trading System API", version="0.1.1", lifespan=lifespan)
@app.get("/health")
async def health_root():
    # Simple liveness endpoint for generic probes
    return {"status": "ok"}
@app.post("/api/trading/simulate")
async def api_trading_simulate(req: SimulateRequest):
    try:
        if req.mode not in ("mock", "custom"):
            return JSONResponse({"status": "error", "error": "invalid_mode"}, status_code=400)
        if req.mode == "mock":
            n = int(req.n or 300)
            start_price = float(req.start_price or 1.0)
            drift = float(req.drift or 0.0)
            vol = float(req.vol or 0.01)
            prob_noise = float(req.prob_noise or 0.05)
            prices, probs = generate_mock_series(n=n, start_price=start_price, drift=drift, vol=vol, prob_noise=prob_noise, seed=req.seed)
        else:
            if not isinstance(req.prices, list) or not isinstance(req.probs, list) or len(req.prices) != len(req.probs) or len(req.prices) < 2:
                return JSONResponse({"status": "error", "error": "invalid_series"}, status_code=400)
            prices = [float(x) for x in req.prices]
            probs = [float(x) for x in req.probs]
        _cfg = SimConfig(
            base_units=float(req.base_units) if req.base_units is not None else 1.0,
            fee_rate=float(req.fee_rate) if req.fee_rate is not None else float(getattr(cfg, 'trading_fee_taker', 0.001) if (getattr(cfg, 'trading_fee_mode', 'taker') == 'taker') else getattr(cfg, 'trading_fee_maker', 0.001)),
            threshold=float(req.threshold) if req.threshold is not None else 0.5,
            cooldown_sec=float(req.cooldown_sec or 0.0),
            allow_scale_in=bool(req.allow_scale_in) if req.allow_scale_in is not None else True,
            si_ratio=float(req.si_ratio or 0.5),
            si_max_legs=int(req.si_max_legs or 3),
            si_min_price_move=float(req.si_min_price_move or 0.0),
            si_cooldown_sec=float(req.si_cooldown_sec or 0.0),
            exit_require_net_profit=bool(req.exit_require_net_profit) if req.exit_require_net_profit is not None else True,
            exit_slice_seconds=float(req.exit_slice_seconds or 0.0),
        )
        res = simulate_trading(prices, probs, _cfg)
        return JSONResponse(res)
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


@app.get("/api/trading/autopilot/state")
async def api_trading_autopilot_state():
    try:
        state = await _autopilot_service.get_state()
        return JSONResponse({"status": "ok", "state": state.model_dump()})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


@app.get("/api/trading/autopilot/performance")
async def api_trading_autopilot_performance():
    try:
        perf = await _autopilot_service.get_performance()
        return JSONResponse({"status": "ok", "performance": perf.model_dump()})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": str(e)}, status_code=500)


@app.get("/api/trading/autopilot/stream")
async def api_trading_autopilot_stream():
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }

    async def event_gen():
        try:
            async for raw in _autopilot_service.iter_events():
                yield f"data: {raw}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(event_gen(), headers=headers)


@app.get("/api/trading/signals/recent")
async def api_trading_signals_recent(limit: int = 100, signal_type: Optional[str] = None):
    if limit < 1 or limit > 500:
        return JSONResponse({"status": "error", "error": "limit_out_of_range"}, status_code=400)
    from backend.apps.trading.repository.signal_repository import TradingSignalRepository  # local import to avoid circulars

    repo = TradingSignalRepository()
    try:
        rows = await repo.fetch_recent(limit=limit, signal_type=signal_type)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": f"fetch_failed:{exc}"}, status_code=500)

    def _to_ts(value):
        try:
            if value is None:
                return None
            if hasattr(value, "timestamp"):
                return float(value.timestamp())
            return float(value)
        except Exception:
            return None

    items = []
    for row in rows:
        items.append({
            "id": row.get("id"),
            "signal_type": row.get("signal_type"),
            "status": row.get("status"),
            "price": row.get("price"),
            "params": row.get("params"),
            "extra": row.get("extra"),
            "order_side": row.get("order_side"),
            "order_size": row.get("order_size"),
            "order_price": row.get("order_price"),
            "error": row.get("error"),
            "created_ts": _to_ts(row.get("created_at")),
            "executed_ts": _to_ts(row.get("executed_at")),
        })

    return JSONResponse({"status": "ok", "signals": items})


@app.get("/api/trading/signals/timeline")
async def api_trading_signals_timeline(
    limit: int = 200,
    from_ts: Optional[float] = None,
    to_ts: Optional[float] = None,
    signal_type: Optional[str] = None,
    _auth: bool = Depends(require_api_key),
):
    if limit < 1 or limit > 1000:
        return JSONResponse({"status": "error", "error": "limit_out_of_range"}, status_code=400)
    try:
        from backend.apps.trading.repository.signal_timeline_repository import SignalTimelineRepository
        repo = SignalTimelineRepository()
        rows = await repo.fetch_timeline(limit=limit, from_ts=from_ts, to_ts=to_ts, signal_type=signal_type)
        return JSONResponse({"status": "ok", "timeline": rows})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": f"timeline_fetch_failed:{e}"}, status_code=500)


@app.delete("/api/trading/signals")
async def api_trading_signals_delete(signal_type: Optional[str] = None):
    from backend.apps.trading.repository.signal_repository import TradingSignalRepository  # local import to avoid circulars

    repo = TradingSignalRepository()
    try:
        deleted = await repo.delete_all(signal_type)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": f"delete_failed:{exc}"}, status_code=500)

    # Clearing signals should also drop any active autopilot signal in memory when the latest status was stored there.
    try:
        await _autopilot_service.clear_signal("trading_signals_cleared")
    except Exception:
        pass

    return JSONResponse({"status": "ok", "deleted": deleted, "signal_type": signal_type})

# --- Admin retention/purge endpoints ---
@app.post("/admin/trading/purge", dependencies=[Depends(require_api_key)])
async def admin_trading_purge(
    older_than_days: int = 30,
    max_rows: int = 5000,
    signal_type: Optional[str] = None,
    event_type: Optional[str] = None,
):
    """
    Purge old trading signals and autopilot events older than the given days.
    Applies a per-table max row cap to avoid long locks; call repeatedly for large cleanups.
    """
    if older_than_days < 1 or older_than_days > 3650:
        return JSONResponse({"status": "error", "error": "invalid_days"}, status_code=400)
    before_ts = (time.time() - older_than_days * 86400)
    deleted_signals = 0
    deleted_events = 0
    try:
        from backend.apps.trading.repository.signal_repository import TradingSignalRepository
        from backend.apps.trading.repository.autopilot_repository import AutopilotRepository
        srepo = TradingSignalRepository()
        arepo = AutopilotRepository()
        deleted_signals = await srepo.delete_older_than(before_ts=before_ts, signal_type=signal_type, max_rows=max_rows)
        deleted_events = await arepo.delete_events_older_than(before_ts=before_ts, event_type=event_type, max_rows=max_rows)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": f"purge_failed:{e}"}, status_code=500)
    return JSONResponse({
        "status": "ok",
        "older_than_days": older_than_days,
        "max_rows": max_rows,
        "deleted": {
            "trading_signals": deleted_signals,
            "autopilot_event_log": deleted_events,
        },
        "filters": {
            "signal_type": signal_type,
            "event_type": event_type,
        }
    })

# --- Autopilot persistence (events & state history) ---
@app.get("/api/trading/autopilot/events")
async def api_trading_autopilot_events(
    limit: int = 100,
    from_ts: Optional[float] = None,
    to_ts: Optional[float] = None,
    event_type: Optional[str] = None,
    before_id: Optional[int] = None,
):
    try:
        from backend.apps.trading.repository.autopilot_repository import AutopilotRepository
        repo = AutopilotRepository()
        rows = await repo.fetch_events(
            limit=limit,
            from_ts=from_ts,
            to_ts=to_ts,
            event_type=event_type,
            before_id=before_id,
        )
        return JSONResponse({"status": "ok", "events": rows})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": f"events_fetch_failed:{e}"}, status_code=500)


@app.get("/api/trading/autopilot/state/history")
async def api_trading_autopilot_state_history(
    limit: int = 200,
    from_ts: Optional[float] = None,
    to_ts: Optional[float] = None,
    before_id: Optional[int] = None,
):
    if limit < 1 or limit > 2000:
        return JSONResponse({"status": "error", "error": "limit_out_of_range"}, status_code=400)
    try:
        from backend.apps.trading.repository.autopilot_repository import AutopilotRepository
        repo = AutopilotRepository()
        rows = await repo.fetch_state_history(from_ts=from_ts, to_ts=to_ts, before_id=before_id, limit=limit)
        return JSONResponse({"status": "ok", "snapshots": rows})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "error": f"state_history_failed:{e}"}, status_code=500)
# Live trading defaults
app.state.live_trading_enabled = False
_v = os.getenv("LIVE_TRADE_BASE_SIZE", "10")
if isinstance(_v, str) and '#' in _v:
    _v = _v.split('#', 1)[0].strip()
app.state.live_trading_base_size = float(_v)
_v = os.getenv("LIVE_TRADE_COOLDOWN_SEC", "60")
if isinstance(_v, str) and '#' in _v:
    _v = _v.split('#', 1)[0].strip()
app.state.live_trading_cooldown_sec = int(float(_v))
app.state.live_trading_last_ts = 0.0
app.state.live_allow_short = False

# Scale-in defaults and lifecycle policy (read from env)
app.state.live_allow_scale_in = bool(os.getenv("ALLOW_SCALE_IN", "1").lower() in ("1","true","yes","y"))
_v = os.getenv("SCALE_IN_SIZE_RATIO", "1.0")
if isinstance(_v, str) and '#' in _v:
    _v = _v.split('#', 1)[0].strip()
app.state.live_scale_in_size_ratio = float(_v)
_v = os.getenv("MAX_SCALEINS", "20")
if isinstance(_v, str) and '#' in _v:
    _v = _v.split('#', 1)[0].strip()
app.state.live_scale_in_max_legs = int(float(_v))
# MIN_ADD_DISTANCE_BPS => fraction (e.g., 25 bps = 0.0025)
try:
    _v = os.getenv("MIN_ADD_DISTANCE_BPS", "0")
    if isinstance(_v, str) and '#' in _v:
        _v = _v.split('#', 1)[0].strip()
    app.state.live_scale_in_min_price_move = float(_v) / 10000.0
except Exception:
    app.state.live_scale_in_min_price_move = 0.0
# Probability-delta gate (disabled when 0). Can be set via SI_PROB_DELTA_MIN or SCALE_IN_PROB_DELTA_GATE
try:
    _v = os.getenv("SI_PROB_DELTA_MIN", os.getenv("SCALE_IN_PROB_DELTA_GATE", "0"))
    if isinstance(_v, str) and '#' in _v:
        _v = _v.split('#', 1)[0].strip()
    app.state.live_scale_in_prob_delta_gate = float(_v)
except Exception:
    app.state.live_scale_in_prob_delta_gate = 0.0
# Gate mode: 'and' or 'or' across active gates (default: 'or' for backward compatibility)
app.state.live_scale_in_gate_mode = str(os.getenv("SCALE_IN_GATE_MODE", "or")).lower()
_v = os.getenv("SCALE_IN_COOLDOWN_SEC", str(app.state.live_trading_cooldown_sec))
if isinstance(_v, str) and '#' in _v:
    _v = _v.split('#', 1)[0].strip()
app.state.live_scale_in_cooldown_sec = int(float(_v))
# Freeze-on-exit: when exit/flat decided, disallow further scale-ins
app.state.live_scale_in_freeze_on_exit = bool(os.getenv("SCALEIN_FREEZE_ON_EXIT", "1").lower() in ("1","true","yes","y"))
# Exit slice spacing (seconds). 0 disables slicing
_v = os.getenv("EXIT_SLICE_SECONDS", "0")
if isinstance(_v, str) and '#' in _v:
    _v = _v.split('#', 1)[0].strip()
app.state.exit_slice_seconds = int(float(_v))
# Require net-profit-only exit (after fees). Default true.
app.state.exit_require_net_profit = bool(os.getenv("EXIT_REQUIRE_NET_PROFIT", "1").lower() in ("1","true","yes","y"))
# Force exit whenever net-profit is positive (bypass cooldown/risk for close). Default true per user request
app.state.exit_force_on_net_profit = bool(os.getenv("EXIT_FORCE_ON_NET_PROFIT", "1").lower() in ("1","true","yes","y"))
# Profit check mode: 'net' (>= breakeven incl. fees) or 'gross' (price >= entry)
app.state.exit_profit_check_mode = str(os.getenv("EXIT_PROFIT_CHECK_MODE", "net")).lower()
# Trailing TP percent (fraction, e.g., 0.02) and Max holding seconds
try:
    _v = os.getenv("TRAILING_TP_PCT", "0")
    if isinstance(_v, str) and '#' in _v:
        _v = _v.split('#', 1)[0].strip()
    app.state.live_trailing_take_profit_pct = float(_v)
except Exception:
    app.state.live_trailing_take_profit_pct = 0.0
try:
    _v = os.getenv("MAX_HOLDING_SECONDS", "0")
    if isinstance(_v, str) and '#' in _v:
        _v = _v.split('#', 1)[0].strip()
    app.state.live_max_holding_seconds = int(float(_v))
except Exception:
    app.state.live_max_holding_seconds = 0

# CORS for local frontend dev (Vite default port 5173) and fallback localhost:3000
frontend_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
logger = logging.getLogger("api")

# If RISK_STARTING_EQUITY env is set, align baseline at startup (best-effort)
try:
    _risk_start_env = os.getenv("RISK_STARTING_EQUITY")
    if _risk_start_env:
        try:
            _risk_start_val = float(_risk_start_env)
            # Align in-memory; persistence occurs on first mutate or explicit reset endpoint call
            risk_engine.session.starting_equity = _risk_start_val
            risk_engine.session.peak_equity = _risk_start_val
            risk_engine.session.current_equity = _risk_start_val
        except Exception:
            pass
except Exception:
    pass

# --- Runtime control: Auto Inference Loop ------------------------------------
import uuid  # ensure available early for request ids

# --- OHLCV Monitoring ---
@app.get("/api/ohlcv/recent")
async def api_ohlcv_recent(
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    limit: int = 120,
    include_open: Optional[bool] = None,
    debug: Optional[bool] = None,
):
    """최근 OHLCV 캔들 + 단순 메트릭 반환 (canonical: ohlcv_candles).

    파라미터:
      symbol / interval: 미지정 시 기본 설정(cfg) 값 사용
      limit: 최근 N개 (1~1000 안전 구간, 내부 fetch에서 clamp)
      include_open: True면 아직 마감(is_closed=False)되지 않은 진행중(partial) 마지막 캔들도 포함

    메트릭 정의:
      pct_change: (마지막 종가 - 첫 종가)/첫 종가
      avg_volume: 단순 평균 거래량
      volatility: 로그 수익률 표본 표준편차 (연속 클로즈 간 log(b/a))

    응답 정렬:
      candles 는 open_time 오름차순(과거→최근). partial 포함 시 마지막 요소 is_closed=False 가능.

    검증(내부 로직 주석):
      1) len(candles) == count
      2) open_time strictly 증가
      3) include_open=False 인 경우 마지막 is_closed=True 보장
    """
    sym = symbol or cfg.symbol
    itv = interval or cfg.kline_interval
    if include_open is None:
        include_open = cfg.ohlcv_include_open_default
    rows = await fetch_kline_recent(sym, itv, limit=limit)
    candles = list(reversed(rows))  # open_time ASC

    # partial(미종가) 캔들 제거 정책 적용
    if not include_open and candles:
        last = candles[-1]
        if last.get("is_closed") is False:
            candles = candles[:-1]

    import math
    pct_change: Optional[float] = None
    avg_volume: Optional[float] = None
    volatility: Optional[float] = None
    closes: list[float] = []
    vols: list[float] = []
    for c in candles:
        cl = c.get("close")
        vol = c.get("volume")
        if isinstance(cl, (int, float)):
            closes.append(float(cl))
        if isinstance(vol, (int, float)):
            vols.append(float(vol))
    if len(closes) >= 2 and closes[0] not in (0, None):
        try:
            pct_change = (closes[-1] - closes[0]) / closes[0]
        except Exception:
            pct_change = None
    if vols:
        avg_volume = sum(vols) / len(vols)
    if len(closes) >= 3:
        rets: list[float] = []
        for a, b in zip(closes, closes[1:]):
            try:
                if a > 0 and b > 0:
                    rets.append(math.log(b / a))
            except Exception:
                pass
        if len(rets) > 1:
            mu = sum(rets) / len(rets)
            var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
            volatility = math.sqrt(var)

    # 내부 검증 (로그 혹은 디버그 용; 여기서는 단순 논리 체크만 수행)
    if candles:
        # 정렬 검사
        for i in range(1, len(candles)):
            if candles[i]["open_time"] <= candles[i-1]["open_time"]:  # pragma: no cover (논리 오류 감지용)
                logger.warning("[ohlcv_recent] 정렬 오류 감지 open_time=%s <= %s", candles[i]["open_time"], candles[i-1]["open_time"])
                break
        if not include_open and candles and candles[-1].get("is_closed") is False:  # pragma: no cover
            logger.warning("[ohlcv_recent] include_open=False 옵션 불구하고 partial 잔존")

    resp = {
        "symbol": sym,
        "interval": itv,
        "count": len(candles),
        "candles": candles,
        "metrics": {"pct_change": pct_change, "avg_volume": avg_volume, "volatility": volatility},
        "include_open": include_open,
    }
    if debug:
        import inspect
        # FastAPI dependency body가 없는 단순 함수라 request.query_params 직접 접근 없이 include_open param 자체를 raw로 보긴 어려움
        # 우회: inspect.signature 이용해 호출 시 전달된 locals 에서 원시 값 추출
        raw_include_open = None
        try:
            raw_include_open = locals().get('include_open')
        except Exception:
            pass
        resp["_debug"] = {
            "cfg_ohlcv_include_open_default": cfg.ohlcv_include_open_default,
            "cfg_ohlcv_ws_snapshot_include_open": cfg.ohlcv_ws_snapshot_include_open,
            "applied_include_open": include_open,
            "provided_include_open_param": raw_include_open,
        }
    return resp


# ---------------------------------------------------------------------------
# OHLCV Meta 정보
# ---------------------------------------------------------------------------
@app.get("/api/ohlcv/meta")
async def api_ohlcv_meta(
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    sample_for_gap: int = 2000,
):
    """OHLCV 메타 데이터(earliest/latest/count/completeness/최대 갭) 반환.

    파라미터:
      symbol / interval: 미지정 시 기본 설정 값 사용
      sample_for_gap: 최대 갭 계산을 위해 최근 N개(open_time DESC) 샘플만 스캔 (성능 보호)

    계산 로직:
      earliest_open_time = MIN(open_time)
      latest_open_time   = MAX(open_time)
      count = 전체 캔들 수
      expected = ((latest - earliest)/interval_ms) + 1  (정수 기반)
      completeness_percent = count / expected (expected>0 일 때)
      largest_gap: 샘플 목록의 인접 open_time 차이 중 interval_ms 보다 큰 최대 구간
        largest_gap_bars = (delta // interval_ms) - 1 (누락된 바 수)
        largest_gap_span_ms = delta - interval_ms

    제약:
      - 매우 긴 히스토리에서도 빠른 응답을 위해 gap 탐지는 최근 sample_for_gap 개만 사용.
      - 향후 GapBackfillService 확장 시 정확도 향상(전체 범위 기반) 가능.
    """
    sym = (symbol or cfg.symbol)
    itv = (interval or cfg.kline_interval)

    def _interval_to_ms(it: str) -> int:
        try:
            unit = it[-1]
            val = int(it[:-1])
            if unit == 'm':
                return val * 60_000
            if unit == 'h':
                return val * 60 * 60_000
            if unit == 'd':
                return val * 24 * 60 * 60_000
        except Exception:  # pragma: no cover
            pass
        return 60_000  # fallback 1m

    interval_ms = _interval_to_ms(itv)

    from backend.common.db.connection import init_pool as _init_pool
    pool = await _init_pool()
    if pool is None:
        return {"status": "db_unavailable"}
    earliest_open = None
    latest_open = None
    total_count = 0
    completeness_365d: Optional[float] = None
    largest_gap_bars = 0
    largest_gap_span_ms = 0
    completeness_percent: Optional[float] = None

    async with pool.acquire() as conn:  # type: ignore
        row = await conn.fetchrow(
            """
            SELECT MIN(open_time) AS earliest, MAX(open_time) AS latest, COUNT(*) AS cnt
            FROM ohlcv_candles
            WHERE symbol=$1 AND interval=$2
            """,
            sym.upper(), itv,
        )
        if row and row["cnt"] > 0:
            earliest_open = int(row["earliest"]) if row["earliest"] is not None else None
            latest_open = int(row["latest"]) if row["latest"] is not None else None
            total_count = int(row["cnt"]) if row["cnt"] is not None else 0
            if earliest_open is not None and latest_open is not None and latest_open >= earliest_open:
                expected = ((latest_open - earliest_open) // interval_ms) + 1
                if expected > 0:
                    completeness_percent = total_count / expected

        # 365d trailing completeness: count bars within (latest_open_time - 365d) .. latest_open_time
        if latest_open is not None:
            window_start = latest_open - (365 * 24 * 60 * 60 * 1000)
            row_365 = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt FROM ohlcv_candles
                WHERE symbol=$1 AND interval=$2 AND open_time BETWEEN $3 AND $4
                """,
                sym.upper(), itv, window_start, latest_open,
            )
            if row_365 and row_365["cnt"] is not None:
                bars_365 = int(row_365["cnt"])
                expected_365 = ((latest_open - window_start) // interval_ms) + 1
                if expected_365 > 0:
                    completeness_365d = bars_365 / expected_365

        # 갭 샘플 스캔 (최근 sample_for_gap 개 DESC → ASC 로 변환)
        if total_count > 1:
            limit_scan = min(sample_for_gap, total_count)
            rows_scan = await conn.fetch(
                """
                SELECT open_time FROM ohlcv_candles
                WHERE symbol=$1 AND interval=$2
                ORDER BY open_time DESC
                LIMIT $3
                """,
                sym.upper(), itv, limit_scan,
            )
            scan_list = [int(r["open_time"]) for r in rows_scan]
            scan_list.reverse()  # ASC
            prev = None
            for ot in scan_list:
                if prev is not None:
                    delta = ot - prev
                    if delta > interval_ms:
                        missing_bars = (delta // interval_ms) - 1
                        if missing_bars > largest_gap_bars:
                            largest_gap_bars = missing_bars
                            largest_gap_span_ms = delta - interval_ms
                prev = ot

    response = {
        "symbol": sym,
        "interval": itv,
        "earliest_open_time": earliest_open,
        "latest_open_time": latest_open,
        "count": total_count,
        "completeness_percent": completeness_percent,
        "completeness_365d_percent": completeness_365d,
        "largest_gap_bars": largest_gap_bars,
        "largest_gap_span_ms": largest_gap_span_ms,
        "sample_for_gap": sample_for_gap,
    }
    # Prometheus gauge 반영 (백필 루프 이전 초기 호출에서도 노출되도록)
    try:  # pragma: no cover
        from backend.apps.ingestion.backfill.gap_backfill_service import (
            OHLCV_COMPLETENESS,
            OHLCV_LARGEST_GAP_BARS,
            OHLCV_LARGEST_GAP_SPAN_MS,
            OHLCV_COMPLETENESS_365D,
        )
        if completeness_percent is not None:
            OHLCV_COMPLETENESS.labels(sym.upper(), itv).set(completeness_percent)
        if completeness_365d is not None:
            OHLCV_COMPLETENESS_365D.labels(sym.upper(), itv).set(completeness_365d)
        OHLCV_LARGEST_GAP_BARS.labels(sym.upper(), itv).set(largest_gap_bars)
        OHLCV_LARGEST_GAP_SPAN_MS.labels(sym.upper(), itv).set(largest_gap_span_ms)
    except Exception:
        pass
    return response

# ---------------------------------------------------------------------------
# OHLCV History (cursor 기반 페이징, ASC 반환)
# ---------------------------------------------------------------------------
@app.get("/api/ohlcv/history")
async def api_ohlcv_history(
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    limit: int = 500,
    before_open_time: Optional[int] = None,
    after_open_time: Optional[int] = None,
    include_open: Optional[bool] = None,
):
    """히스토리 캔들 조회 (open_time 오름차순) + 커서 페이징.

    파라미터:
      symbol/interval: 기본 설정값 대체
      limit: 1~2000 (기본 500)
      before_open_time: 해당 시각(미포함) 보다 이전(과거) 구간을 뒤로 탐색 (이전 페이지)
      after_open_time:  해당 시각(미포함) 보다 이후(미래쪽) 구간을 앞으로 탐색 (앞 페이지)
        ※ before / after 는 동시에 사용할 수 없음 (동시 지정 시 400)
      include_open: True 면 마지막 partial(진행중) 캔들을 필요 시 추가 (only when newest segment)

    반환:
      candles: open_time ASC
      count: 실 반환 수
      next_before_open_time: 다음(더 과거) 페이지 요청 시 사용할 open_time 커서 (없으면 null)
      next_after_open_time: 다음(더 최근) 페이지 요청 시 사용할 커서 (after 모드에서만)

    정책:
      - 기본적으로 is_closed = true 만 반환 (확정된 캔들)  
      - include_open=True & 최신 페이지(after/before 둘 다 미사용)일 때 진행중 캔들 1개 끝에 덧붙임
    """
    if before_open_time and after_open_time:
        raise HTTPException(status_code=400, detail="before_open_time 과 after_open_time 은 동시에 사용할 수 없습니다")
    sym = (symbol or cfg.symbol)
    itv = (interval or cfg.kline_interval)
    limit = max(1, min(2000, limit))
    if include_open is None:
        include_open = cfg.ohlcv_include_open_default

    from backend.common.db.connection import init_pool as _init_pool
    pool = await _init_pool()
    if pool is None:
        return {"status": "db_unavailable"}

    candles: list[dict[str, Any]] = []
    newest_page = (before_open_time is None and after_open_time is None)

    base_sql = "SELECT open_time, close_time, open, high, low, close, volume, trade_count, taker_buy_volume, taker_buy_quote_volume, is_closed FROM ohlcv_candles WHERE symbol=$1 AND interval=$2"
    params: list[Any] = [sym.upper(), itv]
    where_extra = " AND is_closed = true"

    order_clause = " ORDER BY open_time DESC"  # fetch DESC 후 reverse → ASC (before / 기본 페이지)
    cursor_mode = "default"
    if before_open_time:
        # 과거 페이지: before 커서보다 작은 (이전) 것 DESC로 가져와 reverse
        params.append(before_open_time)
        where_extra += " AND open_time < $3"
        cursor_mode = "before"
    elif after_open_time:
        # 미래(최근) 방향 페이지: after 커서보다 큰 것 ASC 직접 조회
        params.append(after_open_time)
        where_extra += " AND open_time > $3"
        order_clause = " ORDER BY open_time ASC"  # 이미 ASC 로 가져옴
        cursor_mode = "after"

    sql = base_sql + where_extra + order_clause + f" LIMIT {limit}"

    async with pool.acquire() as conn:  # type: ignore
        rows = await conn.fetch(sql, *params)
        # rows 순서: cursor_mode 가 default/before 면 DESC, after 면 ASC
        if cursor_mode in ("default", "before"):
            rows = list(rows)
            rows.reverse()
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            for num_key in ["open","high","low","close","volume","taker_buy_volume","taker_buy_quote_volume"]:
                v = d.get(num_key)
                if v is not None:
                    try:
                        d[num_key] = float(v)
                    except Exception:
                        pass
            candles.append(d)

        # 최신 페이지 + include_open=True 인 경우 진행중 캔들 1개 추가 시도
        if newest_page and include_open:
            row_partial = await conn.fetchrow(
                """
                SELECT open_time, close_time, open, high, low, close, volume, trade_count, taker_buy_volume, taker_buy_quote_volume, is_closed
                FROM ohlcv_candles
                WHERE symbol=$1 AND interval=$2 AND is_closed=false
                ORDER BY open_time DESC
                LIMIT 1
                """,
                sym.upper(), itv,
            )
            if row_partial:
                p = {k: row_partial[k] for k in row_partial.keys()}
                for num_key in ["open","high","low","close","volume","taker_buy_volume","taker_buy_quote_volume"]:
                    v = p.get(num_key)
                    if v is not None:
                        try:
                            p[num_key] = float(v)
                        except Exception:
                            pass
                # 중복 방지: 마지막 open_time != partial open_time
                if (not candles) or candles[-1]["open_time"] != p["open_time"]:
                    candles.append(p)

    count = len(candles)
    next_before_open_time = None
    next_after_open_time = None
    if count:
        # 과거 페이지 커서: 가장 이른 open_time
        next_before_open_time = candles[0]["open_time"] if count > 0 else None
        # after 모드일 때는 가장 늦은 open_time을 다음 after 커서로 활용
        if cursor_mode == "after":
            next_after_open_time = candles[-1]["open_time"]
        elif cursor_mode in ("default", "before") and not before_open_time:
            # 기본(최신) 페이지의 after 커서 = 마지막 open_time
            next_after_open_time = candles[-1]["open_time"]

    # 정렬/연속성 검증 로그 (간단)
    for i in range(1, count):
        if candles[i]["open_time"] <= candles[i-1]["open_time"]:  # pragma: no cover
            logger.warning("[ohlcv_history] 정렬 이상: %s <= %s", candles[i]["open_time"], candles[i-1]["open_time"]) 
            break

    return {
        "symbol": sym,
        "interval": itv,
        "count": count,
        "candles": candles,
        "next_before_open_time": next_before_open_time,
        "next_after_open_time": next_after_open_time,
        "cursor_mode": cursor_mode,
        "include_open": include_open,
    }

@app.get("/api/ohlcv/features/preview")
async def api_ohlcv_features_preview(
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    limit: int = 300,
    horizon: int = 5,
    sentiment_window: int = 60,
    sentiment_window_secondary: Optional[int] = 15,
    dynamic_sentiment: bool = False,
):
    """Compute ad-hoc OHLCV + sentiment feature rows with forward-return label (not persisted).

    Params:
      symbol, interval: override defaults (falls back to config values)
      limit: number of recent candles to pull (ascending processing after fetch)
      horizon: future candle distance for label (future return > 0 -> 1 else 0)
      sentiment_window: minutes window for sentiment aggregation (single snapshot applied to all rows)

    Returns:
      { samples: [...], meta: {symbol, interval, limit_used, horizon, sentiment_window, sample_count} }
    """
    sym = symbol or cfg.symbol
    itv = interval or cfg.kline_interval
    raw_rows = await fetch_kline_recent(sym, itv, limit=limit)
    candles = list(reversed(raw_rows))  # ascending
    meta: dict[str, Any] = {}
    sentiment_map: dict[int, dict] = {}
    sentiment_map_secondary: dict[int, dict] = {}
    if not candles:
        return {
            "symbol": sym,
            "interval": itv,
            "limit_used": limit,
            "horizon": horizon,
            "sentiment_window": sentiment_window,
            "sample_count": 0,
            "dynamic_sentiment_used": False,
            "samples": [],
        }
    # Guard: very large limit with dynamic sentiment -> clamp to avoid heavy DB load first iteration
    if dynamic_sentiment and limit > 800:
        meta["limit_clamped"] = 800
        raw_rows = await fetch_kline_recent(sym, itv, limit=800)
        candles = list(reversed(raw_rows))
    if dynamic_sentiment:
        # Efficient single fetch of all news sentiments in required time span
        try:
            earliest_close_ms = candles[0].get("close_time")
            latest_close_ms = candles[-1].get("close_time")
            if isinstance(earliest_close_ms, int) and isinstance(latest_close_ms, int):
                window_sec = sentiment_window * 60
                window_sec_secondary = sentiment_window_secondary * 60 if sentiment_window_secondary else None
                earliest_cutoff_sec = max(0, (earliest_close_ms // 1000) - window_sec)
                latest_close_sec = latest_close_ms // 1000
                from backend.common.db.connection import init_pool as _init_pool
                pool = await _init_pool()
                articles: list[dict[str, Any]] = []
                if pool is not None:
                    async with pool.acquire() as conn:  # type: ignore
                        if sym:
                            rows_news = await conn.fetch(
                                """
                                SELECT published_ts, sentiment FROM news_articles
                                WHERE published_ts BETWEEN $1 AND $2
                                  AND sentiment IS NOT NULL
                                  AND symbol = $3
                                ORDER BY published_ts ASC
                                """,
                                earliest_cutoff_sec, latest_close_sec, sym,
                            )
                        else:
                            rows_news = await conn.fetch(
                                """
                                SELECT published_ts, sentiment FROM news_articles
                                WHERE published_ts BETWEEN $1 AND $2
                                  AND sentiment IS NOT NULL
                                ORDER BY published_ts ASC
                                """,
                                earliest_cutoff_sec, latest_close_sec,
                            )
                        for r in rows_news:
                            try:
                                articles.append({
                                    "published_ts": int(r["published_ts"]),
                                    "sentiment": float(r["sentiment"]),
                                })
                            except Exception:
                                pass
                # Sliding window pointers
                import collections
                # Primary window deques
                dq = collections.deque()  # store sentiments within primary window
                sum_sent = 0.0; pos = 0; neg = 0
                # Secondary window deques (optional)
                dq2 = collections.deque() if window_sec_secondary else None
                sum_sent2 = 0.0; pos2 = 0; neg2 = 0
                # We'll iterate candles in ascending order; maintain pointer over articles
                art_idx = 0
                total_articles = len(articles)
                for c in candles:
                    ct_ms = c.get("close_time")
                    if not isinstance(ct_ms, int):
                        continue
                    ct_sec = ct_ms // 1000
                    cutoff = ct_sec - window_sec
                    cutoff2 = (ct_sec - window_sec_secondary) if window_sec_secondary else None
                    # Push new articles up to ct_sec
                    while art_idx < total_articles and articles[art_idx]["published_ts"] <= ct_sec:
                        a = articles[art_idx]
                        dq.append(a)
                        s = a["sentiment"]
                        sum_sent += s
                        if s > 0.05:
                            pos += 1
                        elif s < -0.05:
                            neg += 1
                        if dq2 is not None:
                            dq2.append(a)
                            sum_sent2 += s
                            if s > 0.05:
                                pos2 += 1
                            elif s < -0.05:
                                neg2 += 1
                        art_idx += 1
                    # Pop old articles
                    while dq and dq[0]["published_ts"] < cutoff:
                        old = dq.popleft()
                        s_old = old["sentiment"]
                        sum_sent -= s_old
                        if s_old > 0.05:
                            pos -= 1
                        elif s_old < -0.05:
                            neg -= 1
                    if dq2 is not None and cutoff2 is not None:
                        while dq2 and dq2[0]["published_ts"] < cutoff2:
                            old2 = dq2.popleft()
                            s_old2 = old2["sentiment"]
                            sum_sent2 -= s_old2
                            if s_old2 > 0.05:
                                pos2 -= 1
                            elif s_old2 < -0.05:
                                neg2 -= 1
                    count = len(dq)
                    neutral = count - pos - neg if count >= (pos + neg) else 0
                    avg = (sum_sent / count) if count else None
                    sentiment_map[ct_ms] = {
                        "window_minutes": sentiment_window,
                        "count": count,
                        "avg": avg,
                        "pos": pos,
                        "neg": neg,
                        "neutral": neutral,
                        "symbol": sym,
                    }
                    if dq2 is not None:
                        count2 = len(dq2)
                        neutral2 = count2 - pos2 - neg2 if count2 >= (pos2 + neg2) else 0
                        avg2 = (sum_sent2 / count2) if count2 else None
                        sentiment_map_secondary[ct_ms] = {
                            "window_minutes": sentiment_window_secondary,
                            "count": count2,
                            "avg": avg2,
                            "pos": pos2,
                            "neg": neg2,
                            "neutral": neutral2,
                            "symbol": sym,
                        }
                meta["dynamic_sentiment_used"] = True
                meta["sentiment_article_total"] = total_articles
            else:
                meta["dynamic_sentiment_used"] = False
        except Exception as e:  # fallback to static if failure
            meta["dynamic_sentiment_error"] = str(e)
            meta["dynamic_sentiment_used"] = False
    if not dynamic_sentiment or not sentiment_map:
        # Static single snapshot fallback
        try:
            from backend.apps.news.repository.news_repository import NewsRepository
            news_repo = NewsRepository()
            sent = await news_repo.sentiment_window_stats(minutes=sentiment_window, symbol=sym)
            sent2 = None
            if sentiment_window_secondary:
                try:
                    sent2 = await news_repo.sentiment_window_stats(minutes=sentiment_window_secondary, symbol=sym)
                except Exception:
                    sent2 = None
        except Exception:
            sent = {"avg": None, "count": 0, "pos": 0, "neg": 0, "neutral": 0}
            sent2 = None
        for c in candles:
            ct = c.get("close_time")
            if isinstance(ct, int):
                sentiment_map[ct] = sent
                if sent2:
                    sentiment_map_secondary[ct] = sent2
        if "dynamic_sentiment_used" not in meta:
            meta["dynamic_sentiment_used"] = False
    # Build dataset samples
    from backend.apps.features.service.dataset_builder import build_samples
    # Merge secondary window features by suffix when available
    samples = build_samples(candles, sentiment_map, horizon=horizon)
    if sentiment_map_secondary:
        # augment each sample with secondary window fields using suffix _w2
        for s in samples:
            ct = s.get("close_time")
            if isinstance(ct, int) and ct in sentiment_map_secondary:
                sm2 = sentiment_map_secondary[ct]
                s["sent_avg_w2"] = sm2.get("avg")
                s["sent_count_w2"] = sm2.get("count")
                s["sent_pos_w2"] = sm2.get("pos")
                s["sent_neg_w2"] = sm2.get("neg")
                s["sent_neutral_w2"] = sm2.get("neutral")
                s["sent_window_w2"] = sm2.get("window_minutes")
                # Derived difference & ratio (short vs long window)
                avg_long = s.get("sent_avg")
                avg_short = s.get("sent_avg_w2")
                if isinstance(avg_long, (int,float)) and isinstance(avg_short, (int,float)) and avg_long is not None and avg_short is not None:
                    try:
                        s["sent_avg_diff_w2"] = avg_short - avg_long
                        if abs(avg_long) > 1e-9:
                            s["sent_avg_ratio_w2"] = avg_short / (avg_long if avg_long != 0 else 1e-9)
                    except Exception:
                        pass
                # Pos/neg ratios (stability guarded)
                pos_short = s.get("sent_pos_w2"); neg_short = s.get("sent_neg_w2")
                pos_long = s.get("sent_pos"); neg_long = s.get("sent_neg")
                def _safe_ratio(a,b):
                    try:
                        if isinstance(a,(int,float)) and isinstance(b,(int,float)) and b not in (0,None):
                            return float(a)/float(b)
                    except Exception:
                        return None
                    return None
                s["sent_pos_neg_ratio"] = _safe_ratio(pos_long, neg_long)
                s["sent_pos_neg_ratio_w2"] = _safe_ratio(pos_short, neg_short)
                # Short vs long pos ratio delta
                if s.get("sent_pos_neg_ratio") is not None and s.get("sent_pos_neg_ratio_w2") is not None:
                    try:
                        s["sent_pos_neg_ratio_diff_w2"] = s["sent_pos_neg_ratio_w2"] - s["sent_pos_neg_ratio"]
                    except Exception:
                        pass
    # Recent tail only (avoid returning huge list if limit very large)
    base_resp = {
        "symbol": sym,
        "interval": itv,
        "limit_used": limit,
        "horizon": horizon,
    "sentiment_window": sentiment_window,
    "sentiment_window_secondary": sentiment_window_secondary,
        "sample_count": len(samples),
        "samples": samples[-200:],  # cap output size
    }
    base_resp.update(meta)
    return base_resp
# Simple holder for calibration streak state
class _CalibrationStreakState:
    abs_streak: int = 0
    rel_streak: int = 0
    last_snapshot: Optional[dict] = None
    last_recommend: bool = False

_streak_state = _CalibrationStreakState()

async def _fetch_production_ece() -> Optional[float]:
    """Fetch current production (or latest) model ECE metric.

    Returns None if unavailable or on error.
    """
    try:
        repo_reg = ModelRegistryRepository()
        rows_reg = await repo_reg.fetch_latest(cfg.auto_promote_model_name, "supervised", limit=5)
        prod = None
        for r in rows_reg:
            if r.get("status") == "production":
                prod = r
                break
        if not prod and rows_reg:
            prod = rows_reg[0]
        if prod:
            metrics = prod.get("metrics") or {}
            e = metrics.get("ece")
            if isinstance(e, (int, float)):
                return float(e)
    except Exception:
        return None
    return None

@app.middleware("http")
async def request_logger(request: Request, call_next):
    """Log every request with latency; capture unhandled exceptions.

    Enhancements:
      - Generate per-request correlation id (header X-Request-ID if supplied, else uuid4)
      - Log structured line on success & error
      - For unhandled exceptions, return JSON {error, request_id} (preserve HTTPException semantics)
    """
    import uuid, traceback
    start = time.perf_counter()
    req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    # Attach to state so handlers could use if needed
    request.state.request_id = req_id  # type: ignore
    try:
        response = await call_next(request)
    except HTTPException:
        # Let FastAPI render this, but log first
        duration_ms = (time.perf_counter() - start) * 1000
        logger.warning(
            "request_error method=%s path=%s status=%s duration_ms=%.2f request_id=%s",  # noqa: E501
            request.method,
            request.url.path,
            getattr(request, 'status_code', 'n/a'),
            duration_ms,
            req_id,
        )
        raise
    except Exception as e:  # noqa: BLE001
        duration_ms = (time.perf_counter() - start) * 1000
        tb = traceback.format_exc(limit=20)
        logger.error(
            "unhandled_exception method=%s path=%s duration_ms=%.2f request_id=%s exc=%s\n%s",  # noqa: E501
            request.method,
            request.url.path,
            duration_ms,
            req_id,
            repr(e),
            tb,
        )
        # Return JSON error (avoid leaking internal details in prod)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "message": "Unhandled server exception",
                "request_id": req_id,
            },
        )
    duration_ms = (time.perf_counter() - start) * 1000
    try:
        response.headers["X-Request-ID"] = req_id
    except Exception:
        pass
    logger.info(
        "request method=%s path=%s status=%s duration_ms=%.2f request_id=%s",  # noqa: E501
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        req_id,
    )
    return response

#ㅕㅕㅕㅕ
API_KEY_HEADER = "X-API-Key"
API_KEY = os.getenv("API_KEY", "dev-key")

async def require_api_key(request: Request, x_api_key: Optional[str] = Header(default=None)):
    """API Key 인증.

    개선:
    - 환경변수 ALLOW_DOCS_NOAUTH=1 인 경우 Swagger UI(/docs)나 ReDoc(/redoc) 에서 오는 요청(Referer 기반)은 키 없이 허용.
    - 로컬 개발 편의성을 위해서만 사용 권장. 운영에서는 반드시 ALLOW_DOCS_NOAUTH 비활성화.
    """
    # 1) 완전 비활성화 플래그: DISABLE_API_KEY=1 → 모든 엔드포인트 무조건 통과 (개발/로컬 용)
    if os.getenv("DISABLE_API_KEY", "0").lower() in {"1","true","yes","on"}:
        return True
    # 1-2) 로컬 개발 환경(APP_ENV=local/dev/development)에서는 모든 인증 우회
    app_env = os.getenv("APP_ENV", "").lower()
    if app_env in {"local", "dev", "development"}:
        return True
    current_key = getattr(request.app.state, "current_api_key", API_KEY)  # type: ignore
    # 2) 문서/스키마/자산 경로는 항상 허용 (Swagger UI, ReDoc, OpenAPI JSON)
    path = request.url.path
    if path.startswith("/docs") or path.startswith("/redoc") or path == "/openapi.json":
        return True
    # 3) Referer 기반 (Swagger UI 내부 fetch) 도 항상 허용 (일부 브라우저는 /docs 자바스크립트 호출에 referer만 설정)
    ref = request.headers.get("referer", "")
    if "/docs" in ref or "/redoc" in ref:
        return True
    # 4) (이전) ALLOW_DOCS_NOAUTH 는 더 이상 필요 없지만 하위 호환 위해 남김
    if os.getenv("ALLOW_DOCS_NOAUTH", "0") in {"1", "true", "TRUE", "on", "yes"}:
        # 이미 위 조건으로 커버되지만 혹시 경로/레퍼러 둘 다 없는 케이스 fallback
        return True
    # 5) (선택) 관리자 경로 무인증 허용: ALLOW_ADMIN_NOAUTH=1 이면 /admin/* 경로도 허용 (개발 전용)
    try:
        allow_admin = os.getenv("ALLOW_ADMIN_NOAUTH", "0").lower() in {"1","true","yes","on"}
    except Exception:
        allow_admin = False
    if allow_admin and request.url.path.startswith("/admin/"):
        return True
    # 6) 상태/요약 성격의 공개 읽기 엔드포인트 허용 (개발 기본 허용, 운영에서는 ALLOW_PUBLIC_STATUS=0 권장)
    try:
        allow_public = os.getenv("ALLOW_PUBLIC_STATUS", "1").lower() in {"1", "true", "yes", "on"}
    except Exception:
        allow_public = False
    if allow_public:
        # 개발 편의: ALLOW_PUBLIC_STATUS=1 이면 모든 /api/* 엔드포인트는 공개 허용 (관리자 /admin/* 는 제외)
        if path.startswith("/api/"):
            return True
        # (하위 호환) 정확 경로 & 접두사 기반 허용 목록 (읽기 전용 상태/요약)
        public_exact = {
            "/api/system/status",
            "/api/ingestion/status",
            "/api/version",
            "/api/ohlcv/backfill/year",
            "/api/ohlcv/backfill/year/status",
            "/api/risk/state",
        }
        public_prefix = (
            "/api/inference/activity",  # /recent, /summary 등
            "/api/features",            # features endpoints (e.g., /api/features/drift/scan)
        )
        if path in public_exact or any(path.startswith(pref) for pref in public_prefix):
            return True

    # 7) (옵션) 쿼리스트링 기반 API Key 허용 (SSE 등 헤더 설정이 어려운 클라이언트 편의)
    #    ALLOW_API_KEY_QUERY=1 일 때만 허용. 키 이름: x_api_key 또는 apikey
    try:
        allow_q = os.getenv("ALLOW_API_KEY_QUERY", "0").lower() in {"1","true","yes","on"}
    except Exception:
        allow_q = False
    if allow_q and x_api_key is None:
        try:
            qp = request.query_params
            qkey = qp.get("x_api_key") or qp.get("apikey")
            if qkey and qkey == current_key:
                return True
        except Exception:
            pass

    # 8) 일반 보호 로직
    if x_api_key != current_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    return True

# --- Runtime control: Auto Inference Loop (admin endpoints) ---
from pydantic import BaseModel as _AutoInferModel

class AutoInferEnableRequest(_AutoInferModel):
    interval: Optional[float] = None
    run_first: Optional[bool] = False
    threshold: Optional[float] = None  # optional custom threshold for immediate run

def _restart_auto_inference_task(app: FastAPI) -> bool:  # type: ignore[name-defined]
    t = getattr(app.state, 'auto_inference_task', None)
    if t and not getattr(t, 'done', lambda: True)():
        with contextlib.suppress(Exception):
            t.cancel()
    cfg_local = load_config()
    runtime_enabled = getattr(app.state, 'auto_inference_enabled_override', None)
    effective_enabled = cfg_local.inference_auto_loop_enabled if runtime_enabled is None else bool(runtime_enabled)
    if not effective_enabled:
        return False
    interval_override = getattr(app.state, 'auto_inference_interval_override', None)
    async def _launcher():
        return await auto_inference_loop(interval_override=interval_override)
    try:
        task = asyncio.create_task(_launcher())
        app.state.auto_inference_task = task
        return True
    except Exception:
        return False

@app.post("/admin/inference/auto/enable", dependencies=[Depends(require_api_key)])
async def admin_auto_infer_enable(req: AutoInferEnableRequest):
    app.state.auto_inference_enabled_override = True  # type: ignore
    if req.interval is not None:
        if req.interval <= 0:
            raise HTTPException(status_code=400, detail="interval must be > 0")
        app.state.auto_inference_interval_override = float(req.interval)  # type: ignore
    # Set threshold override for the loop if provided
    if req.threshold is not None:
        if not (0 < float(req.threshold) < 1):
            raise HTTPException(status_code=400, detail="threshold must be between 0 and 1")
        app.state.auto_inference_threshold_override = float(req.threshold)  # type: ignore
    restarted = _restart_auto_inference_task(app)
    immediate: Optional[dict] = None
    if req.run_first:
        # Perform a single prediction immediately (manual log entry) using provided or default threshold
        thr = req.threshold
        if thr is not None and (thr <= 0 or thr >= 1):
            raise HTTPException(status_code=400, detail="threshold must be between 0 and 1")
        if thr is None:
            # Fallback resolution: runtime override > config default
            try:
                _override = getattr(app.state, 'auto_inference_threshold_override', None)
                if isinstance(_override, (int, float)) and 0 < float(_override) < 1:
                    thr = float(_override)
                else:
                    thr = float(load_config().inference_prob_threshold)
            except Exception:
                thr = 0.5
        svc = _training_service()
        try:
            immediate = await svc.predict_latest(threshold=float(thr))
            # Flag that this came from run_first
            if isinstance(immediate, dict):
                immediate["run_first"] = True
        except Exception as e:  # noqa: BLE001
            immediate = {"status": "error", "error": str(e), "run_first": True}
    return {
        "status": "ok",
        "enabled": True,
        "interval_override": getattr(app.state, 'auto_inference_interval_override', None),
        "threshold_override": getattr(app.state, 'auto_inference_threshold_override', None),
        "restarted": restarted,
        "run_first_executed": bool(req.run_first),
        "immediate_result": immediate,
    }

@app.post("/admin/inference/auto/disable", dependencies=[Depends(require_api_key)])
async def admin_auto_infer_disable():
    app.state.auto_inference_enabled_override = False  # type: ignore
    t = getattr(app.state, 'auto_inference_task', None)
    if t and not getattr(t, 'done', lambda: True)():
        with contextlib.suppress(Exception):
            t.cancel()
    return {"status": "ok", "enabled": False}

@app.post("/admin/inference/auto/clear", dependencies=[Depends(require_api_key)])
async def admin_auto_infer_clear():
    if hasattr(app.state, 'auto_inference_enabled_override'):
        delattr(app.state, 'auto_inference_enabled_override')
    if hasattr(app.state, 'auto_inference_interval_override'):
        delattr(app.state, 'auto_inference_interval_override')
    restarted = _restart_auto_inference_task(app)
    return {"status": "ok", "override_cleared": True, "restarted": restarted}

@app.get("/admin/inference/auto/status", dependencies=[Depends(require_api_key)])
async def admin_auto_infer_status():
    cfg_local = load_config()
    runtime_enabled = getattr(app.state, 'auto_inference_enabled_override', None)
    interval_override = getattr(app.state, 'auto_inference_interval_override', None)
    effective_enabled = cfg_local.inference_auto_loop_enabled if runtime_enabled is None else bool(runtime_enabled)
    effective_interval = None
    if effective_enabled:
        effective_interval = interval_override if (isinstance(interval_override, (int,float)) and interval_override and interval_override > 0) else cfg_local.inference_auto_loop_interval
    task = getattr(app.state, 'auto_inference_task', None)
    running = bool(task and not getattr(task, 'done', lambda: True)())
    return {
        "status": "ok",
        "config_enabled": cfg_local.inference_auto_loop_enabled,
        "runtime_override": runtime_enabled,
        "interval_config": cfg_local.inference_auto_loop_interval,
        "interval_override": interval_override,
        "effective_enabled": effective_enabled,
        "effective_interval": effective_interval,
        "task_running": running,
        "threshold_config": cfg_local.inference_prob_threshold,
        "threshold_override": getattr(app.state, 'auto_inference_threshold_override', None),
    }

@app.post("/admin/inference/auto/threshold", dependencies=[Depends(require_api_key)])
async def admin_auto_infer_set_threshold(threshold: Optional[float] = Body(default=None)):
    # When threshold is null/None, clear the runtime override
    if threshold is None:
        try:
            if hasattr(app.state, 'auto_inference_threshold_override'):
                delattr(app.state, 'auto_inference_threshold_override')
        except Exception:
            pass
        restarted = _restart_auto_inference_task(app)
        return {"status": "ok", "threshold_override": None, "restarted": restarted}
    # Otherwise set a valid override
    if not (0 < float(threshold) < 1):
        raise HTTPException(status_code=400, detail="threshold must be between 0 and 1")
    app.state.auto_inference_threshold_override = float(threshold)  # type: ignore
    restarted = _restart_auto_inference_task(app)
    return {"status": "ok", "threshold_override": float(threshold), "restarted": restarted}

# --- Admin: Inspect thresholds (env/config/effective) ---
@app.get("/admin/inference/thresholds", dependencies=[Depends(require_api_key)])
async def admin_infer_thresholds():
    cfg_local = load_config()
    import os as _os
    env_ml = _os.getenv("ML_SIGNAL_THRESHOLD")
    env_inf = _os.getenv("INFERENCE_PROB_THRESHOLD")
    # Effective inference threshold resolution: runtime override > config default
    thr_override = getattr(app.state, 'auto_inference_threshold_override', None)
    thr_cfg = float(getattr(cfg_local, 'inference_prob_threshold', 0.5))
    inf_eff = (
        float(thr_override) if (isinstance(thr_override, (int, float)) and 0 < float(thr_override) < 1) else thr_cfg
    )
    # ML signal effective threshold (kept for diagnostics; not used by the auto inference loop)
    try:
        from backend.apps.trading.service.ml_signal_service import MLSignalService as _Svc
        ml_eff = _Svc(risk_engine, autopilot=_autopilot_service)._effective_threshold()
    except Exception as e:  # noqa: BLE001
        ml_eff = None
    return {
        "status": "ok",
        "env": {
            "ML_SIGNAL_THRESHOLD": env_ml,
            "INFERENCE_PROB_THRESHOLD": env_inf,
        },
        "config": {
            "ml_signal_threshold": getattr(cfg_local, 'ml_signal_threshold', None),
            "inference_prob_threshold": getattr(cfg_local, 'inference_prob_threshold', None),
        },
        # Backward-compat: expose 'effective_threshold' as the auto-inference effective
        "effective_threshold": inf_eff,
        "inference_effective_threshold": inf_eff,
        "ml_effective_threshold": ml_eff,
        "override": getattr(app.state, 'auto_inference_threshold_override', None),
    }

# --- Admin: Effective inference settings snapshot (threshold + label params) ---
@app.get("/admin/inference/settings", dependencies=[Depends(require_api_key)])
async def admin_inference_settings():
    """Return effective inference-related settings currently applied in runtime.

    Includes:
      - threshold: config default, runtime override, and effective default used when client omits threshold
      - label_params: lookahead/drawdown/rebound from DB-backed labeler runtime overrides (if applied) with config fallbacks
    """
    cfg_local = load_config()
    # Threshold resolution: runtime override > config default
    thr_override = getattr(app.state, 'auto_inference_threshold_override', None)
    thr_cfg = float(getattr(cfg_local, 'inference_prob_threshold', 0.5))
    thr_effective = (
        float(thr_override) if (isinstance(thr_override, (int, float)) and 0 < float(thr_override) < 1) else thr_cfg
    )
    # Labeler params from runtime overrides when available
    try:
        from backend.apps.training.service.auto_labeler import get_auto_labeler_service as _get_als
        _svc_lbl = _get_als()
        L = getattr(_svc_lbl, "_L_override", None)
        DD = getattr(_svc_lbl, "_DD_override", None)
        RB = getattr(_svc_lbl, "_RB_override", None)
    except Exception:
        L = None; DD = None; RB = None
    lookahead_eff = int(L) if isinstance(L, (int, float)) else int(getattr(cfg_local, 'bottom_lookahead', 30) or 30)
    drawdown_eff = float(DD) if isinstance(DD, (int, float)) else float(getattr(cfg_local, 'bottom_drawdown', 0.005) or 0.005)
    rebound_eff = float(RB) if isinstance(RB, (int, float)) else float(getattr(cfg_local, 'bottom_rebound', 0.003) or 0.003)
    label_source = {
        "lookahead": "db" if isinstance(L, (int, float)) else "config",
        "drawdown": "db" if isinstance(DD, (int, float)) else "config",
        "rebound": "db" if isinstance(RB, (int, float)) else "config",
    }
    return {
        "status": "ok",
        "threshold": {
            "config": thr_cfg,
            "override": (float(thr_override) if isinstance(thr_override, (int, float)) else None),
            "effective_default": thr_effective,
        },
        "label_params": {
            "lookahead": lookahead_eff,
            "drawdown": drawdown_eff,
            "rebound": rebound_eff,
            "source": label_source,
        },
    }

# --- Admin: Reload env files (.env/.env.private) ---
@app.post("/admin/env/reload", dependencies=[Depends(require_api_key)])
async def admin_env_reload():
    try:
        reload_env_from_files()
        cfg = load_config()
        return {
            "status": "ok",
            "ml_signal_threshold_config": getattr(cfg, 'ml_signal_threshold', None),
            "inference_prob_threshold_config": getattr(cfg, 'inference_prob_threshold', None),
            "symbol": getattr(cfg, 'symbol', None),
        }
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))

# --- Admin: Inference Log Queue diagnostics ---
@app.get("/admin/inference/queue/status", dependencies=[Depends(require_api_key)])
async def admin_inference_queue_status():
    q = get_inference_log_queue()
    return q.status()

@app.post("/admin/inference/queue/flush", dependencies=[Depends(require_api_key)])
async def admin_inference_queue_flush():
    q = get_inference_log_queue()
    n = await q.flush_now()
    return {"flushed": n, "status": q.status()}

@app.get("/api/inference/predict")
async def inference_predict(
    threshold: Optional[float] = None,
    debug: bool = False,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    target: Optional[str] = None,
    use: Optional[str] = None,
    version: Optional[str] = None,
    _auth: bool = Depends(require_api_key),
):
    """최신 데이터에 대해 단발 추론 수행.

    threshold: (0,1) 사이. 확률 >= threshold 면 decision=1.
    """
    # Resolve threshold: prefer explicit query; else DB runtime override; else config default
    thr_explicit = None
    if isinstance(threshold, (int, float)):
        thr_explicit = float(threshold)
    thr_override = getattr(app.state, 'auto_inference_threshold_override', None)
    thr_default = float(getattr(cfg, 'inference_prob_threshold', 0.5))
    thr_use = (
        float(thr_explicit) if (isinstance(thr_explicit, (int, float)) and 0 < float(thr_explicit) < 1)
        else (float(thr_override) if (isinstance(thr_override, (int, float)) and 0 < float(thr_override) < 1) else thr_default)
    )
    if not (0 < float(thr_use) < 1):
        raise HTTPException(status_code=400, detail="threshold must be between 0 and 1")
    # Allow optional scoping override for symbol/interval (default to cfg)
    use_symbol = (symbol or cfg.symbol)
    use_interval = (interval or cfg.kline_interval)
    # Bottom-only mode: force target to 'bottom'
    tgt = 'bottom'
    svc = TrainingService(use_symbol, use_interval, cfg.model_artifact_dir)
    try:
        prefer_latest = bool(use and str(use).strip().lower() in ("latest","newest"))
        if tgt == 'bottom' and hasattr(svc, 'predict_latest_bottom'):
            res = await svc.predict_latest_bottom(threshold=thr_use, debug=bool(debug), prefer_latest=prefer_latest, version=version)  # type: ignore[attr-defined]
        else:
            res = await svc.predict_latest(threshold=thr_use, debug=bool(debug), prefer_latest=prefer_latest, version=version)
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    # If features look stale, add a gentle hint for UI troubleshooting
    try:
        if isinstance(res, dict) and res.get("status") == "ok" and res.get("feature_stale") is True and "hint" not in res:
            res["hint"] = "입력 피처가 오래되었습니다. 캔들/피처 스케줄러가 동작 중인지 확인하세요 (feature_age_seconds 확인)."
    except Exception:
        pass
    # Mark that the threshold was explicitly supplied (and valid) so UI/history can reflect it
    try:
        if isinstance(res, dict) and res.get("status") == "ok" and isinstance(threshold, (int, float)) and 0 < float(threshold) < 1:
            # Mark only when user explicitly supplied threshold in query
            res.setdefault("overridden_threshold", True)
    except Exception:
        pass
    # Direction Playground UI block removed in bottom-only mode
    # Add remediation hints for common data availability cases
    try:
        st = res.get("status") if isinstance(res, dict) else None
        if st == "no_data" and "hint" not in res:
            res["hint"] = (
                "피처 데이터가 없습니다. 다음을 확인하세요: 1) 최근 OHLCV가 로드되었는지, 2) feature_scheduler가 실행 중인지, "
                "3) 필요시 /admin/features/compute-now 호출로 즉시 생성. (Admin>Artifacts/Jobs 탭 참고)"
            )
            res.setdefault("next_steps", [
                "GET /api/ohlcv/recent?limit=1",
                "GET /admin/features/status",
                "POST /admin/features/compute-now"
            ])
        elif st == "insufficient_features" and "hint" not in res:
            missing = res.get("missing")
            try:
                miss_txt = ",".join(missing) if isinstance(missing, list) else ""
            except Exception:
                miss_txt = ""
            res["hint"] = (
                f"일부 피처가 비어 있습니다({miss_txt}). 최신 스냅샷이 충분한 히스토리를 갖도록 피처 스케줄러가 주기적으로 실행되는지 확인하세요. "
                "필요시 /admin/features/compute-now 호출로 최신 스냅샷을 강제 생성할 수 있습니다."
            )
            res.setdefault("next_steps", [
                "GET /admin/features/status",
                "POST /admin/features/compute-now",
                "POST /admin/features/backfill?target=600"
            ])
    except Exception:
        pass
    try:
        if res.get("status") == "ok" and isinstance(res.get("probability"), (int,float)):
            # Echo target and label params for clients and logs
            try:
                res.setdefault("target", tgt)
                if tgt == 'bottom':
                    # Prefer DB-applied overrides (Admin>DB Settings) via labeler service runtime; fallback to config
                    try:
                        from backend.apps.training.service.auto_labeler import get_auto_labeler_service as _get_als
                        _svc_lbl = _get_als()
                        L = getattr(_svc_lbl, "_L_override", None)
                        DD = getattr(_svc_lbl, "_DD_override", None)
                        RB = getattr(_svc_lbl, "_RB_override", None)
                    except Exception:
                        L = None; DD = None; RB = None
                    res.setdefault("label_params", {
                        "lookahead": int(L if isinstance(L, (int, float)) else (getattr(cfg, 'bottom_lookahead', 30) or 30)),
                        "drawdown": float(DD if isinstance(DD, (int, float)) else (getattr(cfg, 'bottom_drawdown', 0.005) or 0.005)),
                        "rebound": float(RB if isinstance(RB, (int, float)) else (getattr(cfg, 'bottom_rebound', 0.003) or 0.003)),
                    })
            except Exception:
                pass
            log_queue = get_inference_log_queue()
            # Use model family name consistent with explicit target for logging
            _model_name_log = 'bottom_predictor'
            enq_ok = await log_queue.enqueue({
                "symbol": use_symbol,
                "interval": use_interval,
                "model_name": _model_name_log,
                "model_version": str(res.get("model_version")),
                "probability": float(res.get("probability")),
                "decision": int(res.get("decision")),
                "threshold": float(res.get("threshold")),
                "production": bool(res.get("used_production")),
                "extra": {"manual": True, "target": tgt},
            })
            if not enq_ok:
                repo = InferenceLogRepository()
                with contextlib.suppress(Exception):
                    await repo.bulk_insert([
                        {
                            "symbol": use_symbol,
                            "interval": use_interval,
                            "model_name": _model_name_log,
                            "model_version": str(res.get("model_version")),
                            "probability": float(res.get("probability")),
                            "decision": int(res.get("decision")),
                            "threshold": float(res.get("threshold")),
                            "production": bool(res.get("used_production")),
                            "extra": {"manual": True, "fallback": True, "target": tgt},
                        }
                    ])
        # Metrics: count artifact issues
        st_metric = res.get("status")
        try:
            INFERENCE_REQUESTS.labels(status=st_metric or "unknown").inc()
            if st_metric == "artifact_not_found":
                INFERENCE_ARTIFACT_NOT_FOUND.inc()
        except Exception:
            pass
    except Exception:
        pass
    # Attach remediation hints for common artifact issues to aid UI troubleshooting
    try:
        st = res.get("status")
        if st in {"artifact_not_found", "artifact_missing"}:
            # Only add hint if not already present
            if "hint" not in res:
                res["hint"] = (
                    "Model registry row refers to a missing artifact file. "
                    "Run /admin/models/artifacts/verify to confirm. If missing, trigger a new training run to recreate the artifact, "
                    "or check that the artifacts directory (config model_artifact_dir) is correctly mounted and not cleaned by external processes."
                )
                res["next_steps"] = [
                    "GET /admin/models/artifacts/verify",
                    "POST /api/training/run (or your training trigger) to regenerate",
                    "Inspect artifact directory volume/mount permissions",
                    "Confirm artifact_cleanup settings didn't delete the file unexpectedly"
                ]
        elif st == "artifact_corrupt" and "hint" not in res:
            res["hint"] = (
                "Artifact file loaded but missing required serialization (sk_model_b64). Retrain the model to generate a fresh artifact."
            )
        elif st == "artifact_load_error" and "error" in res and "hint" not in res:
            res["hint"] = "Error unpickling artifact. Ensure Python/sklearn versions match those used during training, or retrain to refresh artifact."
    except Exception:
        pass
    return res

# --- Public: Effective inference threshold (read-only, non-admin) ---
@app.get("/api/inference/effective_threshold")
async def api_inference_effective_threshold(_auth: bool = Depends(require_api_key)):
    """Return the effective inference probability threshold used by the server.

    Resolution order: runtime override from DB (/admin/settings inference.auto.threshold) > config default.
    Provided as a non-admin endpoint so UIs can display the applied value without admin privileges.
    """
    cfg_local = load_config()
    thr_override = getattr(app.state, 'auto_inference_threshold_override', None)
    thr_cfg = float(getattr(cfg_local, 'inference_prob_threshold', 0.5))
    thr_effective = (
        float(thr_override) if (isinstance(thr_override, (int, float)) and 0 < float(thr_override) < 1) else thr_cfg
    )
    return {"status": "ok", "threshold": thr_effective}

# ---------------------------------------------------------------------------
# Admin: Feature scheduler status and on-demand compute
# ---------------------------------------------------------------------------
@app.get("/admin/features/status", dependencies=[Depends(require_api_key)])
async def admin_features_status(symbol: Optional[str] = None, interval: Optional[str] = None):
    sym = symbol or cfg.symbol
    itv = interval or cfg.kline_interval
    svc: FeatureService = getattr(app.state, 'feature_service', FeatureService(sym, itv))
    # If different scope requested, create a temp service for status read
    if svc.symbol != sym or svc.interval != itv:
        svc = FeatureService(sym, itv)
    last_ok = getattr(svc, 'last_success_ts', None)
    last_err = getattr(svc, 'last_error_ts', None)
    from time import time as _now
    lag = None
    try:
        if isinstance(last_ok, (int,float)):
            lag = max(0.0, _now() - float(last_ok))
    except Exception:
        lag = None
    # peek latest snapshot meta if available
    latest_snapshot = None
    try:
        rows = await svc.fetch_recent(limit=1)
        if rows:
            r = rows[0]
            latest_snapshot = {
                "open_time": r.get("open_time"),
                "close_time": r.get("close_time"),
            }
    except Exception:
        pass
    return {
        "status": "ok",
        "symbol": sym,
        "interval": itv,
        "last_success_ts": last_ok,
        "last_error_ts": last_err,
        "lag_seconds": lag,
        "latest_snapshot": latest_snapshot,
        "fast_startup": bool(getattr(app.state, 'fast_startup', False)),
        "skipped": "feature_scheduler" in getattr(app.state, 'skipped_components', []),
        "degraded": "feature_scheduler_db" in getattr(app.state, 'degraded_components', []),
    }

@app.post("/admin/features/compute-now", dependencies=[Depends(require_api_key)])
async def admin_features_compute_now(symbol: Optional[str] = None, interval: Optional[str] = None):
    sym = symbol or cfg.symbol
    itv = interval or cfg.kline_interval
    svc: FeatureService = getattr(app.state, 'feature_service', FeatureService(sym, itv))
    if svc.symbol != sym or svc.interval != itv:
        svc = FeatureService(sym, itv)
    try:
        res = await svc.compute_and_store()
        return {"status": "ok", "result": res}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"compute_failed:{e}")

@app.get("/api/inference/activity/recent")
async def inference_activity_recent(limit: int = 50, auto_only: bool = False, manual_only: bool = False, _auth: bool = Depends(require_api_key)):
    """최근 inference 로그 일부를 반환.

    Params:
      limit: 1-500 사이 (기본 50)
      auto_only: True 면 auto loop 기록만
      manual_only: True 면 manual (UI/endpoint) 기록만
        (둘 다 True 면 400 에러)

    Response:
      { status, count, items: [ { created_at, probability, decision, threshold, production, model_name, model_version, auto } ] }
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit_out_of_range")
    if auto_only and manual_only:
        raise HTTPException(status_code=400, detail="conflicting_filters")
    repo = InferenceLogRepository()
    rows = await repo.fetch_recent(limit=limit)
    items: list[dict] = []
    for r in rows:
        extra = r.get("extra") or {}
        is_manual = bool(extra.get("manual"))
        if auto_only and is_manual:
            continue
        if manual_only and not is_manual:
            continue
        created_at = r.get("created_at")
        try:
            ts = created_at.timestamp() if hasattr(created_at, "timestamp") else float(created_at)
        except Exception:
            ts = None
        items.append({
            "created_at": ts,
            "probability": r.get("probability"),
            "decision": r.get("decision"),
            "threshold": r.get("threshold"),
            "production": r.get("production"),
            "model_name": r.get("model_name"),
            "model_version": r.get("model_version"),
            "auto": (not is_manual),
        })
    return {"status": "ok", "count": len(items), "items": items}

# ---------------------------------------------------------------------------
# Admin: Feature one-shot backfill (bootstrap helper)
# ---------------------------------------------------------------------------
@app.post("/admin/features/backfill", dependencies=[Depends(require_api_key)])
async def admin_features_backfill(target: int = 600, symbol: Optional[str] = None, interval: Optional[str] = None, window: Optional[int] = None, recent: Optional[bool] = False):
    """Compute and insert feature_snapshot rows in bulk for bootstrap.

    Params:
      target: number of snapshots to generate (approx)
      symbol/interval override config defaults
    """
    sym = symbol or cfg.symbol
    itv = interval or cfg.kline_interval
    w = None
    try:
        w = int(window) if window is not None else None
    except Exception:
        w = None
    if w is not None and w < 1:
        w = 1
    svc = FeatureService(sym, itv, window=w if w is not None else 300)
    try:
        res = await svc.backfill_snapshots(target=target, from_tail=bool(recent))
        return {"status": "ok", **res, "symbol": sym, "interval": itv}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"feature_backfill_failed:{e}")

# ---------------------------------------------------------------------------
# Admin: Full bootstrap orchestrator
# ---------------------------------------------------------------------------
class BootstrapRequest(BaseModel):
    symbol: Optional[str] = None
    interval: Optional[str] = None
    backfill_year: bool = True
    fill_gaps: bool = True
    feature_target: int = 600
    train_sentiment: bool = False
    # New: guardrails and controls
    min_auc: Optional[float] = None
    max_ece: Optional[float] = None
    dry_run: bool = False
    # Step toggles
    skip_features: bool = False
    skip_training: bool = False
    skip_promotion: bool = False
    retry_fill_gaps: bool = False
    # Bottom training params (optional, will fall back to cfg defaults)
    bottom_lookahead: Optional[int] = None
    bottom_drawdown: Optional[float] = None
    bottom_rebound: Optional[float] = None

@app.post("/admin/bootstrap", dependencies=[Depends(require_api_key)])
async def admin_bootstrap(req: BootstrapRequest):
    """Run end-to-end bootstrap: continuity -> features -> training -> promotion.

    Steps:
      1) Optional year backfill and gap fill
      2) Feature backfill to reach feature_target
      3) Manual training run (baseline), optional sentiment training
      4) Attempt auto-promotion if better
    """
    sym = req.symbol or cfg.symbol
    itv = req.interval or cfg.kline_interval
    # Apply default thresholds if not provided
    min_auc_default = 0.6
    max_ece_default = 0.08
    min_auc_used = req.min_auc if req.min_auc is not None else min_auc_default
    max_ece_used = req.max_ece if req.max_ece is not None else max_ece_default

    meta: dict[str, Any] = {
        "symbol": sym,
        "interval": itv,
        "thresholds_applied": {"min_auc": min_auc_used, "max_ece": max_ece_used},
    }

    # 1) Data continuity (best-effort)
    continuity: dict[str, Any] = {}
    if req.backfill_year:
        try:
            jb = await start_year_backfill(sym, itv)
            # Normalize time-unit labeling for clarity (_sec/_ms)
            if isinstance(jb, dict):
                if "started_at" in jb and isinstance(jb["started_at"], (int, float)):
                    jb.setdefault("started_at_sec", jb["started_at"])  # duplicate with explicit unit
                if "last_progress_ts" in jb and isinstance(jb["last_progress_ts"], (int, float)):
                    jb.setdefault("last_progress_ts_sec", jb["last_progress_ts"])  # duplicate with explicit unit
                if "from_open_time" in jb and isinstance(jb["from_open_time"], int):
                    jb.setdefault("from_open_time_ms", jb["from_open_time"])  # clarify milliseconds
                if "to_open_time" in jb and isinstance(jb["to_open_time"], int):
                    jb.setdefault("to_open_time_ms", jb["to_open_time"])  # clarify milliseconds
            continuity["year_backfill"] = jb
        except Exception as e:  # noqa: BLE001
            continuity["year_backfill_error"] = str(e)
    if req.fill_gaps:
        try:
            # Prefer invoking the existing HTTP endpoint for consistency
            # Note: for internal call we can import repository, but we keep it simple
            from backend.apps.ingestion.backfill.gap_backfill_service import GapBackfillService as _GBS  # type: ignore
            consumer = getattr(app.state, "kline_consumer", None)
            if consumer is not None:
                svc_g = _GBS(consumer, itv)
                # Run a single scan/fill cycle method if available; fallback no-op
                if hasattr(svc_g, "_cycle"):
                    await svc_g._cycle()  # type: ignore
                continuity["gap_fill_invoked"] = True
                # Optional retry cycles for stubborn gaps
                if getattr(req, "retry_fill_gaps", False):
                    attempts = 0
                    errors: list[str] = []
                    while attempts < 2:  # up to 2 extra cycles
                        attempts += 1
                        try:
                            await svc_g._cycle()  # type: ignore[attr-defined]
                        except Exception as re:  # noqa: BLE001
                            errors.append(str(re))
                            break
                    continuity["gap_fill_retries"] = {"attempts": attempts, "errors": errors}
            else:
                continuity["gap_fill_skipped"] = "consumer_unavailable"
        except Exception as e:  # noqa: BLE001
            continuity["gap_fill_error"] = str(e)

    # 2) Features backfill (unless skipped)
    feats: Optional[dict[str, Any]] = None
    if not req.skip_features:
        feats = await FeatureService(sym, itv).backfill_snapshots(target=max(100, req.feature_target))
    else:
        feats = {"status": "skipped"}

    # 3) Training (unless skipped)
    default_limit = getattr(cfg, 'auto_retrain_min_samples', 600) or 600
    svc_train = _training_service()
    train_result: Optional[dict[str, Any]] = None
    promo_result: Optional[dict[str, Any]] = None
    if not req.skip_training:
        try:
            if req.dry_run:
                # In dry-run, estimate training viability based on available features only.
                train_result = {"status": "dry_run", "limit": int(default_limit)}
            else:
                # Prefer bottom-specific training path when available
                if hasattr(svc_train, 'run_training_bottom'):
                    look = int(req.bottom_lookahead or getattr(cfg, 'bottom_lookahead', 30) or 30)
                    dd = float(req.bottom_drawdown or getattr(cfg, 'bottom_drawdown', 0.005) or 0.005)
                    rb = float(req.bottom_rebound or getattr(cfg, 'bottom_rebound', 0.003) or 0.003)
                    train_result = await svc_train.run_training_bottom(limit=int(default_limit), lookahead=look, drawdown=dd, rebound=rb)  # type: ignore[attr-defined]
                else:
                    # Fallback: generic training (baseline)
                    train_result = await svc_train.run_training(limit=int(default_limit))
        except Exception as e:  # noqa: BLE001
            train_result = {"status": "error", "error": str(e)}
    else:
        train_result = {"status": "skipped"}

    # 4) Promotion attempt (guarded by thresholds, unless skipped or dry-run)
    if not req.skip_promotion and not req.dry_run and train_result and train_result.get("status") == "ok":
        metrics = train_result.get("metrics") or {}
        auc = metrics.get("auc")
        ece = metrics.get("ece")
        thresholds_ok = True
        reason: list[str] = []
        # Use defaults when not specified by client
        if not isinstance(auc, (int, float)) or auc < min_auc_used:
            thresholds_ok = False
            reason.append(f"auc<{min_auc_used}")
        if not isinstance(ece, (int, float)) or ece > max_ece_used:
            thresholds_ok = False
            reason.append(f"ece>{max_ece_used}")
        if thresholds_ok:
            try:
                from backend.apps.training.auto_promotion import promote_if_better
                promo_result = await promote_if_better(train_result.get("model_id"), metrics)
            except Exception as pe:  # noqa: BLE001
                promo_result = {"error": str(pe)}
        else:
            promo_result = {"status": "skipped_by_threshold", "reason": ",".join(reason), "metrics": metrics}
    elif req.dry_run:
        promo_result = {"status": "dry_run"}

    # Optional sentiment training
    sentiment_result: Optional[dict[str, Any]] = None
    if not req.dry_run and req.train_sentiment and hasattr(svc_train, "run_training_ohlcv_sentiment"):
        try:
            sentiment_result = await svc_train.run_training_ohlcv_sentiment(limit=int(default_limit))  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            sentiment_result = {"status": "error", "error": str(e)}
    elif req.dry_run and req.train_sentiment:
        sentiment_result = {"status": "dry_run"}

    # Guard: if training_result nonsensically reports insufficient_labels while have >= required, fix status locally
    if isinstance(train_result, dict) and train_result.get("status") == "insufficient_labels":
        try:
            hv = int(train_result.get("have") or 0)
            rq = int(train_result.get("required") or 0)
            if hv >= rq and rq > 0:
                # Prefer class variation hint when available
                if float(train_result.get("pos_ratio") or 0.0) in (0.0, 1.0):
                    train_result["status"] = "insufficient_class_variation"
                else:
                    train_result["status"] = "insufficient_data"
        except Exception:
            pass

    # Compose response; keep service-provided fields intact, annotate partials in notes
    notes: list[str] = []
    if isinstance(train_result, dict) and train_result.get("status") not in ("ok","skipped","dry_run"):
        notes.append("training did not complete successfully; see training.status")

    return {
        "status": "ok",
        **meta,
        "continuity": continuity,
        "features": feats,
        "training": train_result,
        "promotion": promo_result,
        "sentiment": sentiment_result,
        **({"notes": notes} if notes else {}),
    }

from pydantic import BaseModel as _BaseModelKey  # local alias to avoid confusion
class RotateKeyRequest(_BaseModelKey):
    new_key: Optional[str] = None


# --- Model Comparison Endpoint (moved after require_api_key) ---
@app.get("/api/models/compare", dependencies=[Depends(require_api_key)])
async def api_models_compare(names: str = "bottom_predictor,ohlcv_sentiment_predictor", limit: int = 1):
    """Compare latest metrics for given comma-separated model names.

    Returns a dict keyed by model name with latest registry row metrics subset.
    """
    model_names = [n.strip() for n in names.split(",") if n.strip()]
    if not model_names:
        raise HTTPException(status_code=400, detail="no model names supplied")
    repo = ModelRegistryRepository()
    out: dict[str, Any] = {}
    for m in model_names:
        try:
            rows = await repo.fetch_latest(m, "supervised", limit=max(1, limit))
            if not rows:
                out[m] = {"status": "not_found"}
                continue
            latest = rows[0]
            metrics = latest.get("metrics") or {}
            subset = {k: metrics.get(k) for k in ["auc","accuracy","brier","ece","mce","samples","val_samples"]}
            out[m] = {
                "status": latest.get("status"),
                "version": latest.get("version"),
                "metrics": subset,
                "raw_metric_keys": list(metrics.keys()),
            }
        except Exception as e:
            out[m] = {"status": "error", "error": str(e)}
    return {"models": out, "requested": model_names}

@app.get("/api/models/coefficients", dependencies=[Depends(require_api_key)])
async def api_models_coefficients(name: str = "bottom_predictor", version: Optional[str] = None):
    """Expose LogisticRegression (또는 호환) 파이프라인의 피처별 계수/스케일 정보를 반환.

    - name: 모델 이름 (기본 bottom_predictor, 확장 ohlcv_sentiment_predictor 지원)
    - version: 특정 버전 지정 (없으면 production 우선, 없으면 최신)
    """
    repo = ModelRegistryRepository()
    # 1) 대상 레지스트리 행 선택
    target_row = None
    rows = await repo.fetch_latest(name, "supervised", limit=10)
    if not rows:
        raise HTTPException(status_code=404, detail="model_not_found")
    if version:
        for r in rows:
            if str(r.get("version")) == str(version):
                target_row = r
                break
        if not target_row:
            raise HTTPException(status_code=404, detail="version_not_found")
    else:
        # production 우선, 없으면 첫 번째
        prod = next((r for r in rows if r.get("status") == "production"), None)
        target_row = prod or rows[0]
    artifact_path = target_row.get("artifact_path")
    metrics = target_row.get("metrics") or {}
    # Seed baseline (artifact 없음) 처리
    if not artifact_path:
        if metrics.get("seed_baseline"):
            return {
                "status": "seed_baseline_no_artifact",
                "model_name": name,
                "version": target_row.get("version"),
                "message": "Seed baseline has no coefficients (no artifact).",
            }
        raise HTTPException(status_code=400, detail="artifact_missing")
    import json, base64, pickle, math
    from pathlib import Path
    p = Path(artifact_path)
    if not p.exists():
        raise HTTPException(status_code=400, detail="artifact_file_not_found")
    try:
        with open(p, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"artifact_load_error: {e}")
    b64 = payload.get("sk_model_b64")
    if not b64:
        raise HTTPException(status_code=400, detail="artifact_has_no_model_serialization")
    try:
        raw = base64.b64decode(b64)
        model_obj = pickle.loads(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"model_unpickle_error: {e}")
    # Support sklearn Pipeline(scaler, clf) or direct LogisticRegression
    pipe = model_obj
    scaler = None
    clf = None
    feature_names = metrics.get("feature_set") or metrics.get("features") or []
    try:
        from sklearn.pipeline import Pipeline as _Pipeline  # type: ignore
        if isinstance(pipe, _Pipeline):
            scaler = pipe.named_steps.get("scaler") if hasattr(pipe, "named_steps") else None
            clf = pipe.named_steps.get("clf") if hasattr(pipe, "named_steps") else None
        else:
            clf = pipe
    except Exception:
        clf = pipe
    # 추출 가능한지 확인
    if clf is None or not hasattr(clf, "coef_"):
        raise HTTPException(status_code=400, detail="classifier_has_no_coefficients")
    coef = getattr(clf, "coef_", None)
    intercept = getattr(clf, "intercept_", None)
    # LogisticRegression: coef_ shape (1, n_features)
    if coef is None:
        raise HTTPException(status_code=400, detail="no_coef_array")
    try:
        import numpy as np  # type: ignore
        coef_arr = np.array(coef).reshape(-1)
    except Exception:
        coef_arr = [float(c) for c in coef[0]] if isinstance(coef, (list, tuple)) else []  # type: ignore
    # feature 이름 길이와 coef 길이 불일치시 fallback generic names
    if feature_names and len(feature_names) != len(coef_arr):
        # 불일치시 generic 이름 생성 (안내 포함)
        feature_mismatch = True
        feature_names_out = [f"f{i}" for i in range(len(coef_arr))]
    else:
        feature_mismatch = False
        feature_names_out = feature_names if feature_names else [f"f{i}" for i in range(len(coef_arr))]
    items = []
    for fname, w in zip(feature_names_out, coef_arr):
        w_f = float(w)
        items.append({
            "feature": fname,
            "weight": w_f,
            "abs_weight": abs(w_f),
            "odds_ratio": math.exp(w_f) if not math.isnan(w_f) else None,
        })
    # abs_weight 기준 정렬 순위 부여
    items_sorted = sorted(items, key=lambda x: x["abs_weight"], reverse=True)
    rank_map = {it["feature"]: i+1 for i, it in enumerate(items_sorted)}
    for it in items:
        it["abs_rank"] = rank_map[it["feature"]]
    scaler_out = None
    if scaler is not None:
        scaler_out = {}
        for attr in ("mean_", "scale_", "var_"):
            if hasattr(scaler, attr):
                try:
                    val = getattr(scaler, attr)
                    scaler_out[attr[:-1]] = [float(x) for x in list(val)]
                except Exception:
                    pass
    response = {
        "status": "ok",
        "model_name": name,
        "version": target_row.get("version"),
        "model_status": target_row.get("status"),
        "feature_names": feature_names_out,
        "feature_count": len(feature_names_out),
        "coefficients": items,
        "intercept": float(intercept[0]) if isinstance(intercept, (list, tuple)) else (float(intercept) if intercept is not None else None),
        "scaler": scaler_out,
        "artifact_path": artifact_path,
        "feature_name_mismatch": feature_mismatch,
        "metrics_keys": list(metrics.keys()),
    }
    return response

@app.get("/api/models/calibration/summary", dependencies=[Depends(require_api_key)])
async def api_models_calibration_summary(name: str = "bottom_predictor", model_type: str = "supervised", recent: int = 5, include_sentiment: bool = True):
    """프로덕션 vs 최근 학습 모델들의 Calibration 지표 요약.

    Args:
        name: 기본 대상 모델 이름 (bottom_predictor)
        model_type: 레지스트리 모델 타입 (기본 supervised)
        recent: 최근 N개 (staging 포함) 조회
        include_sentiment: True 시 sentiment 모델도 함께 요약

    Returns:
        production: 현행 prod 모델 calibration 핵심 지표
        candidates: 최근 N개 후보 (prod 제외) 의 auc/brier/ece + cv mean/stdev (가능 시)
        sentiment(optional): sentiment 모델 동일 구조
    """
    repo = ModelRegistryRepository()
    def _extract(row: dict) -> dict:
        metrics = row.get("metrics") or {}
        cv = metrics.get("cv_report") or {}
        auc_cv = cv.get("auc") or {}
        ece = metrics.get("ece")
        brier = metrics.get("brier")
        return {
            "id": row.get("id"),
            "version": row.get("version"),
            "status": row.get("status"),
            "created_at": row.get("created_at"),
            "auc": metrics.get("auc"),
            "brier": brier,
            "ece": ece,
            "samples": metrics.get("samples"),
            "val_samples": metrics.get("val_samples"),
            "cv_auc_mean": (auc_cv.get("mean") if isinstance(auc_cv, dict) else None),
            "cv_auc_std": (auc_cv.get("std") if isinstance(auc_cv, dict) else None),
            "cv_splits_used": cv.get("splits_used"),
        }
    out: dict[str, any] = {}
    try:
        rows = await repo.fetch_latest(name, model_type, limit=max(1, recent+2))
        prod = next((r for r in rows if r.get("status") == "production"), None)
        if prod:
            out["production"] = _extract(prod)
        # candidates = first N staging (exclude prod id)
        cand_rows = [r for r in rows if r.get("id") != (prod.get("id") if prod else None)]
        out["candidates"] = [_extract(r) for r in cand_rows[:recent]]
    except Exception as e:
        out["error_primary"] = str(e)
    if include_sentiment:
        try:
            s_rows = await repo.fetch_latest("ohlcv_sentiment_predictor", model_type, limit=max(1, recent+2))
            s_prod = next((r for r in s_rows if r.get("status") == "production"), None)
            block: dict[str, any] = {}
            if s_prod:
                block["production"] = _extract(s_prod)
            s_cand = [r for r in s_rows if r.get("id") != (s_prod.get("id") if s_prod else None)]
            block["candidates"] = [_extract(r) for r in s_cand[:recent]]
            out["sentiment"] = block
        except Exception as e:
            out["sentiment_error"] = str(e)
    return {"status": "ok", "model": name, "summary": out}

# --- Admin: Reset models and training data ----------------------------------
@app.post("/admin/models/reset", dependencies=[Depends(require_api_key)])
async def admin_models_reset(drop_features: bool = False):
    """Dangerous: delete model registry rows, lifecycle audit, training jobs, and remove artifacts.

    Parameters:
    - drop_features: when true, also clears feature backfill run metadata (does not delete OHLCV candles).
    """
    pool = await init_pool()
    if pool is None:
        return JSONResponse({"status": "error", "error": "db_unavailable"}, status_code=503)
    deleted_counts: dict[str, int] = {}
    errors: list[str] = []
    async with pool.acquire() as conn:  # type: ignore
        async def _exec(sql: str, label: str):
            try:
                res = await conn.execute(sql)
                n = 0
                try:
                    parts = str(res).strip().split()
                    n = int(parts[-1]) if parts and parts[-1].isdigit() else 0
                except Exception:
                    n = 0
                deleted_counts[label] = deleted_counts.get(label, 0) + n
            except Exception as e:  # noqa: BLE001
                errors.append(f"{label}:{e}")

        # Delete audit/history first to avoid FK issues
        await _exec("DELETE FROM model_metrics_history;", "model_metrics_history")
        await _exec("DELETE FROM model_lineage;", "model_lineage")
        await _exec("DELETE FROM promotion_events;", "promotion_events")
        await _exec("DELETE FROM retrain_events;", "retrain_events")
        await _exec("DELETE FROM training_jobs;", "training_jobs")
        await _exec("DELETE FROM model_registry;", "model_registry")
        if drop_features:
            await _exec("DELETE FROM feature_backfill_runs;", "feature_backfill_runs")

    # Remove on-disk artifacts (best-effort)
    artifacts_removed: list[str] = []
    artifacts_errors: list[str] = []
    try:
        base = getattr(cfg, "model_artifact_dir", None)
        if base:
            p = Path(str(base))
            if p.exists() and p.is_dir():
                for f in p.glob("*.json"):
                    try:
                        f.unlink()
                        artifacts_removed.append(str(f))
                    except Exception as e:  # noqa: BLE001
                        artifacts_errors.append(f"unlink:{f.name}:{e}")
    except Exception as e:  # noqa: BLE001
        artifacts_errors.append(f"artifact_scan:{e}")

    try:
        await refresh_production_metrics()
    except Exception:
        pass

    return JSONResponse({
        "status": "ok",
        "deleted": deleted_counts,
        "artifact_removed_count": len(artifacts_removed),
        "artifact_errors": artifacts_errors,
        "errors": errors,
    })

@app.get("/api/inference/sentiment", dependencies=[Depends(require_api_key)])
async def api_inference_sentiment(horizon: int = 5):
    """Sentiment 확장 모델(ohlcv_sentiment_predictor) 추론.

    - 최신 OHLCV + rolling sentiment (60m & 15m) 기반 샘플 구성
    - production 우선, 없으면 최신 staging 모델 사용
    - 결과: 확률, threshold 대비 의사결정, 사용 피처 수/목록
    """
    svc = TrainingService(symbol=cfg.symbol, interval=cfg.kline_interval, artifact_dir=cfg.model_artifact_dir)
    out = await svc.predict_latest_sentiment(horizon=horizon)
    return out

@app.get("/admin/api-key/status", dependencies=[Depends(require_api_key)])
async def api_key_status():
    key = getattr(app.state, "current_api_key", API_KEY)
    return {
        "placeholder_in_use": getattr(app.state, "api_key_placeholder", False),
        "length": len(key) if isinstance(key, str) else None,
        "auto_generated": getattr(app.state, "generated_api_key", False),
    }

@app.post("/admin/api-key/rotate")
async def api_key_rotate(req: RotateKeyRequest, request: Request, x_api_key: Optional[str] = Header(default=None)):
    current = getattr(app.state, "current_api_key", API_KEY)
    # Validate old key
    if x_api_key != current:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    # Decide new key
    new_key = req.new_key.strip() if (req.new_key and req.new_key.strip()) else secrets.token_hex(24)
    app.state.current_api_key = new_key
    app.state.api_key_placeholder = False
    # Security note: returning the key here is acceptable in local/dev; for prod could suppress.
    return {"status": "ok", "new_key": new_key, "length": len(new_key)}

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "env": cfg.environment}

@app.get("/health/live")
async def health_live():
    return {"status": "alive"}

@app.get("/health/ready")
async def health_ready():
    # Basic readiness: DB reachable + feature service instantiated
    ready = True
    reasons: list[str] = []
    # Trigger background ensure (non-blocking)
    await ensure_pool_background()
    ps = pool_status()
    if not ps.get("has_pool"):
        ready = False
        reasons.append("db_unreachable")
    if not getattr(app.state, "feature_service", None):
        reasons.append("feature_service_missing")
    # In fast startup mode we intentionally skip some components; annotate but don't mark not_ready solely for those.
    skipped = getattr(app.state, 'skipped_components', [])
    if skipped:
        reasons.extend([f"skipped:{c}" for c in skipped])
    if getattr(app.state, 'degraded_components', None):
        reasons.extend(getattr(app.state, 'degraded_components'))
    return {"status": "ready" if ready and not reasons else "not_ready", "reasons": reasons, "db": ps}

@app.get("/health/extended")
async def extended_health():
    now = time.time()
    # DB ping
    db_ok = True
    db_latency_ms: Optional[float] = None
    from backend.common.db.connection import init_pool
    try:
        pool = await init_pool()
        t0 = time.perf_counter()
        async with pool.acquire() as conn:  # type: ignore
            await conn.fetchval("SELECT 1")
        db_latency_ms = (time.perf_counter() - t0) * 1000
    except Exception:
        db_ok = False

    # Feature scheduler
    feature_svc: Optional[FeatureService] = getattr(app.state, "feature_service", None)
    feature_status = {
        "running": bool(feature_svc),
        "last_success_ts": getattr(feature_svc, "last_success_ts", None),
        "lag_sec": None,
        "status": "unknown",
    }
    if feature_svc and feature_svc.last_success_ts:
        lag = now - feature_svc.last_success_ts
        feature_status["lag_sec"] = lag
        feature_status["status"] = "ok" if lag <= cfg.health_max_feature_lag_sec else "degraded"
    elif feature_svc:
        feature_status["status"] = "pending"
    else:
        feature_status["status"] = "disabled"

    # Ingestion
    consumer = getattr(app.state, "kline_consumer", None)
    ingestion_status = {
        "enabled": cfg.ingestion_enabled,
        "running": bool(consumer and consumer._running),
        "last_message_ts": getattr(consumer, "last_message_ts", None),
        "lag_sec": None,
        "status": "disabled" if not cfg.ingestion_enabled else "unknown",
    }
    if cfg.ingestion_enabled and consumer and consumer.last_message_ts:
        lag = now - consumer.last_message_ts
        ingestion_status["lag_sec"] = lag
        ingestion_status["status"] = "ok" if lag <= cfg.health_max_ingestion_lag_sec else "degraded"
    elif cfg.ingestion_enabled and consumer and not consumer.last_message_ts:
        ingestion_status["status"] = "pending"
    elif cfg.ingestion_enabled and not consumer:
        ingestion_status["status"] = "stopped"

    overall = "ok"
    for comp in (feature_status, ingestion_status):
        if comp.get("status") in ("degraded", "stopped"):
            overall = "degraded"
    if not db_ok:
        overall = "error"

    return {
        "status": overall,
        "environment": cfg.environment,
        "db": {"ok": db_ok, "latency_ms": db_latency_ms},
        "feature_scheduler": feature_status,
        "ingestion": ingestion_status,
        "thresholds": {
            "feature_lag_sec": cfg.health_max_feature_lag_sec,
            "ingestion_lag_sec": cfg.health_max_ingestion_lag_sec,
        },
        "timestamp": now,
    }

# Helper used by system status builder
async def ingestion_status():  # type: ignore[func-returns-value]
    consumer = getattr(app.state, "kline_consumer", None)
    now = time.time()
    status = {
        "enabled": cfg.ingestion_enabled,
        "running": bool(consumer and getattr(consumer, "_running", False)),
        "last_message_ts": getattr(consumer, "last_message_ts", None),
        "lag_sec": None,
        "status": "disabled" if not cfg.ingestion_enabled else "unknown",
    }
    db_latest_close_time_ms: int | None = None
    db_lag_sec: float | None = None
    if cfg.ingestion_enabled:
        try:
            recent = await fetch_kline_recent((cfg.symbol or "").upper(), cfg.kline_interval, limit=1)
            if recent:
                row = recent[0]
                close_time = row.get("close_time") or 0
                if not close_time and row.get("open_time"):
                    try:
                        interval = cfg.kline_interval or "1m"
                        unit = interval[-1]
                        value = int(interval[:-1] or "1")
                        multiplier = 60 if unit == "m" else 3600 if unit == "h" else 86400 if unit == "d" else 60
                        close_time = int(row["open_time"]) + value * multiplier * 1000
                    except Exception:
                        close_time = int(row.get("open_time", 0))
                if close_time:
                    db_latest_close_time_ms = int(close_time)
                    db_lag_sec = max(0.0, now - (close_time / 1000.0))
        except Exception:
            db_latest_close_time_ms = None
            db_lag_sec = None
    # Merge detailed consumer status if available
    try:
        if cfg.ingestion_enabled and consumer and hasattr(consumer, "status"):
            # KlineConsumer.status() exposes detailed counters/metrics
            detail = consumer.status()
            # Whitelist merge to avoid surprising fields
            for k in (
                "symbol", "interval", "buffer_size", "total_messages", "total_closed",
                "batch_size", "flush_interval", "reconnect_attempts", "last_flush_ts",
                "last_closed_kline_close_time",
            ):
                if k in detail:
                    status[k] = detail[k]
    except Exception:
        pass
    if cfg.ingestion_enabled and consumer and getattr(consumer, "last_message_ts", None):
        try:
            lag = now - consumer.last_message_ts  # type: ignore[attr-defined]
            status["lag_sec"] = lag
            status["status"] = "ok" if lag <= cfg.health_max_ingestion_lag_sec else "degraded"
            # Convenience fields
            status["lag_seconds"] = lag
            status["stale"] = bool(lag > cfg.health_max_ingestion_lag_sec)
            status["lag_source"] = "consumer"
        except Exception:
            pass
    elif cfg.ingestion_enabled and consumer and not getattr(consumer, "last_message_ts", None):
        status["status"] = "pending"
    elif cfg.ingestion_enabled and not consumer:
        status["status"] = "stopped"

    if db_lag_sec is not None:
        status["db_lag_sec"] = db_lag_sec
    if db_latest_close_time_ms is not None:
        status["db_latest_close_time"] = db_latest_close_time_ms

    if cfg.ingestion_enabled:
        if status.get("stale") or status.get("status") in {"degraded", "stopped", "pending", "unknown"}:
            if db_lag_sec is not None and db_lag_sec <= cfg.health_max_ingestion_lag_sec:
                status["status"] = "ok"
                status["stale"] = False
                if status.get("lag_sec") is None or (status.get("lag_sec") or 0) > db_lag_sec:
                    status["lag_sec"] = db_lag_sec
                status["lag_source"] = "db_fallback"
        elif db_lag_sec is not None and status.get("lag_sec") is None:
            status["lag_sec"] = db_lag_sec
            status["lag_source"] = "db_only"
    return status

@app.get("/api/version")
async def version():
    return {"version": app.version}

@app.get("/admin/db/status")
async def admin_db_status():
    return pool_status()

@app.post("/admin/db/retry")
async def admin_db_retry():
    result = await force_pool_retry()
    return result

@app.get("/api/ingestion/status")
async def api_ingestion_status(_auth: bool = Depends(require_api_key)):
        """현재 실시간 Kline ingestion 컴포넌트 상태.

        반환 필드:
            enabled: 구성상 활성 여부
            running: consumer 루프 실행 여부
            last_message_ts: 마지막 수신 Unix epoch (없으면 null)
            lag_sec: 마지막 메시지 이후 경과 초 (있을 때만)
            status: ok|degraded|pending|stopped|disabled|unknown
        """
        st = await ingestion_status()
        return st

@app.post("/admin/fast_startup/upgrade")
async def admin_fast_startup_upgrade():
    """When FAST_STARTUP was enabled, start the skipped background components now.

    Idempotent: if components already running or fast mode was off, returns current state.
    """
    if not getattr(app.state, 'fast_startup', False):
        return {"status": "not_fast_mode"}
    started: list[str] = []
    errors: list[str] = []
    db_ok = pool_status().get("has_pool")
    feature_service: FeatureService = getattr(app.state, "feature_service")
    # Feature scheduler
    if "feature_scheduler" in getattr(app.state, 'skipped_components', []):
        if db_ok:
            try:
                app.state.feature_task = asyncio.create_task(
                    feature_scheduler(feature_service, interval_seconds=cfg.feature_sched_interval)
                )
                started.append("feature_scheduler")
            except Exception as e:
                errors.append(f"feature_scheduler:{e}")
        else:
            errors.append("feature_scheduler:db_unavailable")
    # Production metrics loop
    if "production_metrics" in getattr(app.state, 'skipped_components', []):
        try:
            if getattr(app.state, 'production_metrics_task', None) is None or getattr(getattr(app.state, 'production_metrics_task', None), 'done', lambda: True)():
                app.state.production_metrics_task = asyncio.create_task(
                    production_metrics_loop(interval=getattr(cfg, "production_metrics_interval", 60.0))
                )
            started.append("production_metrics")
        except Exception as e:  # noqa: BLE001
            errors.append(f"production_metrics:{e}")
    # Ingestion
    if cfg.ingestion_enabled and "ingestion" in getattr(app.state, 'skipped_components', []):
        try:
            consumer = KlineConsumer(cfg.symbol, cfg.kline_interval)
            app.state.kline_consumer = consumer
            app.state.kline_task = asyncio.create_task(consumer.start())
            started.append("ingestion")
        except Exception as e:
            errors.append(f"ingestion:{e}")
    # Auto retrain
    if cfg.auto_retrain_enabled and "auto_retrain" in getattr(app.state, 'skipped_components', []):
        if db_ok:
            try:
                app.state.auto_retrain_task = asyncio.create_task(auto_retrain_loop(feature_service))
                started.append("auto_retrain")
            except Exception as e:
                errors.append(f"auto_retrain:{e}")
        else:
            errors.append("auto_retrain:db_unavailable")
    # News ingestion
    if 'news_ingestion' in getattr(app.state, 'skipped_components', []):
        try:
            # Ensure service exists
            if getattr(app.state, 'news_service', None) is None:
                app.state.news_service = NewsService(symbol=cfg.symbol)
            if getattr(app.state, 'news_task', None) is None:
                interval = getattr(app.state, 'news_poll_interval', None)
                if not isinstance(interval, (int,float)):
                    with contextlib.suppress(Exception):
                        _v = os.getenv('NEWS_POLL_INTERVAL', '90')
                        if isinstance(_v, str) and '#' in _v:
                            _v = _v.split('#', 1)[0].strip()
                        interval = float(_v)
                if not isinstance(interval, (int,float)):
                    interval = 90
                app.state.news_task = asyncio.create_task(news_loop(app.state.news_service, interval=interval))  # type: ignore
            started.append('news_ingestion')
        except Exception as e:  # noqa: BLE001
            errors.append(f"news_ingestion:{e}")
    # Auto labeler
    if cfg.auto_labeler_enabled and "auto_labeler" in getattr(app.state, 'skipped_components', []):
        try:
            labeler = get_auto_labeler_service()
            await labeler.start()
            started.append("auto_labeler")
        except Exception as e:
            errors.append(f"auto_labeler:{e}")
    # Calibration monitor
    if cfg.calibration_monitor_enabled and "calibration_monitor" in getattr(app.state, 'skipped_components', []):
        if db_ok:
            try:
                app.state.calibration_monitor_task = asyncio.create_task(calibration_monitor_loop())
                started.append("calibration_monitor")
            except Exception as e:
                errors.append(f"calibration_monitor:{e}")
        else:
            errors.append("calibration_monitor:db_unavailable")
    # Remove started from skipped list
    for s in started:
        if s in getattr(app.state, 'skipped_components', []):
            app.state.skipped_components.remove(s)
    # If nothing left skipped, clear flag
    if not getattr(app.state, 'skipped_components', []):
        app.state.fast_startup = False
    return {
        "status": "upgraded" if started else "no_change",
        "started": started,
        "remaining_skipped": getattr(app.state, 'skipped_components', []),
        "errors": errors,
        "db": pool_status(),
    }

@app.get("/admin/fast_startup/status")
async def admin_fast_startup_status():
    """Simple fast-startup status snapshot for UI.

    Returns: { fast_startup, skipped_components, degraded_components, upgrade_possible, db }
    """
    try:
        fast = bool(getattr(app.state, 'fast_startup', False))
    except Exception:
        fast = False
    try:
        skipped = list(getattr(app.state, 'skipped_components', []))
    except Exception:
        skipped = []
    try:
        degraded = list(getattr(app.state, 'degraded_components', []))
    except Exception:
        degraded = []
    try:
        db = pool_status()
    except Exception:
        db = {"has_pool": None}
    upgrade_possible = bool(fast and skipped)
    return {
        "fast_startup": fast,
        "skipped_components": skipped,
        "degraded_components": degraded,
        "upgrade_possible": upgrade_possible,
        "db": db,
    }

@app.post("/admin/fast_startup/start_component")
async def admin_fast_start_single(component: str):
    """FAST_STARTUP 모드에서 특정 컴포넌트만 개별 기동.

    현재 지원:
      - news_ingestion
      - feature_scheduler
      - ingestion (kline)
      - auto_retrain
      - auto_labeler
    - calibration_monitor
    - production_metrics
    이미 실행 중이거나 fast_startup=false 인 경우 idempotent 처리.
    """
    if not getattr(app.state, 'fast_startup', False):
        return {"status": "not_fast_mode"}
    skipped = getattr(app.state, 'skipped_components', [])
    if component not in skipped:
        return {"status": "not_skipped", "component": component, "skipped_components": skipped}
    db_ok = pool_status().get("has_pool")
    started = False
    error: Optional[str] = None
    feature_service: FeatureService = getattr(app.state, "feature_service")
    try:
        if component == "news_ingestion":
            # start news task
            if getattr(app.state, 'news_task', None) is None:
                interval = getattr(app.state, 'news_poll_interval', None)
                if not isinstance(interval, (int,float)):
                    with contextlib.suppress(Exception):
                        _v = os.getenv('NEWS_POLL_INTERVAL', '90')
                        if isinstance(_v, str) and '#' in _v:
                            _v = _v.split('#', 1)[0].strip()
                        interval = float(_v)
                if not isinstance(interval, (int,float)):
                    interval = 90
                app.state.news_task = asyncio.create_task(news_loop(app.state.news_service, interval=interval))  # type: ignore
            started = True
        elif component == "feature_scheduler":
            if db_ok:
                app.state.feature_task = asyncio.create_task(feature_scheduler(feature_service, interval_seconds=cfg.feature_sched_interval))
                started = True
            else:
                error = "db_unavailable"
        elif component == "ingestion":
            if cfg.ingestion_enabled:
                consumer = KlineConsumer(cfg.symbol, cfg.kline_interval, batch_size=max(1, cfg.kline_consumer_batch_size))
                app.state.kline_consumer = consumer
                # Register listener for append broadcast after restart path
                try:  # pragma: no cover
                    if ohlcv_ws_manager:
                        async def _on_flush(batch):
                            from prometheus_client import Counter
                            try:
                                BROADCAST_APPEND_ATTEMPTS = Counter("ohlcv_ws_broadcast_append_attempts_total", "WS append broadcast attempts", ["symbol"])  # lazy define
                                BROADCAST_APPEND_SENT = Counter("ohlcv_ws_broadcast_append_sent_total", "WS append messages successfully queued", ["symbol"])  # lazy define
                            except Exception:
                                BROADCAST_APPEND_ATTEMPTS = None
                                BROADCAST_APPEND_SENT = None
                            out = []
                            for k in batch:
                                try:
                                    out.append({
                                        "open_time": k.get("t"),
                                        "close_time": k.get("T"),
                                        "open": float(k.get("o")) if k.get("o") is not None else None,
                                        "high": float(k.get("h")) if k.get("h") is not None else None,
                                        "low": float(k.get("l")) if k.get("l") is not None else None,
                                        "close": float(k.get("c")) if k.get("c") is not None else None,
                                        "volume": float(k.get("v")) if k.get("v") is not None else None,
                                        "is_closed": True,
                                    })
                                except Exception:
                                    pass
                            if out:
                                if BROADCAST_APPEND_ATTEMPTS:
                                    with contextlib.suppress(Exception):
                                        BROADCAST_APPEND_ATTEMPTS.labels(cfg.symbol.lower()).inc()
                                try:
                                    await ohlcv_ws_manager.broadcast_append(out)
                                    if BROADCAST_APPEND_SENT:
                                        with contextlib.suppress(Exception):
                                            BROADCAST_APPEND_SENT.labels(cfg.symbol.lower()).inc()
                                except Exception:
                                    pass
                        consumer.add_flush_listener(_on_flush)
                except Exception:
                    pass
                app.state.kline_task = asyncio.create_task(consumer.start())
                started = True
            else:
                error = "ingestion_disabled"
        elif component == "auto_retrain":
            if cfg.auto_retrain_enabled and db_ok:
                app.state.auto_retrain_task = asyncio.create_task(auto_retrain_loop(feature_service))
                started = True
            else:
                error = "not_enabled_or_db"
        elif component == "auto_labeler":
            if cfg.auto_labeler_enabled:
                labeler = get_auto_labeler_service()
                await labeler.start()
                started = True
            else:
                error = "not_enabled"
        elif component == "calibration_monitor":
            if cfg.calibration_monitor_enabled and db_ok:
                app.state.calibration_monitor_task = asyncio.create_task(calibration_monitor_loop())
                started = True
            else:
                error = "not_enabled_or_db"
        elif component == "production_metrics":
            # Start the production metrics refresh loop
            if getattr(app.state, 'production_metrics_task', None) is None or getattr(getattr(app.state, 'production_metrics_task', None), 'done', lambda: True)():
                app.state.production_metrics_task = asyncio.create_task(
                    production_metrics_loop(interval=getattr(cfg, "production_metrics_interval", 60.0))
                )
            started = True
        else:
            return {"status": "unknown_component", "component": component}
    except Exception as e:  # noqa: BLE001
        error = repr(e)
    if started and component in skipped:
        skipped.remove(component)
    # fast 모드 해제 조건: 더 이상 skipped 없음
    if not skipped:
        app.state.fast_startup = False
    return {
        "status": "started" if started else "failed",
        "component": component,
        "error": error,
        "remaining_skipped": skipped,
        "fast_startup": getattr(app.state, 'fast_startup', False),
    }

@app.get("/admin/fast_startup/status")
async def admin_fast_startup_status():
    """현재 FAST_STARTUP 모드 및 생략/저하 컴포넌트 상태 조회.

    Response 필드:
      fast_startup: 아직 빠른 기동 모드인지 여부 (True 이면 일부 백그라운드 생략 중)
      skipped_components: 아직 시작하지 않은(생략된) 컴포넌트 리스트
      degraded_components: 시작은 했지만 정상 동작 못한(저하) 컴포넌트 리스트
      upgrade_possible: 즉시 업그레이드( /admin/fast_startup/upgrade ) 호출 의미가 있는지 여부
      db: DB 풀 상태 (pool_status())
    """
    skipped = getattr(app.state, 'skipped_components', [])
    degraded = getattr(app.state, 'degraded_components', [])
    fast = bool(getattr(app.state, 'fast_startup', False))
    return {
        "fast_startup": fast,
        "skipped_components": skipped,
        "degraded_components": degraded,
        "upgrade_possible": fast and bool(skipped),
        "db": pool_status(),
    }

# ---------------------------------------------------------------------------
# Ingestion / WS Append 상태 노출
# ---------------------------------------------------------------------------
@app.get("/api/ohlcv/ingestion_status")
async def api_ohlcv_ingestion_status():
    consumer = getattr(app.state, "kline_consumer", None)
    status = None
    if consumer:
        try:
            status = consumer.status()
        except Exception:
            status = {"error": "status_failed"}
    counters = {}
    try:
        from prometheus_client import REGISTRY  # type: ignore
        for metric in REGISTRY.collect():  # type: ignore
            if metric.name in ("ohlcv_ws_broadcast_append_attempts_total", "ohlcv_ws_broadcast_append_sent_total"):
                per_label = []
                total = 0.0
                for s in metric.samples:  # type: ignore
                    try:
                        v = float(s.value)
                        total += v
                        per_label.append({"labels": getattr(s, 'labels', {}), "value": v})
                    except Exception:
                        pass
                counters[metric.name] = {"total": total, "samples": per_label}
    except Exception:
        pass
    return {"consumer": status, "broadcast_counters": counters}

# ---------------------------------------------------------------------------
# Debug: 강제 flush 로 append 이벤트 즉시 유도 (테스트/운영 주의)
# ---------------------------------------------------------------------------
@app.post("/admin/ohlcv/force_flush")
async def admin_ohlcv_force_flush():
    consumer = getattr(app.state, "kline_consumer", None)
    if not consumer:
        raise HTTPException(status_code=404, detail="consumer_not_running")
    # 내부 flush 메서드는 비공개이므로 getattr 사용 (테스트 목적)
    flush_fn = getattr(consumer, "_flush", None)
    if not flush_fn:
        raise HTTPException(status_code=500, detail="flush_unavailable")
    # 현재 버퍼 크기 파악
    before = consumer.status().get("buffer_size") if hasattr(consumer, "status") else None
    await flush_fn()
    after = consumer.status().get("buffer_size") if hasattr(consumer, "status") else None
    return {"forced": True, "buffer_before": before, "buffer_after": after}

# API alias for dev proxy convenience
@app.post("/api/admin/ohlcv/force_flush")
async def api_admin_ohlcv_force_flush():
    return await admin_ohlcv_force_flush()

# ---------------------------------------------------------------------------
# Debug: mock append broadcast (DB 최근 캔들 재사용) - 테스트용
# ---------------------------------------------------------------------------
@app.post("/admin/ohlcv/mock_append")
async def admin_ohlcv_mock_append(count: int = 1):
    if count < 1 or count > 5:
        raise HTTPException(status_code=400, detail="count_range_1_5")
    if ohlcv_ws_manager is None:
        raise HTTPException(status_code=503, detail="ws_manager_not_ready")
    from backend.common.db.connection import init_pool
    pool = await init_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="db_pool_unavailable")
    rows: list[dict] = []
    async with pool.acquire() as conn:  # type: ignore
        recs = await conn.fetch(
            """
            SELECT open_time, close_time, open, high, low, close, volume
            FROM ohlcv_candles
            WHERE symbol=$1 AND interval=$2
            ORDER BY open_time DESC
            LIMIT $3
            """,
            cfg.symbol.upper(), cfg.kline_interval, count,
        )
        for r in recs:
            rows.append({
                "open_time": int(r["open_time"]),
                "close_time": int(r["close_time"]),
                "open": float(r["open"]) if r["open"] is not None else None,
                "high": float(r["high"]) if r["high"] is not None else None,
                "low": float(r["low"]) if r["low"] is not None else None,
                "close": float(r["close"]) if r["close"] is not None else None,
                "volume": float(r["volume"]) if r["volume"] is not None else None,
                "is_closed": True,
            })
    # ASC 정렬 보장 및 브로드캐스트 메트릭 증가
    rows.sort(key=lambda x: x["open_time"])
    from prometheus_client import Counter as _Counter  # lazy import
    try:  # 메트릭 레지스트리 중복 등록 예외 방지
        BROADCAST_APPEND_ATTEMPTS = _Counter("ohlcv_ws_broadcast_append_attempts_total", "WS append broadcast attempts", ["symbol"])  # type: ignore
        BROADCAST_APPEND_SENT = _Counter("ohlcv_ws_broadcast_append_sent_total", "WS append messages successfully queued", ["symbol"])  # type: ignore
    except Exception:
        BROADCAST_APPEND_ATTEMPTS = None  # type: ignore
        BROADCAST_APPEND_SENT = None  # type: ignore
    if rows and BROADCAST_APPEND_ATTEMPTS:
        with contextlib.suppress(Exception):
            BROADCAST_APPEND_ATTEMPTS.labels(cfg.symbol.lower()).inc()
    try:
        await ohlcv_ws_manager.broadcast_append(rows)
        if rows and BROADCAST_APPEND_SENT:
            with contextlib.suppress(Exception):
                BROADCAST_APPEND_SENT.labels(cfg.symbol.lower()).inc()
    except Exception as e:  # noqa: BLE001
        return {"broadcast": "append", "count": len(rows), "open_times": [r["open_time"] for r in rows], "error": str(e)}
    return {"broadcast": "append", "count": len(rows), "open_times": [r["open_time"] for r in rows]}

# ---------------------------------------------------------------------------
# Admin: 현재 프로세스 PID 확인 (멀티 워커 디버깅 용도)
# ---------------------------------------------------------------------------
@app.get("/admin/system/pid")
async def admin_system_pid():
    import os
    return {"pid": os.getpid()}

# ---------------------------------------------------------------------------
# Admin: kline consumer runtime batch_size 조정 (디버깅/튜닝용)
# ---------------------------------------------------------------------------
@app.post("/admin/ohlcv/set_batch_size")
async def admin_ohlcv_set_batch_size(size: int = Body(..., embed=True)):
    if size < 1 or size > 500:
        raise HTTPException(status_code=400, detail="size_range_1_500")
    consumer = getattr(app.state, "kline_consumer", None)
    if not consumer:
        raise HTTPException(status_code=404, detail="consumer_not_running")
    # 비공개 속성 직접 변경 (테스트 목적)
    old = getattr(consumer, "_batch_size", None)
    setattr(consumer, "_batch_size", size)
    return {"updated": True, "old": old, "new": size}

# API alias for dev proxy convenience
@app.post("/api/admin/ohlcv/set_batch_size")
async def api_admin_ohlcv_set_batch_size(size: int = Body(..., embed=True)):
    return await admin_ohlcv_set_batch_size(size)

# ---------------------------------------------------------------------------
# Admin: kline consumer runtime flush_interval 조정
# ---------------------------------------------------------------------------
@app.post("/admin/ohlcv/set_flush_interval")
async def admin_ohlcv_set_flush_interval(interval: float = Body(..., embed=True)):
    if interval <= 0:
        raise HTTPException(status_code=400, detail="interval_must_be_positive")
    consumer = getattr(app.state, "kline_consumer", None)
    if not consumer:
        raise HTTPException(status_code=404, detail="consumer_not_running")
    old = getattr(consumer, "_flush_interval", None)
    setattr(consumer, "_flush_interval", float(interval))
    return {"updated": True, "old": old, "new": interval}

# API alias for dev proxy convenience
@app.post("/api/admin/ohlcv/set_flush_interval")
async def api_admin_ohlcv_set_flush_interval(interval: float = Body(..., embed=True)):
    return await admin_ohlcv_set_flush_interval(interval)

# ---------------------------------------------------------------------------
# Admin: kline consumer 재연결(재시작) 트리거
# ---------------------------------------------------------------------------
@app.post("/admin/ohlcv/reconnect")
async def admin_ohlcv_reconnect():
    consumer = getattr(app.state, "kline_consumer", None)
    if not consumer:
        # 없으면 생성해서 시작
        c = KlineConsumer(cfg.symbol, cfg.kline_interval, batch_size=max(1, cfg.kline_consumer_batch_size))
        app.state.kline_consumer = c
        started = _start_task_once("kline_task", c.start, label="ingestion")
        return {"status": "started" if started else "already_running"}
    # 있으면 중단 후 다시 시작
    try:
        await consumer.stop()
    except Exception:
        pass
    started = _start_task_once("kline_task", consumer.start, label="ingestion")
    return {"status": "restarted" if started else "already_running"}

# API alias for dev proxy convenience
@app.post("/api/admin/ohlcv/reconnect")
async def api_admin_ohlcv_reconnect():
    return await admin_ohlcv_reconnect()

# --- Model Artifact Verification (detect missing artifact files) ---
@app.get("/admin/models/artifacts/verify", dependencies=[Depends(require_api_key)])
async def admin_models_artifacts_verify(limit_per_model: int = 15, auto_retrain_if_missing: bool = False):
    """Verify that artifact_path files referenced in recent registry rows exist on disk.

    Returns list with status per registry row:
      - ok: file exists (or seed_baseline with no artifact required)
      - missing: artifact_path is null but not a seed baseline
      - file_not_found: artifact_path set but file missing
    """
    repo = ModelRegistryRepository()
    # Heuristic: gather distinct model names from recent registry fetch attempts.
    # Since we lack a list endpoint, infer from common configured names.
    candidate_names = [
        getattr(cfg, "auto_promote_model_name", "bottom_predictor"),
        "bottom_predictor",
        "ohlcv_sentiment_predictor",
        "ohlcv_sentiment_predictor".replace("ohlcv", "ohlcv")  # placeholder to avoid empty list
    ]
    seen: set[str] = set()
    results: list[dict] = []
    retrain_triggered: list[str] = []
    for name in candidate_names:
        if name in seen:
            continue
        seen.add(name)
        try:
            rows = await repo.fetch_latest(name, "supervised", limit=limit_per_model)
        except Exception as e:  # noqa: BLE001
            results.append({"model": name, "error": f"registry_fetch_error:{e}"})
            continue
        missing_flag_for_model = False
        for r in rows:
            status_val = str(r.get("status") or "").strip().lower()
            if status_val in {"deleted", "archived"}:
                # Skip soft-deleted or archived rows; their artifacts are expected to be absent.
                continue
            metrics = r.get("metrics") or {}
            artifact_path = r.get("artifact_path")
            status_row = "ok"
            if not artifact_path:
                if not metrics.get("seed_baseline"):
                    status_row = "missing"
            else:
                try:
                    from pathlib import Path
                    p = Path(artifact_path)
                    if not p.exists():
                        status_row = "file_not_found"
                except Exception:
                    status_row = "file_check_error"
            results.append({
                "model": name,
                "id": r.get("id"),
                "version": r.get("version"),
                "status": r.get("status"),
                "artifact_path": artifact_path,
                "seed_baseline": bool(metrics.get("seed_baseline")),
                "artifact_status": status_row,
            })
            if status_row in ("missing", "file_not_found"):
                missing_flag_for_model = True
        # Auto retrain once per model if any row missing AND flag requested
        if auto_retrain_if_missing and missing_flag_for_model:
            try:
                svc = _training_service()
                # Fire and forget training (manual trigger). Provide minimal context in response.
                asyncio.create_task(svc.run_training(trigger="manual_verify_autorepair"))  # type: ignore[attr-defined]
                retrain_triggered.append(name)
            except Exception as e:
                retrain_triggered.append(f"{name}:error:{e}")
    # Aggregate summary counts
    summary: dict[str, int] = {"ok":0,"missing":0,"file_not_found":0,"file_check_error":0}
    for it in results:
        st = it.get("artifact_status")
        if isinstance(st, str) and st in summary:
            summary[st] += 1
    return {"status": "ok", "summary": summary, "rows": results, "models_checked": list(seen), "auto_retrain": auto_retrain_if_missing, "retrain_triggered": retrain_triggered}

# --- Admin: Relink model artifacts to current base dir (safe migration) ---
@app.post("/admin/models/artifacts/relink", dependencies=[Depends(require_api_key)])
async def admin_models_artifacts_relink(
    limit_per_model: int = 25,
    dry_run: bool = True,
    base_dir: Optional[str] = None,
):
    """Relink registry rows' artifact_path to files found under the current artifact base directory.

    Use when registry points to absolute paths from a different environment (e.g., Docker vs Windows).

    Strategy:
      - For known model names (bottom_predictor, ohlcv_sentiment_predictor), fetch recent rows
      - For each row, compute expected file path: <base_dir>/<name>__<version>.json
      - If the file exists but artifact_path is missing or points elsewhere, update to the expected file path

    Parameters:
      - limit_per_model: how many recent rows per model name to examine
      - dry_run: if true, only report planned changes without updating DB
      - base_dir: override for artifact base directory (defaults to cfg.model_artifact_dir)
    """
    from pathlib import Path
    repo = ModelRegistryRepository()
    base = Path(str(base_dir or getattr(cfg, "model_artifact_dir", "artifacts/models")))
    if not base.exists():
        return JSONResponse({"status": "error", "error": f"base_dir_not_found:{base}"}, status_code=400)
    candidate_names = [
        getattr(cfg, "auto_promote_model_name", "bottom_predictor"),
        "bottom_predictor",
        "ohlcv_sentiment_predictor",
    ]
    seen: set[str] = set()
    examined = 0
    relinked = 0
    planned: list[dict] = []
    errors: list[str] = []
    for name in candidate_names:
        if name in seen:
            continue
        seen.add(name)
        try:
            rows = await repo.fetch_latest(name, "supervised", limit=max(1, limit_per_model))
        except Exception as e:  # noqa: BLE001
            errors.append(f"registry_fetch:{name}:{e}")
            continue
        for r in rows:
            examined += 1
            rid = r.get("id")
            ver = r.get("version")
            if not rid or not ver:
                continue
            current = r.get("artifact_path")
            expected = base / f"{name}__{ver}.json"
            try:
                exists = expected.exists()
            except Exception:
                exists = False
            # Relink criteria: file exists under base and (no current path or current path != expected or current file missing)
            need_update = False
            if exists:
                if not current:
                    need_update = True
                else:
                    try:
                        cur_ok = Path(str(current)).exists()
                    except Exception:
                        cur_ok = False
                    if (not cur_ok) or (str(Path(str(current)).resolve()) != str(expected.resolve())):
                        need_update = True
            if not need_update:
                continue
            planned.append({
                "id": rid,
                "model": name,
                "version": ver,
                "from": current,
                "to": str(expected),
            })
            if not dry_run:
                try:
                    ok = await repo.update_artifact_path(int(rid), str(expected))
                    if ok:
                        relinked += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(f"update:{name}:{ver}:{e}")
    return {
        "status": "ok",
        "base_dir": str(base),
        "dry_run": bool(dry_run),
        "examined": examined,
        "planned": planned,
        "relinked": relinked,
        "errors": errors,
        "models_checked": list(seen),
    }

# --- Manual Training Run Endpoint ---
class TrainingRunRequest(BaseModel):
    trigger: Optional[str] = None  # optional custom trigger label
    sentiment: bool = False     # whether to also train sentiment model if supported
    force: bool = False         # allow bypassing concurrency guard
    horizons: Optional[list[str]] = None  # optional horizon labels to train (e.g., ["1m","5m","15m"])
    ablate_sentiment: bool = False  # run ablation (with vs without sentiment features) in sentiment path
    # New: training target selection and optional bottom params
    target: Optional[str] = None  # bottom-only (legacy 'direction' ignored)
    bottom_lookahead: Optional[int] = None
    bottom_drawdown: Optional[float] = None
    bottom_rebound: Optional[float] = None
    # Optional override for sample limit used in training (defaults to auto_retrain_min_samples)
    limit: Optional[int] = None
    mode: Optional[str] = "baseline"
    store: bool = False
    cv_splits: int = 0
    class_weight: Optional[str] = None
    time_cv: bool = True
    sync: Optional[bool] = None

# In-memory training job tracking (simple volatile store)
_training_jobs: dict[str, dict] = {}
_training_job_order: list[str] = []  # preserve insertion order for history
_training_lock = asyncio.Lock()
_training_active = False  # concurrency guard

def _make_job_id() -> str:
    return uuid.uuid4().hex

def _cap_history(max_keep: int = 200):
    # Trim oldest job ids beyond max_keep
    if len(_training_job_order) > max_keep:
        excess = len(_training_job_order) - max_keep
        for jid in _training_job_order[:excess]:
            _training_jobs.pop(jid, None)
        del _training_job_order[:excess]

async def _launch_training_job(
    trigger: str,
    sentiment: bool,
    force: bool,
    horizons: Optional[list[str]] = None,
    ablate_sentiment: bool = False,
    target: Optional[str] = None,
    bottom_lookahead: Optional[int] = None,
    bottom_drawdown: Optional[float] = None,
    bottom_rebound: Optional[float] = None,
    limit_override: Optional[int] = None,
    mode: Optional[str] = "baseline",
    store: bool = False,
    cv_splits: int = 0,
    class_weight: Optional[str] = None,
    time_cv: bool = True,
    wait_for_completion: bool = False,
) -> tuple[str, dict]:
    """Create job record & launch async task(s). Returns (job_id, info)."""
    mode_used = str(mode or "baseline")
    store_flag = bool(store)
    try:
        cv_splits_int = int(cv_splits)
    except (TypeError, ValueError):
        cv_splits_int = 0
    class_weight_used = class_weight if class_weight is not None else None
    time_cv_flag = bool(time_cv)
    wait_flag = bool(wait_for_completion)
    global _training_active
    async with _training_lock:
        if _training_active and not force:
            raise HTTPException(status_code=409, detail="training_in_progress")
        _training_active = True
        job_id = _make_job_id()
        now = time.time()
        svc = _training_service()
        # Sentiment capability checks for ohlcv+sentiment training entrypoint
        sentiment_capable = hasattr(svc, "run_training_ohlcv_sentiment")
        record = {
            "id": job_id,
            "trigger": trigger,
            "sentiment_requested": sentiment,
            "ablate_sentiment_requested": bool(ablate_sentiment),
            "sentiment_capable": bool(sentiment_capable),
            "status": "running",
            "created_ts": now,
            "started_ts": now,
            "updated_ts": now,
            "errors": [],
            "primary_result": None,
            "sentiment_result": None,
        }
        # Record requested target/params for transparency
        # Bottom-only mode: force target to bottom
        eff_target = 'bottom'
        record["requested_target"] = eff_target
        record["requested_bottom_params"] = {
            "lookahead": int(bottom_lookahead or getattr(cfg, 'bottom_lookahead', 30) or 30),
            "drawdown": float(bottom_drawdown or getattr(cfg, 'bottom_drawdown', 0.005) or 0.005),
            "rebound": float(bottom_rebound or getattr(cfg, 'bottom_rebound', 0.003) or 0.003),
        }
        record["requested_mode"] = mode_used
        record["requested_store"] = store_flag
        record["requested_cv_splits"] = cv_splits_int
        record["requested_class_weight"] = class_weight_used
        record["requested_time_cv"] = time_cv_flag
        # capture requested horizons (if any)
        try:
            record["requested_horizons"] = list(horizons) if horizons else []
        except Exception:
            record["requested_horizons"] = []
        _training_jobs[job_id] = record
        _training_job_order.append(job_id)
        _cap_history()
        # Also create a persistent training_jobs row for UI history list
        db_job_id: Optional[int] = None
        try:
            repo = TrainingJobRepository()
            db_job_id = await repo.create_job(trigger=trigger)
            record["db_job_id"] = db_job_id
        except Exception as e:  # noqa: BLE001
            # Non-fatal: UI list may not reflect manual run if DB unavailable
            record["errors"].append(f"db_create_job_failed:{e}")

    async def _run_primary():
        res_status = "ok"
        start_ts = time.time()
        result: Optional[dict[str, Any]] = None
        promotion: Optional[dict[str, Any]] = None
        try:
            # Use configured default sample limit if available
            default_limit = int(limit_override) if (isinstance(limit_override, int) and limit_override > 0) else (getattr(cfg, 'auto_retrain_min_samples', 600) or 600)
            # Choose training path by target
            req_tgt = str(record.get("requested_target") or "bottom").lower()
            if hasattr(svc, 'run_training_bottom'):
                bp = record.get("requested_bottom_params") or {}
                result = await svc.run_training_bottom(
                    limit=int(default_limit),
                    lookahead=int(bp.get("lookahead", 30)),
                    drawdown=float(bp.get("drawdown", 0.005)),
                    rebound=float(bp.get("rebound", 0.003)),
                )  # type: ignore[attr-defined]
            else:
                # Fallback to generic training if bottom path not available
                result = await svc.run_training(
                    limit=int(default_limit),
                    mode=mode_used,
                    store=store_flag,
                    class_weight=class_weight_used,
                    cv_splits=cv_splits_int,
                    time_cv=time_cv_flag,
                )
            if isinstance(result, dict):
                record["primary_payload"] = result
                record["metrics"] = result.get("metrics")
                status_val = result.get("status")
                if status_val:
                    record["status"] = status_val
            if store_flag and isinstance(result, dict) and result.get("status") == "ok":
                try:
                    from backend.apps.training.auto_promotion import promote_if_better, PROMOTION_STATE, CFG as PROMO_CFG
                    prev_test_mode = getattr(PROMOTION_STATE, "test_mode", False)
                    prev_sample_growth = getattr(PROMO_CFG, "auto_promote_min_sample_growth", None)
                    if os.getenv("APP_ENV", "").lower() == "test":
                        PROMOTION_STATE.test_mode = True
                        try:
                            PROMO_CFG.auto_promote_min_sample_growth = 0.0  # type: ignore[attr-defined]
                        except Exception:
                            pass
                    try:
                        promotion = await promote_if_better(result.get("model_id"), result.get("metrics") or {})
                    finally:
                        PROMOTION_STATE.test_mode = prev_test_mode
                        if prev_sample_growth is not None:
                            try:
                                PROMO_CFG.auto_promote_min_sample_growth = prev_sample_growth  # type: ignore[attr-defined]
                            except Exception:
                                pass
                except Exception as promo_err:  # noqa: BLE001
                    promotion = {"promoted": False, "reason": "promotion_exception", "error": str(promo_err)}
            record["promotion"] = promotion
        except Exception as e:  # noqa: BLE001
            res_status = f"error:{e}"
            record["errors"].append(str(e))
        finally:
            elapsed = time.time() - start_ts
            # Persist outcome if we created a DB job row
            if record.get("db_job_id") is not None:
                repo2 = TrainingJobRepository()
                try:
                    if result and (result.get("status") == "ok" or result.get("status") == "success"):
                        artifact_path = result.get("artifact_path")
                        version = result.get("version") or str(result.get("model_version") or "")
                        metrics = result.get("metrics") or {}
                        model_id = result.get("model_id")
                        await repo2.mark_success(int(record["db_job_id"]), artifact_path, model_id, version, metrics, elapsed)
                    else:
                        # Treat common non-fatal outcomes as informational (not errors)
                        non_error_statuses = {
                            "insufficient_data",
                            "insufficient_labels",
                            "insufficient_features",
                            "insufficient_class_variation",
                            "insufficient_samples",
                            "insufficient_samples_postfilter",
                        }
                        status_str = (result.get("status") if isinstance(result, dict) else None) or ""
                        if status_str in non_error_statuses:
                            # Record a soft failure marker in metrics for visibility
                            soft_metrics = {"status": status_str, "duration_seconds": elapsed}
                            try:
                                await repo2.mark_success(int(record["db_job_id"]), artifact_path=None, model_id=None, version="n/a", metrics=soft_metrics, duration_seconds=elapsed)
                            except Exception:
                                await repo2.mark_error(int(record["db_job_id"]), f"train_insufficient:{status_str}", elapsed)
                        else:
                            err_msg = None
                            if result and result.get("status") and result.get("status") != "ok":
                                err_msg = f"train_failed:{result.get('status')}"
                            await repo2.mark_error(int(record["db_job_id"]), err_msg or (res_status if res_status != "ok" else "unknown_error"), elapsed)
                except Exception as pe:  # noqa: BLE001
                    record["errors"].append(f"db_mark_failed:{pe}")
        record["primary_result"] = res_status

    async def _run_sentiment():
        if not sentiment or not sentiment_capable:
            record["sentiment_result"] = None
            return
        res_status = "ok"
        payload = None
        try:
            default_limit = getattr(cfg, 'auto_retrain_min_samples', 600) or 600
            # If ablation requested, return detailed report payload
            try:
                do_ablate = bool(record.get("ablate_sentiment_requested", False))
            except Exception:
                do_ablate = False
            payload = await svc.run_training_ohlcv_sentiment(limit=int(default_limit), ablation=do_ablate)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            res_status = f"error:{e}"
            record["errors"].append(str(e))
        record["sentiment_result"] = res_status
        if payload is not None:
            record["sentiment_report"] = payload

    async def _run_horizons():
        hz = record.get("requested_horizons") or []
        if not hz:
            record["horizon_models"] = None
            return
        results: dict[str, str] = {}
        for h in hz:
            try:
                default_limit = getattr(cfg, 'auto_retrain_min_samples', 600) or 600
                r = await svc.run_training_for_horizon(limit=int(default_limit), horizon_label=str(h))
                results[str(h)] = r.get("status","unknown") if isinstance(r, dict) else str(r)
            except Exception as e:  # noqa: BLE001
                results[str(h)] = f"error:{e}"
        record["horizon_models"] = results

    async def _runner():
        try:
            await asyncio.gather(_run_primary(), _run_sentiment(), _run_horizons())
            # Evaluate aggregate status
            if record["primary_result"] and record["primary_result"].startswith("error"):
                record["status"] = "error"
            elif (sentiment and record["sentiment_result"] and str(record["sentiment_result"]).startswith("error")):
                record["status"] = "error"
            else:
                record["status"] = "success"
        finally:
            record["updated_ts"] = time.time()
            record["finished_ts"] = record["updated_ts"]
            # Release concurrency guard
            global _training_active
            _training_active = False

    if wait_flag:
        await _runner()
        return job_id, {
            "sentiment_capable": bool(sentiment_capable),
            "requested_horizons": record.get("requested_horizons"),
            "record": record,
        }

    asyncio.create_task(_runner())
    return job_id, {
        "sentiment_capable": bool(sentiment_capable),
        "requested_horizons": record.get("requested_horizons"),
        "record": None,
    }

@app.post("/api/training/run", dependencies=[Depends(require_api_key)])
async def api_training_run(request: Request, req: Optional[TrainingRunRequest] = None):
    """Manually trigger a training run (POST).

    Usage:
      - POST /api/training/run            (empty body OK)
      - POST /api/training/run {"trigger":"my_label"}
      - POST /api/training/run {"sentiment":true}

    Returns immediate acknowledgement; training runs asynchronously.
    422 발생 원인: 잘못된 JSON 형식. Body 없이 호출하려면 Content-Type 제거하거나 빈 body 허용됨.
    """
    data = req or TrainingRunRequest()
    truthy = {"1", "true", "t", "yes", "y", "on"}

    def _parse_bool(val: Any, default: Optional[bool]) -> bool:
        if val is None:
            return bool(default)
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in truthy

    def _parse_int(val: Any, default: Optional[int]) -> Optional[int]:
        if val is None:
            return default
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    qp = request.query_params if request is not None else None
    if qp:
        if "trigger" in qp and not data.trigger:
            data.trigger = qp.get("trigger")
        if "sentiment" in qp:
            data.sentiment = _parse_bool(qp.get("sentiment"), data.sentiment)
        if "force" in qp:
            data.force = _parse_bool(qp.get("force"), data.force)
        if "horizons" in qp and not data.horizons:
            raw = qp.get("horizons")
            if raw:
                data.horizons = [h.strip() for h in str(raw).split(",") if h.strip()]
        if "ablate_sentiment" in qp:
            data.ablate_sentiment = _parse_bool(qp.get("ablate_sentiment"), data.ablate_sentiment)
        if "target" in qp and not data.target:
            data.target = qp.get("target")
        if "bottom_lookahead" in qp:
            data.bottom_lookahead = _parse_int(qp.get("bottom_lookahead"), data.bottom_lookahead)
        if "bottom_drawdown" in qp:
            try:
                data.bottom_drawdown = float(qp.get("bottom_drawdown"))
            except (TypeError, ValueError):
                pass
        if "bottom_rebound" in qp:
            try:
                data.bottom_rebound = float(qp.get("bottom_rebound"))
            except (TypeError, ValueError):
                pass
        if "limit" in qp:
            data.limit = _parse_int(qp.get("limit"), data.limit)
        if "mode" in qp:
            data.mode = qp.get("mode") or data.mode
        if "store" in qp:
            data.store = _parse_bool(qp.get("store"), data.store)
        if "cv_splits" in qp:
            data.cv_splits = _parse_int(qp.get("cv_splits"), data.cv_splits or 0) or 0
        if "class_weight" in qp:
            data.class_weight = qp.get("class_weight") or data.class_weight
        if "time_cv" in qp:
            data.time_cv = _parse_bool(qp.get("time_cv"), data.time_cv)
        if "sync" in qp:
            data.sync = _parse_bool(qp.get("sync"), data.sync)
        if "wait" in qp:
            data.sync = _parse_bool(qp.get("wait"), data.sync)

    trigger = data.trigger or "manual_api"
    limit_override = _parse_int(data.limit, None)
    mode_used = str(data.mode or "baseline")
    store_flag = bool(data.store)
    cv_splits_val = _parse_int(data.cv_splits, 0) or 0
    class_weight_used = data.class_weight if data.class_weight is not None else None
    time_cv_flag = bool(data.time_cv)
    default_limit_cfg = int(getattr(cfg, 'auto_retrain_min_samples', 600) or 600)
    sync_flag = data.sync if data.sync is not None else bool(os.getenv("APP_ENV", "").lower() == "test" or os.getenv("TRAINING_FORCE_SYNC", "0").lower() in truthy)

    try:
        job_id, extra = await _launch_training_job(
            trigger=trigger,
            sentiment=data.sentiment,
            force=data.force,
            horizons=(data.horizons or None),
            ablate_sentiment=bool(data.ablate_sentiment),
            target=(data.target or None),
            bottom_lookahead=data.bottom_lookahead,
            bottom_drawdown=data.bottom_drawdown,
            bottom_rebound=data.bottom_rebound,
            limit_override=(int(limit_override) if limit_override is not None else None),
            mode=mode_used,
            store=store_flag,
            cv_splits=cv_splits_val,
            class_weight=class_weight_used,
            time_cv=time_cv_flag,
            wait_for_completion=sync_flag,
        )
        record = extra.get("record") if isinstance(extra, dict) else None
        if sync_flag and record:
            payload = {}
            if isinstance(record.get("primary_payload"), dict):
                payload = dict(record.get("primary_payload"))
            status_val = payload.get("status") or ("ok" if record.get("status") == "success" else record.get("status") or "ok")
            response = {
                **payload,
                "status": status_val,
                "job_id": job_id,
                "trigger": trigger,
                "sentiment_requested": data.sentiment,
                "ablate_sentiment_requested": bool(data.ablate_sentiment),
                "promotion": record.get("promotion"),
                "concurrency_force": data.force,
                # Bottom-only mode: always reflect bottom target in response
                "requested_target": "bottom",
                "requested_bottom_params": {
                    "lookahead": int(data.bottom_lookahead or getattr(cfg, 'bottom_lookahead', 30) or 30),
                    "drawdown": float(data.bottom_drawdown or getattr(cfg, 'bottom_drawdown', 0.005) or 0.005),
                    "rebound": float(data.bottom_rebound or getattr(cfg, 'bottom_rebound', 0.003) or 0.003),
                },
                "requested_limit": int(limit_override) if limit_override is not None else default_limit_cfg,
                "requested_mode": mode_used,
                "requested_store": store_flag,
                "requested_cv_splits": cv_splits_val,
                "requested_time_cv": time_cv_flag,
            }
            # Ensure metrics key present even if payload empty
            if "metrics" not in response and record.get("metrics"):
                response["metrics"] = record.get("metrics")
            if "promotion" not in response:
                response["promotion"] = record.get("promotion")
            return response

        extra_out = {k: v for k, v in (extra.items() if isinstance(extra, dict) else []) if k != "record"}
        return {
            "status": "started",
            "job_id": job_id,
            "trigger": trigger,
            "sentiment_requested": data.sentiment,
            "ablate_sentiment_requested": bool(data.ablate_sentiment),
            **extra_out,
            "concurrency_force": data.force,
            # Bottom-only mode: always reflect bottom target in response
            "requested_target": "bottom",
            "requested_bottom_params": {
                "lookahead": int(data.bottom_lookahead or getattr(cfg, 'bottom_lookahead', 30) or 30),
                "drawdown": float(data.bottom_drawdown or getattr(cfg, 'bottom_drawdown', 0.005) or 0.005),
                "rebound": float(data.bottom_rebound or getattr(cfg, 'bottom_rebound', 0.003) or 0.003),
            },
            "requested_limit": int(limit_override) if limit_override is not None else default_limit_cfg,
            "requested_mode": mode_used,
            "requested_store": store_flag,
            "requested_cv_splits": cv_splits_val,
            "requested_time_cv": time_cv_flag,
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"training_start_failed:{e}")

@app.get("/api/training/run", dependencies=[Depends(require_api_key)])
async def api_training_run_get(
    trigger: Optional[str] = None,
    sentiment: bool = False,
    ablate_sentiment: bool = False,
    target: Optional[str] = None,
    bottom_lookahead: Optional[int] = None,
    bottom_drawdown: Optional[float] = None,
    bottom_rebound: Optional[float] = None,
    limit: Optional[int] = None,
):
    """GET convenience alias for manual training run.

    Examples:
      - GET /api/training/run
      - GET /api/training/run?trigger=nightly
      - GET /api/training/run?sentiment=true
    """
    trig = trigger or "manual_api_get"
    try:
        job_id, extra = await _launch_training_job(
            trigger=trig,
            sentiment=sentiment,
            force=False,
            horizons=None,
            ablate_sentiment=ablate_sentiment,
            target=(target or None),
            bottom_lookahead=bottom_lookahead,
            bottom_drawdown=bottom_drawdown,
            bottom_rebound=bottom_rebound,
            limit_override=(int(limit) if (isinstance(limit, int) and limit is not None) else None),
        )
        return {
            "status": "started",
            "job_id": job_id,
            "trigger": trig,
            "sentiment_requested": sentiment,
            "ablate_sentiment_requested": ablate_sentiment,
            **extra,
            "method": "GET",
            "requested_limit": (int(limit) if (isinstance(limit, int) and limit is not None) else int(getattr(cfg, 'auto_retrain_min_samples', 600) or 600)),
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"training_start_failed:{e}")

@app.get("/api/training/status", dependencies=[Depends(require_api_key)])
async def api_training_status(id: Optional[str] = None):
    """Fetch status for a specific job id, or list all active+recent jobs.

    Query params:
      id: (optional) job id. If omitted returns list sorted newest->oldest.
    """
    if id:
        job = _training_jobs.get(id)
        if not job:
            raise HTTPException(status_code=404, detail="job_not_found")
        return {"status": "ok", "job": job}
    # return brief list
    items = []
    for jid in reversed(_training_job_order):
        j = _training_jobs.get(jid)
        if j:
            items.append({
                "id": j["id"],
                "trigger": j["trigger"],
                "status": j["status"],
                "created_ts": j.get("created_ts"),
                "finished_ts": j.get("finished_ts"),
                "errors": j.get("errors"),
            })
        if len(items) >= 50:
            break
    return {"status": "ok", "jobs": items}
# --- Bottom label preview (operator helper) ---
@app.get("/api/training/bottom/preview", dependencies=[Depends(require_api_key)])
async def training_bottom_preview(limit: int = 1000, lookahead: Optional[int] = None, drawdown: Optional[float] = None, rebound: Optional[float] = None):
    """Preview bottom-label dataset counts without training.

    Returns: status, have, required, pos_ratio, params actually used.
    """
    # Load fresh config each call so .env/.env.private updates apply without restart
    cfg_local = load_config()
    svc = _training_service()
    rows = await svc.load_recent_features(limit=max(100, min(20000, int(limit))))
    if len(rows) < 200:
        return {"status": "insufficient_data", "required": 200, "have": len(rows)}
    # Fetch OHLCV with cap like training
    try:
        from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as _fetch_kline_recent
        cap = int(getattr(cfg_local, 'bottom_ohlcv_fetch_cap', 5000)) if hasattr(cfg_local, 'bottom_ohlcv_fetch_cap') else 5000
        # Allow larger previews locally to avoid insufficient labels while guarding upper bound
        cap = max(300, min(cap, 100000))
        k_rows = await _fetch_kline_recent(cfg_local.symbol, cfg_local.kline_interval, limit=min(max(len(rows) + 64, 300), cap))
        candles = list(reversed(k_rows))
    except Exception:
        candles = []
    if not candles:
        return {"status": "no_ohlcv"}
    L = int(lookahead if lookahead is not None else getattr(cfg_local, 'bottom_lookahead', 30))
    D = float(drawdown if drawdown is not None else getattr(cfg_local, 'bottom_drawdown', 0.005))
    R = float(rebound if rebound is not None else getattr(cfg_local, 'bottom_rebound', 0.003))
    idx_by_ct: dict[int,int] = {}
    for i, c in enumerate(candles):
        ct = c.get("close_time")
        if isinstance(ct, int):
            idx_by_ct[ct] = i
    def _label_at_ct(ct: int) -> Optional[int]:
        j = idx_by_ct.get(ct)
        if j is None:
            return None
        end = min(len(candles) - 1, j + L)
        if end <= j:
            return None
        try:
            p0 = float(candles[j]["close"])
        except Exception:
            return None
        min_low = None; min_idx = None
        for t in range(j + 1, end + 1):
            try:
                lo = float(candles[t]["low"])  # drawdown check
            except Exception:
                continue
            if min_low is None or lo < min_low:
                min_low = lo; min_idx = t
        if min_low is None or min_idx is None:
            return None
        try:
            drop = (min_low - p0) / p0
        except Exception:
            return None
        if drop > (-abs(D)):
            return 0
        max_high = None
        for t in range(min_idx, end + 1):
            try:
                hi = float(candles[t]["high"])  # rebound check
            except Exception:
                continue
            if max_high is None or hi > max_high:
                max_high = hi
        if max_high is None:
            return 0
        try:
            rb = (max_high - min_low) / min_low
        except Exception:
            return 0
        return 1 if rb >= abs(R) else 0
    feat_names = ["ret_1","ret_5","ret_10","rsi_14","rolling_vol_20","ma_20","ma_50"]
    have = 0
    y_list: list[int] = []
    for r in rows:
        if any(r.get(f) is None for f in feat_names if f in r):
            continue
        ct = r.get("close_time")
        if not isinstance(ct, int):
            continue
        yv = _label_at_ct(ct)
        if yv is None:
            continue
        have += 1
        y_list.append(int(yv))
    try:
        required = int(getattr(cfg_local, 'bottom_min_labels', 150))
    except Exception:
        required = 150
    pos_ratio = (float(sum(y_list))/len(y_list) if y_list else None)
    # Status logic: align with training service semantics
    if have < required:
        status_val = "insufficient_labels"
    elif len(set(y_list)) < 2:
        status_val = "insufficient_class_variation"
    else:
        status_val = "ok"
    return {
        "status": status_val,
        "have": have,
        "required": required,
        "pos_ratio": pos_ratio,
        "params": {"lookahead": L, "drawdown": D, "rebound": R},
        "limit": len(rows),
        "candles_used": len(candles),
    }

@app.get("/api/training/history", dependencies=[Depends(require_api_key)])
async def api_training_history(limit: int = 20):
    """Return recent training jobs from DB (newest first) and overlay in-memory statuses.

    Includes version/artifact_path and flattens key metrics (auc, ece, accuracy, brier, samples, val_samples, features).
    """
    limit = max(1, min(200, limit))
    repo = TrainingJobRepository()
    rows = await repo.fetch_recent(limit=limit)
    # Build overlay map from in-memory jobs keyed by db_job_id
    in_mem_by_dbid: dict[int, dict] = {}
    try:
        for jid in _training_job_order:
            j = _training_jobs.get(jid)
            if not j:
                continue
            dbid = j.get("db_job_id")
            if isinstance(dbid, int):
                in_mem_by_dbid[dbid] = j
    except Exception:
        pass
    out: list[dict] = []
    registry_repo_local = registry_repo
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        # Parse metrics JSON if string
        try:
            mval = d.get("metrics")
            if isinstance(mval, str):
                import json
                try:
                    d["metrics"] = json.loads(mval)
                except Exception:
                    pass
        except Exception:
            pass
        # Flatten common metrics
        try:
            m = d.get("metrics")
            if isinstance(m, dict):
                def _set_if_absent(key: str, val):
                    if key not in d and val is not None:
                        d[key] = val
                _set_if_absent("auc", m.get("auc"))
                _set_if_absent("accuracy", m.get("accuracy"))
                _set_if_absent("ece", m.get("ece"))
                _set_if_absent("mce", m.get("mce"))
                _set_if_absent("brier", m.get("brier"))
                _set_if_absent("samples", m.get("samples"))
                _set_if_absent("val_samples", m.get("val_samples"))
                feats = m.get("features") or m.get("feature_set")
                if feats is not None and "features" not in d:
                    d["features"] = feats
        except Exception:
            pass
        # Overlay with in-memory job status/results
        try:
            dbid = d.get("id")
            if isinstance(dbid, int) and dbid in in_mem_by_dbid:
                mem = in_mem_by_dbid[dbid]
                mem_status = mem.get("status")
                if mem_status in ("success", "error"):
                    d["status"] = mem_status
                d["primary_result"] = mem.get("primary_result", d.get("primary_result"))
                d["sentiment_result"] = mem.get("sentiment_result", d.get("sentiment_result"))
        except Exception:
            pass
        # If metrics missing but model_id present, fetch from registry; also fill version/artifact_path
        if (not d.get("metrics")) and d.get("model_id"):
            try:
                reg = await registry_repo_local.fetch_by_id(int(d["model_id"]))
                if reg and reg.get("metrics"):
                    d["metrics"] = reg.get("metrics")
                    m = d["metrics"]
                    if isinstance(m, dict):
                        d.setdefault("auc", m.get("auc"))
                        d.setdefault("accuracy", m.get("accuracy"))
                        d.setdefault("ece", m.get("ece"))
                        d.setdefault("mce", m.get("mce"))
                        d.setdefault("brier", m.get("brier"))
                        d.setdefault("samples", m.get("samples"))
                        d.setdefault("val_samples", m.get("val_samples"))
                        feats = m.get("features") or m.get("feature_set")
                        if feats is not None:
                            d.setdefault("features", feats)
                if reg and not d.get("version") and reg.get("version"):
                    d["version"] = reg.get("version")
                if reg and not d.get("artifact_path") and reg.get("artifact_path"):
                    d["artifact_path"] = reg.get("artifact_path")
            except Exception:
                pass
        out.append(d)
    # Also include currently running in-memory jobs that may not have DB rows yet
    try:
        seen_db_ids = {int(it.get("id")) for it in out if isinstance(it.get("id"),(int,))}
        sid = -10_000
        for jid in reversed(_training_job_order):
            j = _training_jobs.get(jid)
            if not j or j.get("status") != "running":
                continue
            dbid = j.get("db_job_id")
            if isinstance(dbid, int) and dbid in seen_db_ids:
                continue
            out.insert(0, {
                "id": (dbid if isinstance(dbid, int) else sid),
                "status": "running",
                "trigger": j.get("trigger") or "manual",
                "created_ts": j.get("created_ts"),
                "finished_ts": None,
                "duration_seconds": None,
            })
            if not isinstance(dbid, int):
                sid -= 1
            if len(out) >= limit:
                break
    except Exception:
        pass
    return {"status": "ok", "jobs": out, "count": len(out)}

# -------------------- News Service Helpers & Endpoints --------------------
async def news_loop(service: NewsService, interval: int = 90):  # type: ignore
    aggregate_label = "all"
    while True:
        try:
            inserted = await service.poll_once()
            NEWS_FETCH_RUNS.labels(source=aggregate_label).inc()
            if inserted:
                NEWS_ARTICLES_INGESTED.labels(source=aggregate_label).inc(inserted)
            st = service.status()
            lag = st.get("ingestion_lag_seconds")
            if lag is not None:
                NEWS_INGESTION_LAG.labels(source=aggregate_label).set(lag)
            # sentiment aggregation (window 60m)
            try:
                stats = await service.repo.sentiment_window_stats(minutes=60, symbol=service.symbol)
                avg = stats.get("avg")
                count = stats.get("count")
                if avg is not None:
                    NEWS_SENTIMENT_AVG.labels(source=aggregate_label, window="60m").set(avg)
                NEWS_SENTIMENT_SAMPLES.labels(source=aggregate_label, window="60m").set(count or 0)
            except Exception as se:
                logger.debug("news_sentiment_metric_update_failed err=%s", se)
        except Exception as e:
            try:
                NEWS_FETCH_ERRORS.labels(source=aggregate_label).inc()
            except Exception:
                pass
            logger.warning("news_loop_iteration_failed err=%s", e)
        await asyncio.sleep(interval)

# --- News recent endpoint metrics (added) ---
try:  # pragma: no cover - defensive import
    from prometheus_client import Counter, Histogram  # type: ignore
    NEWS_RECENT_REQUESTS = Counter(
        "news_recent_requests_total", "Number of /api/news/recent calls", ["delta", "summary_only"]
    )
    NEWS_RECENT_ROWS = Counter(
        "news_recent_rows_returned_total", "Rows returned by /api/news/recent", ["delta"]
    )
    NEWS_RECENT_PAYLOAD = Counter(
        "news_recent_payload_bytes_total", "Approx payload bytes for /api/news/recent", ["delta"]
    )
    NEWS_RECENT_LATENCY = Histogram(
        "news_recent_latency_seconds", "Latency of /api/news/recent"
    )
    NEWS_RECENT_NOT_MODIFIED = Counter(
        "news_recent_not_modified_total", "304 not modified responses for /api/news/recent"
    )
    NEWS_RECENT_RATE_LIMITED = Counter(
        "news_recent_rate_limited_total", "Rate limited responses for /api/news/recent", ["mode"]
    )
    from prometheus_client import Gauge as _GaugeNM
    NEWS_RECENT_NOT_MODIFIED_RATIO = _GaugeNM(
        "news_recent_not_modified_ratio",
        "Ratio (0-1) 304 responses / total recent requests (process lifetime approximation)",
    )
    NEWS_RECENT_DELTA_USAGE_RATIO = _GaugeNM(
        "news_recent_delta_usage_ratio",
        "Ratio (0-1) delta-mode calls / total recent requests (process lifetime approximation)",
    )
    NEWS_RECENT_PAYLOAD_DELTA_AVG = _GaugeNM(
        "news_recent_payload_delta_avg_bytes",
        "Running average response size (bytes) for delta requests",
    )
    NEWS_RECENT_PAYLOAD_FULL_AVG = _GaugeNM(
        "news_recent_payload_full_avg_bytes",
        "Running average response size (bytes) for full requests",
    )
    NEWS_RECENT_DELTA_PAYLOAD_SAVING_RATIO = _GaugeNM(
        "news_recent_delta_payload_saving_ratio",
        "1 - (delta_avg / full_avg) when both >0 (process lifetime approximation)",
    )
    NEWS_RECENT_DELTA_EMPTY_STREAK = _GaugeNM(
        "news_recent_delta_empty_streak",
        "Consecutive delta calls (since_ts provided) returning 0 new items",
    )
    NEWS_RECENT_SECONDS_SINCE_NEW = _GaugeNM(
        "news_recent_seconds_since_new_article",
        "Approx seconds since last non-empty news response (full or delta)",
    )
except Exception:  # pragma: no cover
    NEWS_RECENT_REQUESTS = None
    NEWS_RECENT_ROWS = None
    NEWS_RECENT_PAYLOAD = None
    NEWS_RECENT_LATENCY = None
    NEWS_RECENT_NOT_MODIFIED = None
    NEWS_RECENT_RATE_LIMITED = None
    NEWS_RECENT_NOT_MODIFIED_RATIO = None
    NEWS_RECENT_DELTA_USAGE_RATIO = None
    NEWS_RECENT_PAYLOAD_DELTA_AVG = None
    NEWS_RECENT_PAYLOAD_FULL_AVG = None
    NEWS_RECENT_DELTA_PAYLOAD_SAVING_RATIO = None
    NEWS_RECENT_DELTA_EMPTY_STREAK = None
    NEWS_RECENT_SECONDS_SINCE_NEW = None

@app.get("/api/news/recent")
async def news_recent(
    limit: int = 50,
    symbol: Optional[str] = None,
    since_ts: Optional[int] = None,
    summary_only: Optional[int] = 1,
    _auth: bool = Depends(require_api_key),
    request: Request = None,
):
    """최근 뉴스 기사 목록 (delta / summary 지원).

    Query params:
      - limit: 최대 개수 (기본 50, 최대 200 권장)
      - symbol: 특정 심볼 필터
      - since_ts: 해당 epoch(ms|s) 시각 이후(newer or equal) 기사만 반환 → delta fetch 용도
      - summary_only: 1(default) 이면 body 제외(경량화), 0 이면 body 포함

    Response:
      {
        status: ok,
        delta: bool,              # since_ts 사용 여부
        limit: int,
        count: int,
        items: [...],             # 기사 배열 (body 제외 혹은 포함)
        summary_only: bool,
        next_cursor: int|null,    # 다음 페이지 커서 후보 (마지막 기사 published_ts)
        latest_ts: int|null       # 이번 응답에서 가장 최신 published_ts
      }
    """
    if limit <= 0:
        raise HTTPException(status_code=400, detail="invalid_limit")
    if limit > 200:
        raise HTTPException(status_code=416, detail="limit_too_large")

    # Normalize since_ts seconds/ms heuristic: if very small (10 digits) assume seconds, convert to ms base seconds since our stored published_ts appears to be seconds
    norm_since: Optional[int] = None
    if since_ts is not None:
        # Published_ts in repository currently stored as seconds (int). Accept both sec or ms.
        if since_ts > 10_000_000_000:  # > year 2286 if seconds, so treat as ms
            norm_since = int(since_ts / 1000)
        else:
            norm_since = since_ts

    summary_flag = summary_only is None or int(summary_only) == 1
    svc: NewsService = getattr(app.state, 'news_service')
    # -------- Rate Limiting (delta vs full) --------
    # 정책: full(fetch without since_ts) 10 req / 60s, delta(fetch with since_ts) 60 req / 60s 기본
    # 환경변수 기반 override 지원 (옵션)
    _v = os.getenv('NEWS_FULL_RATE_LIMIT', '10')
    if isinstance(_v, str) and '#' in _v:
        _v = _v.split('#', 1)[0].strip()
    full_limit = int(float(_v))
    _v = os.getenv('NEWS_DELTA_RATE_LIMIT', '60')
    if isinstance(_v, str) and '#' in _v:
        _v = _v.split('#', 1)[0].strip()
    delta_limit = int(float(_v))
    window_sec = 60
    # 간단 토큰버킷: 키 = api_key + mode(full/delta)
    if not hasattr(app.state, 'news_rate_buckets'):
        app.state.news_rate_buckets = {}
    buckets = app.state.news_rate_buckets
    api_key = request.headers.get('X-API-Key') if request else 'anon'
    mode = 'delta' if since_ts is not None else 'full'
    bucket_key = f"{api_key}:{mode}"
    now_t = time.time()
    b = buckets.get(bucket_key)
    capacity = delta_limit if mode == 'delta' else full_limit
    refill_rate = capacity / window_sec  # tokens per second
    if not b:
        b = { 'tokens': float(capacity), 'ts': now_t }
    else:
        # refill
        elapsed = now_t - b['ts']
        b['tokens'] = min(float(capacity), b['tokens'] + elapsed * refill_rate)
        b['ts'] = now_t
    if b['tokens'] < 1.0:
        # Rate limited
        retry_after =  int(max(1, (1.0 - b['tokens']) / refill_rate))
        try:
            if 'NEWS_RECENT_RATE_LIMITED' in globals() and NEWS_RECENT_RATE_LIMITED:
                NEWS_RECENT_RATE_LIMITED.labels(mode=mode).inc()
        except Exception:
            pass
        raise HTTPException(status_code=429, detail=f"rate_limited:{mode}")
    else:
        b['tokens'] -= 1.0
    buckets[bucket_key] = b
    # ---- Simple in-memory LRU cache (query signature -> response) ----
    if not hasattr(app.state, 'news_recent_cache'):
        app.state.news_recent_cache = {
            'store': {},   # key -> (etag, resp, ts)
            'order': []    # lru list of keys
        }
    cache = app.state.news_recent_cache
    def _cache_key():
        return f"v1|{limit}|{symbol}|{norm_since}|{summary_flag}"
    key = _cache_key()
    # ETag revalidation
    incoming_etag = None
    try:
        if request is not None:
            incoming_etag = request.headers.get("if-none-match") or request.headers.get("If-None-Match")
    except Exception:
        incoming_etag = None
    # Access raw headers via request context is cleaner; adapt by adding Request param if necessary for further evolution
    # (We keep minimal change: we won't check If-None-Match without Request object for now.)
    start_t = time.perf_counter()
    cached = cache['store'].get(key)
    if cached:
        etag_cached, cached_resp, ts_cached = cached
        if incoming_etag and etag_cached and incoming_etag.strip('"') == etag_cached:
            # 304 Not Modified
            from fastapi import Response
            try:
                if 'NEWS_RECENT_NOT_MODIFIED' in globals() and NEWS_RECENT_NOT_MODIFIED:
                    NEWS_RECENT_NOT_MODIFIED.inc()
                # rolling counters for ratio gauges
                app.state._news_recent_total = getattr(app.state, '_news_recent_total', 0) + 1
                app.state._news_recent_not_modified = getattr(app.state, '_news_recent_not_modified', 0) + 1
                # delta usage counter: we know whether since_ts used
                if norm_since is not None:
                    app.state._news_recent_delta_calls = getattr(app.state, '_news_recent_delta_calls', 0) + 1
                if NEWS_RECENT_NOT_MODIFIED_RATIO:
                    total_loc = app.state._news_recent_total
                    nm_loc = app.state._news_recent_not_modified
                    if total_loc > 0:
                        NEWS_RECENT_NOT_MODIFIED_RATIO.set(nm_loc / total_loc)
                if NEWS_RECENT_DELTA_USAGE_RATIO:
                    d_calls = getattr(app.state, '_news_recent_delta_calls', 0)
                    total_loc = app.state._news_recent_total
                    if total_loc > 0:
                        NEWS_RECENT_DELTA_USAGE_RATIO.set(d_calls / total_loc)
            except Exception:
                pass
            return Response(status_code=304)
    rows = []
    if not cached:
        rows = await svc.repo.fetch_recent(limit=limit, symbol=symbol, since_ts=norm_since, summary_only=summary_flag)
    # Legacy synthetic feed fully removed; no filtering necessary.
    else:
        # We'll still need rows for summary injection check if summary_flag, but cached resp already has it
        pass

    # Synthetic summary field (lightweight) if summary_only 모드
    if summary_flag:
        for r in rows:
            try:
                title = r.get("title") or ""
                snippet = title[:80]
                r["summary"] = snippet + ("…" if len(title) > 80 else "")
            except Exception:
                pass

    latest_ts = rows[0]["published_ts"] if rows else (cached_resp.get("latest_ts") if cached else None)
    if latest_ts is None:
        latest_ts = int(time.time())
    next_cursor = rows[-1]["published_ts"] if rows else (cached_resp.get("next_cursor") if cached else None)
    delta_flag = bool(norm_since is not None)
    if not cached:
        resp = {
            "status": "ok",
            "delta": delta_flag,
            "limit": limit,
            "count": len(rows),
            "items": rows,
            "summary_only": summary_flag,
            "latest_ts": latest_ts,
            "next_cursor": next_cursor,
        }
        # Compute ETag from ids + latest_ts
        try:
            import hashlib, json as _json
            id_part = ','.join(str(i.get('id')) for i in rows[:50])  # cap for safety
            sig_raw = f"{latest_ts}|{next_cursor}|{delta_flag}|{summary_flag}|{id_part}"
            etag_val = hashlib.sha256(sig_raw.encode()).hexdigest()[:16]
            resp['etag'] = etag_val
            cache['store'][key] = (etag_val, resp, time.time())
            cache['order'].append(key)
            # LRU trim
            if len(cache['order']) > 128:
                drop = cache['order'][:-128]
                for k in drop:
                    cache['store'].pop(k, None)
                cache['order'] = cache['order'][-128:]
        except Exception:
            pass
    else:
        resp = cached_resp
    # Metrics record
    try:
        if NEWS_RECENT_REQUESTS:
            NEWS_RECENT_REQUESTS.labels(delta=str(delta_flag).lower(), summary_only=str(summary_flag).lower()).inc()
        if NEWS_RECENT_ROWS:
            NEWS_RECENT_ROWS.labels(delta=str(delta_flag).lower()).inc(len(rows))
        if NEWS_RECENT_LATENCY:
            NEWS_RECENT_LATENCY.observe(time.perf_counter() - start_t)
        if NEWS_RECENT_PAYLOAD:
            import json
            payload_len = len(json.dumps(resp, default=str))
            NEWS_RECENT_PAYLOAD.labels(delta=str(delta_flag).lower()).inc(payload_len)
            # update running averages
            if delta_flag:
                app.state._pl_delta_sum = getattr(app.state, '_pl_delta_sum', 0) + payload_len
                app.state._pl_delta_cnt = getattr(app.state, '_pl_delta_cnt', 0) + 1
            else:
                app.state._pl_full_sum = getattr(app.state, '_pl_full_sum', 0) + payload_len
                app.state._pl_full_cnt = getattr(app.state, '_pl_full_cnt', 0) + 1
            try:
                if NEWS_RECENT_PAYLOAD_DELTA_AVG:
                    if getattr(app.state, '_pl_delta_cnt', 0) > 0:
                        NEWS_RECENT_PAYLOAD_DELTA_AVG.set(getattr(app.state, '_pl_delta_sum', 0) / getattr(app.state, '_pl_delta_cnt', 1))
                if NEWS_RECENT_PAYLOAD_FULL_AVG:
                    if getattr(app.state, '_pl_full_cnt', 0) > 0:
                        NEWS_RECENT_PAYLOAD_FULL_AVG.set(getattr(app.state, '_pl_full_sum', 0) / getattr(app.state, '_pl_full_cnt', 1))
                if NEWS_RECENT_DELTA_PAYLOAD_SAVING_RATIO:
                    d_cnt = getattr(app.state, '_pl_delta_cnt', 0)
                    f_cnt = getattr(app.state, '_pl_full_cnt', 0)
                    if d_cnt > 0 and f_cnt > 0:
                        d_avg = getattr(app.state, '_pl_delta_sum', 0) / d_cnt
                        f_avg = getattr(app.state, '_pl_full_sum', 0) / f_cnt
                        if f_avg > 0:
                            NEWS_RECENT_DELTA_PAYLOAD_SAVING_RATIO.set(max(0.0, min(1.0, 1 - (d_avg / f_avg))))
            except Exception:
                pass
        # ratio rolling counters update
        app.state._news_recent_total = getattr(app.state, '_news_recent_total', 0) + 1
        if delta_flag:
            app.state._news_recent_delta_calls = getattr(app.state, '_news_recent_delta_calls', 0) + 1
        if NEWS_RECENT_DELTA_USAGE_RATIO:
            total_loc = app.state._news_recent_total
            d_calls = getattr(app.state, '_news_recent_delta_calls', 0)
            if total_loc > 0:
                NEWS_RECENT_DELTA_USAGE_RATIO.set(d_calls / total_loc)
        if NEWS_RECENT_NOT_MODIFIED_RATIO:
            nm_loc = getattr(app.state, '_news_recent_not_modified', 0)
            total_loc = app.state._news_recent_total
            if total_loc > 0:
                NEWS_RECENT_NOT_MODIFIED_RATIO.set(nm_loc / total_loc)
        # freshness gauges
        now_sec = int(time.time())
        if delta_flag:
            if len(rows) == 0:
                app.state._delta_empty_streak = getattr(app.state, '_delta_empty_streak', 0) + 1
            else:
                app.state._delta_empty_streak = 0
        if len(rows) > 0:
            app.state._last_news_nonempty_ts = now_sec
        if NEWS_RECENT_DELTA_EMPTY_STREAK:
            try: NEWS_RECENT_DELTA_EMPTY_STREAK.set(getattr(app.state, '_delta_empty_streak', 0))
            except Exception: pass
        if NEWS_RECENT_SECONDS_SINCE_NEW:
            try:
                last_ts = getattr(app.state, '_last_news_nonempty_ts', None)
                if last_ts:
                    NEWS_RECENT_SECONDS_SINCE_NEW.set(max(0, now_sec - last_ts))
            except Exception:
                pass
    except Exception:
        pass
    return resp

@app.get("/api/news/article/{article_id}")
async def news_article(article_id: int, _auth: bool = Depends(require_api_key)):
    svc: NewsService = getattr(app.state, 'news_service')
    try:
        row = await svc.repo.fetch_by_id(article_id)  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"fetch_failed:{e}")
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    return {"status": "ok", "article": row}

@app.get("/api/news/status")
async def news_status(_auth: bool = Depends(require_api_key)):
    svc: NewsService = getattr(app.state, 'news_service')
    st = svc.status()
    st["fast_startup_skipped"] = 'news_ingestion' in getattr(app.state, 'skipped_components', [])
    st["poll_interval"] = getattr(app.state, 'news_poll_interval', None)
    st["active_sources"] = st.get("sources")
    st["status"] = "ok"
    return st

@app.get("/api/news/debug")
async def news_debug(verbose: int = 0, _auth: bool = Depends(require_api_key)):
    """수집 디버그: 기본 상태 + verbose=1 시 상세 fetch 메타/오류."""
    svc: NewsService = getattr(app.state, 'news_service')
    base = {
        "status": "ok",
        "last_poll_ts": svc.last_poll_ts,
        "last_ingested_ts": svc.last_ingested_ts,
        "running": svc.running,
        "fetch_runs": svc.fetch_runs,
        "articles_ingested": svc.articles_ingested,
        "sources": svc.sources,
        "disabled_sources": sorted(list(svc.disabled_sources)),
        "stall_threshold": getattr(svc, '_stall_threshold', None),
        "backoff": { s: max(0.0, getattr(svc, '_src_next_allowed', {}).get(s,0)-time.time()) for s in svc.sources },
        "last_health_scores": getattr(svc, '_last_health_scores', {}),
        "dedup_last_ratio": getattr(svc, '_dedup_last_ratio', {}),
        "dedup_low_ratio_streak": getattr(svc, '_dedup_low_ratio_streak', {}),
    }

@app.get("/ready")
async def readiness():
    """Kubernetes/compose readiness probe.

    Returns 200 only after the first news poll has completed (last_poll_ts set),
    and DB pool is available. This helps avoid serving empty/cached UI before
    initial ingestion.
    """
    pool = await init_pool()
    if pool is None:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "db_pool_unavailable"})
    try:
        svc: NewsService = getattr(app.state, 'news_service')  # type: ignore
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "service_missing"})
    if not getattr(svc, 'last_poll_ts', None):
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "no_poll_yet"})
    return {"status": "ready"}
    if verbose:
        base["verbose"] = {
            "last_error": getattr(svc, '_dbg_last_error', {}),
            "last_error_ts": getattr(svc, '_dbg_last_error_ts', {}),
            "last_success_ts": getattr(svc, '_dbg_last_success_ts', {}),
            "last_fetch_latency": getattr(svc, '_dbg_last_fetch_latency', {}),
            "last_attempt_ts": getattr(svc, '_dbg_attempt_ts', {}),
            "error_streak": getattr(svc, '_dbg_err_streak', {}),
            "bootstrap_running": getattr(svc, '_bootstrap_running', False),
            "bootstrap_started_ts": getattr(svc, '_bootstrap_started_ts', None),
            "bootstrap_finished_ts": getattr(svc, '_bootstrap_finished_ts', None),
        }
    return base

@app.post("/api/news/refresh")
async def news_refresh(_auth: bool = Depends(require_api_key)):
    svc: NewsService = getattr(app.state, 'news_service')
    inserted = await svc.poll_once()
    return {"status": "ok", "inserted": inserted}

@app.get("/api/news/feeds")
async def news_feeds(_auth: bool = Depends(require_api_key)):
    svc: NewsService = getattr(app.state, 'news_service')
    return {"status": "ok", "sources": svc.sources, "disabled": sorted(list(svc.disabled_sources))}

@app.get("/api/news/feeds/health")
async def news_feeds_health(_auth: bool = Depends(require_api_key)):
    svc: NewsService = getattr(app.state, 'news_service')
    return {"status": "ok", "feeds": svc.health_snapshot()}

@app.get("/api/news/feeds/dedup_history")
async def news_feeds_dedup_history(_auth: bool = Depends(require_api_key)):
    svc: NewsService = getattr(app.state, 'news_service')
    return {"status": "ok", "history": svc.dedup_history_snapshot()}

class FeedToggleRequest(BaseModel):
    source: str

class FeedPurgeRequest(BaseModel):
    source: str

class NewsBootstrapRequest(BaseModel):
    rounds: int = 3
    sleep_seconds: float = 5.0
    recent_days: int = 7
    min_total: Optional[int] = None
    per_round_backfill_days: Optional[int] = None

@app.post("/api/news/bootstrap")
async def news_bootstrap(req: NewsBootstrapRequest, _auth: bool = Depends(require_api_key)):
    svc: NewsService = getattr(app.state, 'news_service')
    try:
        return await svc.bootstrap_backfill(
            rounds=req.rounds,
            sleep_seconds=req.sleep_seconds,
            recent_days=req.recent_days,
            min_total=req.min_total,
            per_round_backfill_days=req.per_round_backfill_days,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"bootstrap_failed:{e}")

@app.get("/api/news/schema/status")
async def news_schema_status(_auth: bool = Depends(require_api_key)):
    repo = NewsRepository()
    exists = False
    count_val = None
    try:
        exists = await repo.table_exists()  # type: ignore
        if exists:
            # count rows
            pool = await init_pool()
            if pool is not None:
                async with pool.acquire() as conn:  # type: ignore
                    try:
                        row = await conn.fetchrow("SELECT count(*) AS c FROM news_articles")
                        if row:
                            count_val = int(row['c'])
                    except Exception:
                        pass
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e), "exists": exists, "count": count_val}
    return {"status": "ok", "exists": exists, "count": count_val}

@app.post("/api/news/schema/ensure")
async def news_schema_ensure(_auth: bool = Depends(require_api_key)):
    repo = NewsRepository()
    try:
        await repo.ensure_schema()  # type: ignore
        exists = await repo.table_exists()  # type: ignore
        return {"status": "ok", "ensured": True, "exists": exists}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"ensure_failed:{e}")

@app.post("/api/admin/schema/ensure")
async def admin_schema_ensure(_auth: bool = Depends(require_api_key)):
    """Ensure ALL application tables exist (idempotent).

    Returns list of tables attempted (order of creation). Safe to call multiple times.
    """
    try:
        tables = await ensure_all_schema()
        return {"status": "ok", "tables": tables}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"ensure_all_failed:{e}")

@app.post("/api/news/feeds/purge")
async def news_feed_purge(req: FeedPurgeRequest, _auth: bool = Depends(require_api_key)):
    """Delete all articles for a given source."""
    svc: NewsService = getattr(app.state, 'news_service')
    deleted = await svc.repo.delete_by_source(req.source)  # type: ignore
    return {"status": "ok", "deleted": deleted, "source": req.source}

@app.post("/api/news/feeds/disable")
async def news_feed_disable(body: FeedToggleRequest, _auth: bool = Depends(require_api_key)):
    svc: NewsService = getattr(app.state, 'news_service')
    ok = svc.disable_source(body.source)
    if not ok:
        raise HTTPException(status_code=404, detail="source_not_found")
    return {"status": "ok", "disabled": body.source}

@app.post("/api/news/feeds/enable")
async def news_feed_enable(body: FeedToggleRequest, _auth: bool = Depends(require_api_key)):
    svc: NewsService = getattr(app.state, 'news_service')
    ok = svc.enable_source(body.source)
    if not ok:
        raise HTTPException(status_code=404, detail="not_disabled_or_missing")
    return {"status": "ok", "enabled": body.source}

class FeedAddRequest(BaseModel):
    url: str
    symbol: Optional[str] = None

@app.post("/api/news/feeds/add")
async def news_feed_add(body: FeedAddRequest, _auth: bool = Depends(require_api_key)):
    svc: NewsService = getattr(app.state, 'news_service')
    try:
        name = svc.add_rss_feed(body.url, symbol=body.symbol)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"add_failed:{e}")
    return {"status": "ok", "added": name, "url": body.url}

@app.get("/api/news/body")
async def news_body(ids: str, _auth: bool = Depends(require_api_key)):
    """여러 기사 id 에 대한 body 를 한번에 조회.

    Query:
      ids: 콤마구분 id 목록 (최대 200)
    Response:
      { status: ok, items: [ {id, body}, ... ] }
    """
    if not ids:
        return {"status": "ok", "items": []}
    try:
        id_list = [int(x) for x in ids.split(',') if x.strip().isdigit()]
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_ids")
    if not id_list:
        return {"status": "ok", "items": []}
    svc: NewsService = getattr(app.state, 'news_service')
    try:
        rows = await svc.repo.fetch_bodies(id_list)  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"bulk_fetch_failed:{e}")
    return {"status": "ok", "items": rows}

@app.get("/api/news/sentiment")
async def news_sentiment(window: int = 60, symbol: Optional[str] = None, _auth: bool = Depends(require_api_key)):
    svc: NewsService = getattr(app.state, 'news_service')
    try:
        stats = await svc.repo.sentiment_window_stats(minutes=window, symbol=symbol)
        if not isinstance(stats, dict):  # unexpected fallback
            stats = {}
    except Exception as e:  # noqa: BLE001
        # Defensive: don't break endpoint, surface error indicator
        stats = {"error": str(e)}
    base = {"window_minutes": window, "count": 0, "avg": None, "pos": 0, "neg": 0, "neutral": 0, "symbol": symbol}
    merged = {**base, **stats}
    return {"status": "ok", **merged}

@app.post("/api/news/fetch/manual")
async def news_fetch_manual(_auth: bool = Depends(require_api_key)):
    """수동 즉시 뉴스 수집: 백그라운드 주기 대기 없이 한번 poll."""
    svc: NewsService = getattr(app.state, 'news_service')
    try:
        inserted = await svc.poll_once()
        return {"status": "ok", "inserted": inserted, "sources": svc.sources}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}

@app.get("/api/news/articles")
async def news_articles(days: Optional[int] = None, limit: int = 200, offset: int = 0, symbol: Optional[str] = None, with_body: bool = False, _auth: bool = Depends(require_api_key)):
    """뉴스 기사 목록 조회.

    Params:
      days: 최근 N일 (옵션). 지정 시 since_ts 필터 적용.
      limit: 반환 최대 개수 (max 1000)
      offset: 단순 페이지네이션 용 슬라이스 (DB offset 미사용)
      with_body: True -> body 포함
    """
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit>0")
    if limit > 1000:
        limit = 1000
    svc: NewsService = getattr(app.state, 'news_service')
    since_ts = None
    if isinstance(days, int) and days > 0:
        since_ts = int(time.time()) - days * 86400
    try:
        rows = await svc.repo.fetch_recent(limit=limit+offset, symbol=symbol, since_ts=since_ts, summary_only=not with_body)  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"fetch_failed:{e}")
    if offset > 0:
        rows = rows[offset:]
    rows = rows[:limit]
    return {"status": "ok", "count": len(rows), "limit": limit, "offset": offset, "days": days, "articles": rows}

@app.post("/api/news/backfill")
async def news_backfill(days: int = 10, max_per_source: int = 500, _auth: bool = Depends(require_api_key)):
    """단순 RSS 재수집을 통한 과거 N일 기사 백필 (feed 제공 범위 내).

    주의: RSS 자체가 과거 깊은 히스토리를 제공하지 않으면 효과 제한.
    max_per_source: 소스별 삽입 상한 (중복/메모리 보호)
    """
    if days <= 0:
        raise HTTPException(status_code=400, detail="days>0")
    svc: NewsService = getattr(app.state, 'news_service')
    cutoff = int(time.time()) - days * 86400
    total_inserted = 0
    per_source_kept: dict[str, int] = {}
    seen_hashes: set[str] = set()
    try:
        for fetcher in svc.fetchers:
            source_label = getattr(fetcher, 'source_name', getattr(fetcher, 'source', 'unknown'))
            try:
                items = await fetcher.fetch()
            except Exception:
                continue
            kept = 0
            collected: list[dict] = []
            for it in items:
                try:
                    if 'published_ts' not in it:
                        it['published_ts'] = int(time.time())
                    if it['published_ts'] < cutoff:
                        continue
                    if 'hash' not in it:
                        base = f"{it.get('url')}|{it.get('published_ts')}|{it.get('title')}"
                        import hashlib as _h
                        it['hash'] = _h.sha256(base.encode()).hexdigest()[:48]
                    hval = it['hash']
                    if hval in seen_hashes:
                        continue
                    seen_hashes.add(hval)
                    collected.append(it)
                    kept += 1
                    if kept >= max_per_source:
                        break
                except Exception:
                    continue
            if collected:
                try:
                    ins = await svc.repo.insert_articles(collected)  # type: ignore
                    total_inserted += ins
                except Exception:
                    pass
            per_source_kept[source_label] = kept
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e), "inserted": total_inserted, "per_source": per_source_kept}
    return {"status": "ok", "inserted": total_inserted, "days": days, "per_source": per_source_kept, "cutoff": cutoff}

@app.get("/api/news/summary")
async def news_summary(window_minutes: int = 1440, symbol: Optional[str] = None, _auth: bool = Depends(require_api_key)):
    """최근 기사 집계 요약.

    - window_minutes: 최근 N분 (기본 24h)
    - 기사 수, 소스별 비중, 감성 통계(평균/표준편차/양/음/중립 비율) 포함
    """
    if window_minutes <= 0:
        raise HTTPException(status_code=400, detail="window_minutes>0")
    cutoff = int(time.time()) - window_minutes * 60
    svc: NewsService = getattr(app.state, 'news_service')
    try:
        rows = await svc.repo.fetch_recent(limit=5000, symbol=symbol, since_ts=cutoff, summary_only=True)  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
    # Defensive: drop legacy synthetic rows if still present in DB for any reason
    rows = [r for r in rows if r.get('source') != 'demo_feed']
    total = len(rows)
    per_source: dict[str, int] = {}
    sentiments: list[float] = []
    pos = neg = neu = 0
    for r in rows:
        src = r.get("source") or "unknown"
        per_source[src] = per_source.get(src, 0) + 1
        s = r.get("sentiment")
        if isinstance(s, (int, float)):
            sentiments.append(float(s))
            if s > 0.05:
                pos += 1
            elif s < -0.05:
                neg += 1
            else:
                neu += 1

@app.get("/api/news/preflight")
async def news_preflight(feed: Optional[str] = None, sample_titles: int = 3, _auth: bool = Depends(require_api_key)):
    """RSS 사전 점검 (connectivity + parse) endpoint.

    - feed 파라미터 지정 시 해당 URL 1개만 검사
    - feed 미지정 시 현재 서비스에 구성된 fetcher 목록 또는 환경변수 `NEWS_RSS_FEEDS` 사용
    결과: 각 feed 별 네트워크/파싱 상태 요약
    """
    import time as _time, asyncio as _asyncio
    results: list[dict] = []
    # Determine feed list
    if feed:
        feed_list = [feed]
    else:
        # Prefer live service fetchers if started
        try:
            svc: NewsService = getattr(app.state, 'news_service')  # type: ignore
            feed_list = [getattr(f, 'url', None) for f in getattr(svc, 'fetchers', []) if getattr(f, 'url', None)]
        except Exception:
            feed_list = []
        if not feed_list:
            raw_env = os.getenv('NEWS_RSS_FEEDS', '')
            if raw_env.strip():
                feed_list = [p.strip() for p in raw_env.split(',') if p.strip()]
    if not feed_list:
        return {"status": "no_feeds", "feeds": []}
    # Perform checks sequentially (feeds usually few; keeps resource usage low)
    for url in feed_list:
        t0 = _time.perf_counter()
        entry = {
            "url": url,
            "http_status": None,
            "content_type": None,
            "latency_ms": None,
            "parsed_entries": 0,
            "parsed_titles_sample": [],
            "error": None,
        }
        try:
            import aiohttp, feedparser  # type: ignore
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Try HEAD first; some feeds may not support – fallback to GET
                resp = None
                try:
                    resp = await session.head(url, allow_redirects=True)
                    if resp.status >= 400:
                        # force GET to inspect body in error conditions
                        await resp.release()
                        resp = None
                except Exception:
                    resp = None
                if resp is None:
                    resp = await session.get(url, allow_redirects=True)
                entry["http_status"] = resp.status
                entry["content_type"] = resp.headers.get('Content-Type')
                # Read at most ~512KB to avoid huge downloads
                raw = await resp.read()
                await resp.release()
            # Parse with feedparser (sync) in thread
            loop = _asyncio.get_event_loop()
            parsed = await loop.run_in_executor(None, lambda: feedparser.parse(raw))
            items = getattr(parsed, 'entries', []) or []
            entry["parsed_entries"] = len(items)
            if items:
                titles = []
                for it in items[:sample_titles]:
                    title = getattr(it, 'title', None) or getattr(it, 'id', None) or '(no-title)'
                    if title:
                        titles.append(title[:160])
                entry["parsed_titles_sample"] = titles
        except Exception as e:  # noqa: BLE001
            entry["error"] = str(e)[:300]
        finally:
            entry["latency_ms"] = round((_time.perf_counter() - t0) * 1000, 2)
        results.append(entry)
    # Aggregate quick summary
    ok = sum(1 for r in results if r["parsed_entries"] > 0 and not r["error"])
    return {"status": "ok", "feeds_total": len(results), "feeds_parsed_ok": ok, "feeds": results}
    avg = std = None
    if sentiments:
        import math
        avg = sum(sentiments)/len(sentiments)
        if len(sentiments) > 1:
            mu = avg
            var = sum((x-mu)**2 for x in sentiments)/(len(sentiments)-1)
            std = math.sqrt(var)
        else:
            std = 0.0
    source_dist = sorted(
        [ {"source": k, "count": v, "ratio": (v/total if total else 0.0)} for k,v in per_source.items() ],
        key=lambda x: x["count"], reverse=True
    )
    return {
        "status": "ok",
        "window_minutes": window_minutes,
        "symbol": symbol,
        "total": total,
        "sources": source_dist,
        "sentiment": {
            "count": len(sentiments),
            "avg": avg,
            "std": std,
            "pos": pos,
            "neg": neg,
            "neutral": neu,
        }
    }

@app.get("/api/news/search")
async def news_search(q: str, limit: int = 200, days: int = 7, symbol: Optional[str] = None, _auth: bool = Depends(require_api_key)):
    """간단 제목 검색 (ILIKE). Full-text Search 대체 (향후 확장 가능)."""
    if not q or len(q.strip()) < 2:
        raise HTTPException(status_code=400, detail="query_too_short")
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit>0")
    if limit > 1000:
        limit = 1000
    cutoff = int(time.time()) - max(1, days) * 86400
    pool = await init_pool()
    if pool is None:
        return {"status": "error", "error": "db_unavailable"}
    async with pool.acquire() as conn:  # type: ignore
        # Basic safe pattern: parameterized ILIKE & cutoff filter
        params = [cutoff]
        where = ["published_ts >= $1"]
        if symbol:
            params.append(symbol)
            where.append(f"symbol = ${len(params)}")
        params.append(f"%{q}%")
        where.append(f"title ILIKE ${len(params)}")
        params.append(limit)
        sql = f"SELECT id, source, symbol, title, url, published_ts, sentiment FROM news_articles WHERE {' AND '.join(where)} ORDER BY published_ts DESC LIMIT ${len(params)}"
        rows = await conn.fetch(sql, *params)
        items = []
        for r in rows:
            items.append({k: r[k] for k in r.keys()})
        return {"status": "ok", "query": q, "days": days, "limit": limit, "count": len(items), "articles": items}

@app.get("/api/news/debug/persistence")
async def news_debug_persistence(include_samples: int = 5, _auth: bool = Depends(require_api_key)):
    """Lightweight persistence diagnostic.

    Returns aggregate counts confirming data is physically stored in the current
    PostgreSQL database the API process is connected to. Helps reconcile cases
    where the UI search shows results but the operator suspects rows are not
    really persisted (e.g. pointing at different DB, volume mismatch, etc.).

    Fields:
      - total_rows: total rows in news_articles
      - min_published_ts / max_published_ts
      - max_ingested_ts
      - per_source: top (<=50) sources with counts
      - rows_missing_body: count of rows with NULL/empty body
      - recent: latest N sample rows (id, source, title, published_ts)
    """
    if include_samples < 0:
        include_samples = 0
    if include_samples > 50:
        include_samples = 50
    pool = await init_pool()
    if pool is None:
        return {"status": "error", "error": "db_unavailable"}
    async with pool.acquire() as conn:  # type: ignore
        agg = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total_rows,
                   COALESCE(MIN(published_ts),0) AS min_published_ts,
                   COALESCE(MAX(published_ts),0) AS max_published_ts,
                   COALESCE(MAX(ingested_ts),0) AS max_ingested_ts
            FROM news_articles
            """
        )
        per_source_rows = await conn.fetch(
            "SELECT source, COUNT(*) AS count FROM news_articles GROUP BY source ORDER BY count DESC LIMIT 50"
        )
        missing_body = await conn.fetchval(
            "SELECT COUNT(*) FROM news_articles WHERE body IS NULL OR length(trim(body))=0"
        )
        samples: list[dict] = []
        if include_samples:
            sample_rows = await conn.fetch(
                "SELECT id, source, title, published_ts FROM news_articles ORDER BY published_ts DESC LIMIT $1",
                include_samples,
            )
            for r in sample_rows:
                samples.append({k: r[k] for k in r.keys()})
    # Update gauge with current total
    try:
        from backend.apps.api.main import NEWS_ARTICLES_TOTAL as _NAT
        if agg:
            _NAT.set(int(agg["total_rows"]))
    except Exception:
        pass
    # Per-source last ingest timestamps from running service (best-effort)
    per_source_last_ingest: list[dict] = []
    try:
        svc: NewsService = getattr(app.state, 'news_service')  # type: ignore
        if hasattr(svc, '_src_last_ingest'):
            for src, ts in getattr(svc, '_src_last_ingest').items():  # type: ignore
                per_source_last_ingest.append({"source": src, "last_ingest_ts": ts})
    except Exception:
        pass
    return {
        "status": "ok",
        "total_rows": int(agg["total_rows"]) if agg else 0,
        "min_published_ts": int(agg["min_published_ts"]) if agg else 0,
        "max_published_ts": int(agg["max_published_ts"]) if agg else 0,
        "max_ingested_ts": int(agg["max_ingested_ts"]) if agg else 0,
        "per_source": [ {"source": r["source"], "count": int(r["count"]) } for r in per_source_rows ],
        "rows_missing_body": int(missing_body) if missing_body is not None else 0,
        "recent": samples,
        "per_source_last_ingest": per_source_last_ingest,
    }

@app.get("/api/news/debug/dbinfo")
async def news_debug_dbinfo(_auth: bool = Depends(require_api_key)):
    """Expose minimal (non-sensitive) DB target info + quick sanity metrics.

    Helps detect 환경 변수(DB 접속) mismatch: API 가 A DB 를 보고 있는데
    사용자가 다른 인스턴스 B 를 psql 로 보고 있을 때 row count 0 착각 문제.

    Does NOT return password. Only host/db/user and search_path plus a quick
    news_articles existence + count.
    """
    cfg = load_config()
    pool = await init_pool()
    if pool is None:
        return {"status": "error", "error": "db_unavailable"}
    async with pool.acquire() as conn:  # type: ignore
        # search_path & current database/user
        try:
            sp = await conn.fetchval("SHOW search_path")
        except Exception:
            sp = None
        exists = False
        total = None
        try:
            row = await conn.fetchrow("SELECT to_regclass('public.news_articles') AS t")
            exists = bool(row and row["t"])
            if exists:
                total = await conn.fetchval("SELECT COUNT(*) FROM news_articles")
        except Exception:
            pass
    return {
        "status": "ok",
        "db_host": cfg.postgres_host,
        "db_port": cfg.postgres_port,
        "db_name": cfg.postgres_db,
        "db_user": cfg.postgres_user,
        "search_path": sp,
        "table_exists": exists,
        "table_row_count": int(total) if isinstance(total, (int,)) else total,
    }

@app.get("/api/news/status")
async def news_status(_auth: bool = Depends(require_api_key)):
    """Lightweight runtime status for news ingestion.

    Provides timestamps and counters so UI가 첫 insert 전 상태/대기 구분 가능.
    """
    try:
        svc: NewsService = getattr(app.state, 'news_service')  # type: ignore
    except Exception:
        return {"status": "error", "error": "service_unavailable"}
    # Derive stats
    last_poll = svc.last_poll_ts
    last_ingest = svc.last_ingested_ts
    fetch_runs = svc.fetch_runs
    ingested_total = svc.articles_ingested
    errors = svc.error_count
    per_source_last_ingest = []
    try:
        for src, ts in getattr(svc, '_src_last_ingest', {}).items():  # type: ignore
            per_source_last_ingest.append({"source": src, "last_ingest_ts": ts})
    except Exception:
        pass
    health_scores = getattr(svc, '_last_health_scores', {})
    # Compose
    # Live DB total rows (best-effort; avoid heavy cost on every call; simple count)
    db_total = None
    try:
        pool = await init_pool()
        if pool is not None:
            async with pool.acquire() as conn:  # type: ignore
                db_total = await conn.fetchval("SELECT COUNT(*) FROM news_articles")
    except Exception:
        pass
    return {
        "status": "ok",
        "last_poll_ts": last_poll,
        "last_ingested_ts": last_ingest,
        "fetch_runs": fetch_runs,
        "articles_ingested_runtime": ingested_total,
        "error_count": errors,
        "sources": svc.sources,
        "per_source_last_ingest": per_source_last_ingest,
        "health_scores": health_scores,
        "health_threshold": getattr(svc, '_health_threshold', None),
        "stall_threshold_sec": getattr(svc, '_stall_threshold', None),
        "db_total_rows": int(db_total) if isinstance(db_total, (int,)) else db_total,
        "dedup_preload_size": len(getattr(svc, '_recent_hashes_set', [])),
        "dedup_preload_checked": bool(getattr(svc, '_dedup_preload_checked', False)),
    }

@app.get("/api/news/sources")
async def news_sources(include_history: int = 10, _auth: bool = Depends(require_api_key)):
        """뉴스 수집 소스별 상태/헬스/중복/스톨/백오프 지표 요약.

        Params:
            - include_history: 각 소스 dedup history 최근 N개 (기본 10, 최대 50)
        응답:
            {
                status: ok,
                count: <소스수>,
        return {"status": "ok", "sources": out_sources, "dedup_history": dedup_hist}
                dedup_history: { source: [ {ts, raw, kept, ratio}, ... ] }
            }
        """
        if include_history <= 0:
                include_history = 10
        if include_history > 50:
                include_history = 50
        svc = getattr(app.state, 'news_service', None)
        if not svc:
                raise HTTPException(status_code=503, detail="news_service_unavailable")
        # health snapshot (scores, penalties etc.)
        sources = svc.health_snapshot()
        hist_full = svc.dedup_history_snapshot()
        trimmed: dict[str, list[dict[str, float]]] = {}
        for src, series in hist_full.items():
                trimmed[src] = series[-include_history:]
        return {"status": "ok", "count": len(sources), "sources": sources, "dedup_history": trimmed, "history_points": include_history}

def _training_service() -> TrainingService:
    return TrainingService(cfg.symbol, cfg.kline_interval, cfg.model_artifact_dir)

@app.get("/api/inference/seed/status")
async def inference_seed_status(_auth: bool = Depends(require_api_key)):
    """Seed fallback 모드 관련 실시간 상태.

    Fields:
      active: 현재 seed fallback 중인지
      current_interval_seconds: 진행 중일 경우 경과 시간
      intervals: 과거 완료된 fallback 구간 리스트 [(start,end), ...]
      total_intervals: 누적 fallback 횟수
      last_exit_ts: 마지막으로 fallback 종료된 unix ts (없으면 null)
      instability_ratio_1h: 최근 1시간(기본 윈도우) 점유 비율 (Prometheus gauge 와 동일 산식 재사용)
    """
    now = time.time()
    active = bool(getattr(app.state, "seed_fallback_active", False))
    start = getattr(app.state, "seed_fallback_start", None)
    intervals = list(getattr(app.state, "seed_fallback_intervals", []))
    last_exit = getattr(app.state, "seed_fallback_last_exit", None)
    current_interval = None
    if active and isinstance(start, (int,float)):
        current_interval = now - start
    total_intervals = len(intervals) + (1 if active else 0)
    # Recompute instability ratio over default 3600s window (same logic as loop)
    window_seconds = 3600
    window_start = now - window_seconds
    acc = 0.0
    work = list(intervals)
    if active and start is not None:
        work.append((start, now))
    for s,e in work:
        if e < window_start or s > now:
            continue
        overlap_start = max(s, window_start)
        overlap_end = min(e, now)
        if overlap_end > overlap_start:
            acc += (overlap_end - overlap_start)
    ratio = acc / window_seconds if window_seconds > 0 else 0.0
    return {
        "status": "ok",
        "active": active,
        "current_interval_seconds": current_interval,
        "intervals": intervals[-50:],  # cap output
        "total_intervals": total_intervals,
        "last_exit_ts": last_exit,
        "instability_ratio_1h": ratio,
    }

@app.get("/metrics")
async def metrics():
    data = generate_latest()  # type: ignore
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)

@app.post("/api/features/compute")
async def compute_features(symbol: Optional[str] = None, interval: Optional[str] = None, _auth: bool = Depends(require_api_key)):
    svc = FeatureService(symbol or cfg.symbol, interval or cfg.kline_interval)
    result = await svc.compute_and_store()
    return result

@app.get("/api/features/status")
async def feature_status():
    svc: Optional[FeatureService] = getattr(app.state, "feature_service", None)
    if not svc:
        return {"running": False}
    return {
        "running": True,
        "symbol": svc.symbol,
        "interval": svc.interval,
        "last_success_ts": svc.last_success_ts,
        "last_error_ts": svc.last_error_ts,
        "next_run_interval_seconds": cfg.feature_sched_interval,
        "lock_held": svc._lock.locked(),
    }

@app.get("/api/features/recent")
async def recent_features(limit: int = 50, _auth: bool = Depends(require_api_key)):
    svc: Optional[FeatureService] = getattr(app.state, "feature_service", None)
    if not svc:
        svc = FeatureService(cfg.symbol, cfg.kline_interval)
    rows = await svc.fetch_recent(limit=limit)
    return rows

@app.get("/api/features/export")
async def export_features(limit: int = 200, _auth: bool = Depends(require_api_key)):
    svc: Optional[FeatureService] = getattr(app.state, "feature_service", None)
    if not svc:
        svc = FeatureService(cfg.symbol, cfg.kline_interval)
    csv_data = await svc.recent_as_csv(limit=limit)
    return PlainTextResponse(content=csv_data, media_type="text/csv")

# ---------------------------------------------------------------------------
# Feature Backfill Runs — list and detail
# ---------------------------------------------------------------------------
@app.get("/api/features/backfill/runs", dependencies=[Depends(require_api_key)])
async def feature_backfill_runs_list(
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    sort_by: Optional[str] = None,
    order: Optional[str] = None,
    started_from: Optional[int] = None,  # epoch ms or seconds
    started_to: Optional[int] = None,    # epoch ms or seconds
):
    """List feature_backfill_runs with filters + pagination.

    Query params:
    - symbol, interval: default to current cfg
    - status: running|success|error
    - started_from, started_to: inclusive start time window (epoch seconds or ms)
    - page (1-based), page_size (<= 200)
    - sort_by: one of [started_at, finished_at, inserted, requested_target, used_window, status, id]
    - order: asc|desc (default desc)
    """
    sym = symbol or cfg.symbol
    itv = interval or cfg.kline_interval

    # Coerce times: accept seconds or ms
    def _coerce_ts(v: Optional[int]) -> Optional[int]:
        if not v:
            return None
        try:
            # treat > 10^12 as ms
            return int(v // 1000) if int(v) > 10_000_000_000 else int(v)
        except Exception:
            return None

    sf = _coerce_ts(started_from)
    st = _coerce_ts(started_to)

    # Build WHERE dynamically with numbered params
    where = ["symbol = $1", "interval = $2"]
    args: list[object] = [sym, itv]
    idx = 3
    if status:
        where.append(f"status = ${idx}")
        args.append(status)
        idx += 1
    if sf is not None:
        where.append(f"started_at >= to_timestamp(${idx})")
        args.append(sf)
        idx += 1
    if st is not None:
        where.append(f"started_at <= to_timestamp(${idx})")
        args.append(st)
        idx += 1
    where_sql = " AND ".join(where)

    # Sorting: whitelist columns
    allowed_sort = {
        "started_at": "started_at",
        "finished_at": "finished_at",
        "inserted": "inserted",
        "requested_target": "requested_target",
        "used_window": "used_window",
        "status": "status",
        "id": "id",
    }
    sort_col = allowed_sort.get((sort_by or "started_at").lower(), "started_at")
    sort_dir = "ASC" if (order or "desc").lower() == "asc" else "DESC"

    # Pagination
    p = max(1, int(page))
    ps = max(1, min(200, int(page_size)))
    offset = (p - 1) * ps

    # Query total and page items
    q_total = f"""
    SELECT COUNT(1) AS c
    FROM feature_backfill_runs
    WHERE {where_sql}
    """
    q_items = f"""
    SELECT id, symbol, interval, requested_target, used_window, inserted,
           from_open_time, to_open_time, from_close_time, to_close_time,
           source_fetched, start_index, end_index, status, error,
           started_at, finished_at
    FROM feature_backfill_runs
    WHERE {where_sql}
    ORDER BY {sort_col} {sort_dir}
    OFFSET {offset}
    LIMIT {ps}
    """
    pool = await init_pool()
    async with pool.acquire() as conn:
        total_row = await conn.fetchrow(q_total, *args)
        total = int(total_row["c"]) if total_row else 0  # type: ignore
        rows = await conn.fetch(q_items, *args)
        items = [dict(r) for r in rows]
        return {"items": items, "total": total, "page": p, "page_size": ps}

@app.get("/api/features/backfill/runs/{run_id}", dependencies=[Depends(require_api_key)])
async def feature_backfill_run_detail(run_id: int):
    pool = await init_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, symbol, interval, requested_target, used_window, inserted,
                   from_open_time, to_open_time, from_close_time, to_close_time,
                   source_fetched, start_index, end_index, status, error,
                   started_at, finished_at
            FROM feature_backfill_runs
            WHERE id = $1
            """,
            run_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="backfill_run_not_found")
        return dict(row)

@app.get("/api/features/drift")
async def feature_drift(feature: str = "ret_1", window: int = 200, threshold: Optional[float] = None, _auth: bool = Depends(require_api_key)):
    svc: Optional[FeatureService] = getattr(app.state, "feature_service", None)
    if not svc:
        svc = FeatureService(cfg.symbol, cfg.kline_interval)
    # Apply drift window override if provided via DB-backed settings
    try:
        if window == 200 and hasattr(app.state, 'drift_window'):
            w = int(getattr(app.state, 'drift_window'))
            if w > 0:
                window = w
    except Exception:
        pass
    stats = await svc.compute_drift(feature=feature, window=window)
    if stats.get("status") == "ok":
        z = stats.get("z_score", 0.0) or 0.0
        th = threshold if (threshold is not None and threshold > 0) else (
            float(getattr(app.state, 'drift_threshold', None)) if isinstance(getattr(app.state, 'drift_threshold', None), (int,float)) else cfg.drift_z_threshold
        )
        stats["drift"] = abs(z) >= th
        stats["threshold"] = th
    return stats

@app.get("/api/features/drift/scan")
async def feature_drift_scan(window: int = 200, features: Optional[str] = None, threshold: Optional[float] = None, _auth: bool = Depends(require_api_key)):
    """Scan multiple comma-separated features (default core set) and return per-feature drift stats.
    Query param features=ret_1,ret_5,ret_10,rsi_14,rolling_vol_20,ma_20,ma_50
    """
    # Resolve default features from runtime override or fallback
    default_str = None
    try:
        default_str = getattr(app.state, 'drift_features', None)
    except Exception:
        default_str = None
    if not isinstance(default_str, str) or not default_str.strip():
        default_str = "ret_1,ret_5,ret_10,rsi_14,rolling_vol_20,ma_20,ma_50"
    _feat_source = features if (isinstance(features, str) and features.strip()) else default_str
    feat_list = [f.strip() for f in _feat_source.split(",") if f.strip()]
    svc: Optional[FeatureService] = getattr(app.state, "feature_service", None)
    if not svc:
        svc = FeatureService(cfg.symbol, cfg.kline_interval)
    # Apply window override if not explicitly provided
    try:
        if window == 200 and hasattr(app.state, 'drift_window'):
            w = int(getattr(app.state, 'drift_window'))
            if w > 0:
                window = w
    except Exception:
        pass
    result = await svc.compute_drift_scan(feat_list, window=window)
    if result.get("status") == "ok":
        th = threshold if (threshold is not None and threshold > 0) else (
            float(getattr(app.state, 'drift_threshold', None)) if isinstance(getattr(app.state, 'drift_threshold', None), (int,float)) else cfg.drift_z_threshold
        )
        # annotate drift boolean per feature
        for f, st in result.get("results", {}).items():
            if st.get("status") == "ok":
                z = st.get("z_score", 0.0) or 0.0
                st["drift"] = abs(z) >= th
                st["threshold"] = th
    # Build summary snapshot for history (best-effort)
    try:
        snap: dict[str, Any] = {
            "ts": time.time(),
            "window": window,
            "feature_count": len(result.get("results", {})) if isinstance(result.get("results"), dict) else None,
            "applied_threshold": th,
        }
        # derive counts
        drift_cnt = 0
        max_abs_z = None
        top_feat = None
        if result.get("results") and isinstance(result.get("results"), dict):
            for fname, st in result["results"].items():
                if isinstance(st, dict) and st.get("status") == "ok":
                    if st.get("drift"):
                        drift_cnt += 1
                    z = st.get("z_score")
                    if isinstance(z, (int,float)):
                        if max_abs_z is None or abs(z) > abs(max_abs_z):
                            max_abs_z = z
                            top_feat = fname
        snap["drift_count"] = drift_cnt
        snap["total"] = len(result.get("results", {})) if isinstance(result.get("results"), dict) else None
        snap["max_abs_z"] = max_abs_z
        snap["top_feature"] = top_feat
        _append_drift_history({k: v for k, v in snap.items() if v is not None})
    except Exception:
        pass
    return result

def _append_drift_history(snapshot: dict[str, Any], max_len: int = 500):
    """Append a drift scan summary to in-memory ring buffer on app.state.

    snapshot keys expected: ts, total, drift_count, max_abs_z, top_feature, applied_threshold.
    """
    try:
        buf: list[dict[str, Any]] = getattr(app.state, 'drift_history', [])  # type: ignore
        buf.append(snapshot)
        if len(buf) > max_len:
            # trim oldest 20% to avoid O(n) per pop
            trim = max(int(max_len * 0.2), 1)
            del buf[0:trim]
        setattr(app.state, 'drift_history', buf)
        setattr(app.state, 'last_drift_summary', snapshot)
    except Exception:
        pass

@app.get("/api/features/drift/history")
async def feature_drift_history(limit: int = 100, _auth: bool = Depends(require_api_key)):
    """Return recent feature drift scan summaries (in-memory).

    Note: Populated best-effort when feature_drift_scan endpoint is invoked. If no
    scans have occurred since process start this may be empty.
    """
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be > 0")
    if limit > 1000:
        raise HTTPException(status_code=400, detail="limit too large (max 1000)")
    hist: list[dict[str, Any]] = getattr(app.state, 'drift_history', [])  # type: ignore
    return {"status": "ok", "count": len(hist), "items": hist[-limit:]}

@app.post("/api/models/seed/ensure")
async def models_seed_ensure(_auth: bool = Depends(require_api_key)):
    """레지스트리에 seed baseline 모델이 없다면 강제로 생성.

    반환:
      - created: 새로 생성 여부
      - model_id: 생성된 경우 id (best-effort)
      - message: 상황 설명
    """
    repo = ModelRegistryRepository()
    name = cfg.auto_promote_model_name
    try:
        existing = await repo.fetch_latest(name, "supervised", limit=3)
        for r in existing:
            if r.get("status") == "production":
                return {"created": False, "message": "production model already exists", "model_name": name}
    except Exception as e:  # noqa: BLE001
        return {"created": False, "error": str(e), "message": "fetch_failed", "model_name": name}
    # Attempt seed insert
    baseline_metrics = {
        "auc": 0.50,
        "accuracy": 0.50,
        "brier": 0.25,
        "ece": 0.05,
        "mce": 0.05,
        "seed_baseline": True,
        "manual_seed": True,
    }
    version = "seed"
    try:
        model_id = await repo.register(
            name=name,
            version=version,
            model_type="supervised",
            status="production",
            artifact_path=None,
            metrics=baseline_metrics,
        )
        try:
            SEED_AUTO_SEED_TOTAL.labels(source="manual", result="success").inc()
        except Exception:
            pass
        return {"created": True, "model_id": model_id, "model_name": name, "version": version}
    except Exception as e:  # noqa: BLE001
        try:
            SEED_AUTO_SEED_TOTAL.labels(source="manual", result="error").inc()
        except Exception:
            pass
        return {"created": False, "error": str(e), "message": "seed_insert_failed", "model_name": name}

async def seed_instability_loop(window_seconds: int = 3600, interval: float = 30.0):
    SEED_FALLBACK_INSTABILITY_WINDOW.set(window_seconds)
    while True:
        try:
            now = time.time()
            # Gather current intervals
            intervals = getattr(app.state, "seed_fallback_intervals", [])
            # If currently in fallback, include partial interval
            start = getattr(app.state, "seed_fallback_start", None)
            work_intervals = list(intervals)
            if start is not None:
                work_intervals.append((start, now))
            # Sum overlap with window (now - window_seconds, now)
            window_start = now - window_seconds
            acc = 0.0
            for s, e in work_intervals:
                if e < window_start or s > now:
                    continue
                overlap_start = max(s, window_start)
                overlap_end = min(e, now)
                if overlap_end > overlap_start:
                    acc += (overlap_end - overlap_start)
            ratio = acc / window_seconds if window_seconds > 0 else 0.0
            SEED_FALLBACK_INSTABILITY_RATIO.labels(window=str(window_seconds)).set(ratio)
        except Exception:
            pass
        await asyncio.sleep(interval)

# -------------------- Calibration Monitor Loop --------------------
async def calibration_monitor_loop():
    """Periodic calibration drift evaluation loop.

    Uses recent inference logs within a sliding window to compute Brier, ECE, MCE
    and compares live ECE to production (validation) ECE. Maintains absolute &
    relative drift streak counters and saves a snapshot for the status endpoint.

    Config (from AppConfig):
      - calibration_monitor_interval
      - calibration_monitor_window_seconds
      - calibration_monitor_min_samples
      - calibration_monitor_ece_drift_abs
      - calibration_monitor_ece_drift_rel
      - calibration_monitor_abs_streak_trigger / rel_streak_trigger (used by status endpoint)
    """
    interval = max(5.0, float(cfg.calibration_monitor_interval))  # safety lower bound
    window_seconds = int(cfg.calibration_monitor_window_seconds)
    min_samples = int(cfg.calibration_monitor_min_samples)
    abs_th = float(cfg.calibration_monitor_ece_drift_abs)
    rel_th = float(cfg.calibration_monitor_ece_drift_rel)
    bins = 10  # fixed for monitor loop (endpoint can vary)
    repo = InferenceLogRepository()
    logger.info("calibration_monitor_loop started interval=%s window_seconds=%s min_samples=%s", interval, window_seconds, min_samples)
    # Bottom-only mode: always monitor bottom target
    mon_target = 'bottom'
    while True:
        started = time.perf_counter()
        try:
            # Scope by configured symbol/interval to prevent cross-interval mixing
            rows = await repo.fetch_window_for_live_calibration(
                window_seconds=window_seconds,
                symbol=cfg.symbol,
                interval=cfg.kline_interval,
                target=mon_target,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("calibration_monitor_fetch_failed window=%s err=%s", window_seconds, e)
            await asyncio.sleep(interval)
            continue
        if not rows or len(rows) < min_samples:
            # Not enough data yet – store lightweight snapshot (optional)
            _streak_state.last_snapshot = {
                "ts": time.time(),
                "status": "insufficient_samples",
                "sample_count": len(rows) if rows else 0,
                "min_samples": min_samples,
                "target": mon_target,
            }
            await asyncio.sleep(interval)
            continue
        probs: list[float] = []
        labels: list[int] = []
        for r in rows:
            try:
                p = float(r["probability"])
                if p < 0: p = 0.0
                if p > 1: p = 1.0
                probs.append(p)
                y_raw = r["realized"]
                labels.append(1 if (y_raw is not None and y_raw > 0) else 0)
            except Exception:
                continue
        n = len(probs)
        if n < min_samples:
            _streak_state.last_snapshot = {
                "ts": time.time(),
                "status": "insufficient_samples_postfilter",
                "sample_count": n,
                "min_samples": min_samples,
                "target": mon_target,
            }
            await asyncio.sleep(interval)
            continue
        # Shared calibration compute
        try:
            calc = compute_calibration(probs, labels, bins=bins)
        except Exception as e:  # noqa: BLE001
            logger.warning("calibration_monitor_compute_failed err=%s", e)
            await asyncio.sleep(interval)
            continue
        brier = calc.get("brier")
        ece = calc.get("ece")
        mce = calc.get("mce")
        reliability_bins = calc.get("reliability_bins", [])
        # Production ECE lookup
        prod_ece: Optional[float] = await _fetch_production_ece()
        live_ece = ece
        live_mce = mce
        delta = None
        rel_gap = None
        drift_abs = False
        drift_rel = False
        if prod_ece is not None:
            delta = abs(live_ece - prod_ece)
            rel_gap = (delta / prod_ece) if prod_ece > 0 else None
            drift_abs = delta >= abs_th
            drift_rel = rel_gap is not None and rel_gap >= rel_th
        # Update Prometheus gauges (best-effort)
        try:
            INFERENCE_LIVE_BRIER.set(brier)
            INFERENCE_LIVE_ECE.set(live_ece)
            INFERENCE_LIVE_MCE.set(live_mce)
            if delta is not None:
                INFERENCE_LIVE_ECE_DELTA.set(delta)
            CALIBRATION_LAST_LIVE_ECE.set(live_ece)
            if prod_ece is not None:
                CALIBRATION_LAST_PROD_ECE.set(prod_ece)
        except Exception:
            pass
        # Streak management & events
        streak_abs = getattr(_streak_state, 'abs_streak', 0)
        streak_rel = getattr(_streak_state, 'rel_streak', 0)
        if drift_abs:
            CALIBRATION_DRIFT_EVENTS.labels(type="abs").inc()
            streak_abs += 1
        else:
            streak_abs = 0
        if drift_rel:
            CALIBRATION_DRIFT_EVENTS.labels(type="rel").inc()
            streak_rel += 1
        else:
            streak_rel = 0
        _streak_state.abs_streak = streak_abs
        _streak_state.rel_streak = streak_rel
        try:
            CALIBRATION_DRIFT_STREAK_ABS.set(streak_abs)
            CALIBRATION_DRIFT_STREAK_REL.set(streak_rel)
        except Exception:
            pass
        # Snapshot
        _streak_state.last_snapshot = {
            "ts": time.time(),
            "live_ece": live_ece,
            "live_mce": live_mce,
            "prod_ece": prod_ece,
            "delta": delta,
            "abs_threshold": abs_th,
            "rel_threshold": rel_th,
            "abs_drift": drift_abs,
            "rel_drift": drift_rel,
            "rel_gap": rel_gap,
            "sample_count": n,
            "bins": bins,
            "brier": brier,
            "status": "ok",
            "target": mon_target,
        }
        # Determine recommendation in-loop so auto-retrain doesn't rely on the status endpoint being called
        recommend_retrain = False
        now_ts = time.time()
        if cfg.calibration_monitor_enabled:
            abs_trigger = cfg.calibration_monitor_abs_streak_trigger
            rel_trigger = cfg.calibration_monitor_rel_streak_trigger
            if drift_abs and streak_abs >= abs_trigger:
                recommend_retrain = True
            if drift_rel and streak_rel >= rel_trigger:
                recommend_retrain = True
            # Heuristic: very large delta even without streak
            mult = max(0.0, float(getattr(cfg, 'calibration_abs_delta_multiplier', 2.0))) or 2.0
            if isinstance(delta, (int, float)) and delta is not None and delta >= mult * abs_th:
                recommend_retrain = True
            # Cooldown: suppress recommendations if within cooldown window
            cooldown = int(getattr(cfg, 'calibration_recommend_cooldown_seconds', 0))
            if cooldown > 0 and recommend_retrain:
                last_ts = getattr(_streak_state, 'last_recommend_ts', None)
                if isinstance(last_ts, (int, float)) and (now_ts - last_ts) < cooldown:
                    recommend_retrain = False
        # Update recommendation state and best-effort metrics
        try:
            prev = getattr(_streak_state, 'last_recommend', False)
            _streak_state.last_recommend = recommend_retrain
            if recommend_retrain:
                CALIBRATION_RETRAIN_RECOMMEND.set(1)
                if not prev:
                    CALIBRATION_RETRAIN_RECOMMEND_EVENTS.labels(state="enter").inc()
                # record last ts for cooldown gating
                setattr(_streak_state, 'last_recommend_ts', now_ts)
            else:
                CALIBRATION_RETRAIN_RECOMMEND.set(0)
                if prev:
                    CALIBRATION_RETRAIN_RECOMMEND_EVENTS.labels(state="exit").inc()
        except Exception:
            pass
        # Sleep respecting interval (subtract processing time)
        elapsed = time.perf_counter() - started
        sleep_for = interval - elapsed
        if sleep_for < 0:
            sleep_for = interval * 0.2  # fallback minimal delay to avoid tight loop
        await asyncio.sleep(sleep_for)
    # (loop never returns)

@app.get("/api/training/auto/status")
async def training_auto_status(_auth: bool = Depends(require_api_key)):
    return auto_retrain_status()

@app.get("/api/training/jobs")
async def training_jobs(limit: int = 20, _auth: bool = Depends(require_api_key)):
    repo = TrainingJobRepository()
    rows = await repo.fetch_recent(limit=limit)
    # Build an overlay map from in-memory jobs keyed by db_job_id
    in_mem_by_dbid: dict[int, dict] = {}
    try:
        for jid in _training_job_order:
            j = _training_jobs.get(jid)
            if not j:
                continue
            dbid = j.get("db_job_id")
            if isinstance(dbid, int):
                # Keep the most recent record for this db id
                in_mem_by_dbid[dbid] = j
    except Exception:
        pass
    out = []
    # Backfill helper: try to parse metrics or fetch from registry when missing
    for r in rows:
        d = {k: r[k] for k in r.keys()}
        try:
            mval = d.get("metrics")
            if isinstance(mval, str):
                import json  # local to keep import scope minimal
                try:
                    d["metrics"] = json.loads(mval)
                except Exception:
                    # leave as raw string on parse failure
                    pass
        except Exception:
            pass
        # Flatten commonly used metrics to top-level for UI convenience
        try:
            m = d.get("metrics")
            if isinstance(m, dict):
                def _set_if_absent(key: str, val):
                    if key not in d and val is not None:
                        d[key] = val
                _set_if_absent("auc", m.get("auc"))
                _set_if_absent("accuracy", m.get("accuracy"))
                _set_if_absent("ece", m.get("ece"))
                _set_if_absent("mce", m.get("mce"))
                _set_if_absent("brier", m.get("brier"))
                _set_if_absent("samples", m.get("samples"))
                _set_if_absent("val_samples", m.get("val_samples"))
                _set_if_absent("mode", m.get("mode"))
                _set_if_absent("symbol", m.get("symbol"))
                _set_if_absent("interval", m.get("interval"))
                # Normalize features list to a single key
                feats = m.get("features") or m.get("feature_set")
                if feats is not None and "features" not in d:
                    d["features"] = feats
                # Surface identity
                _set_if_absent("model_name", m.get("model_name") or m.get("name"))
                _set_if_absent("target", m.get("target"))
        except Exception:
            pass
        # Fallback to registry row when metrics missing identity
        if (not d.get("model_name") or not d.get("target")) and d.get("model_id"):
            try:
                reg = await registry_repo.fetch_by_id(int(d["model_id"]))
                if reg:
                    if not d.get("model_name"):
                        d["model_name"] = reg.get("name")
                    if not d.get("target"):
                        met = reg.get("metrics") or {}
                        tv = met.get("target") if isinstance(met, dict) else None
                        if tv is not None:
                            d["target"] = tv
            except Exception:
                pass
        # Normalize status (backend safety): if finished_at/duration present without error, ensure status reflects completion
        try:
            if (d.get("error") is None or d.get("error") == "") and (d.get("finished_at") or (isinstance(d.get("duration_seconds"),(int,float)) and float(d.get("duration_seconds") or 0) > 0)):
                if d.get("status") in ("running","completed", None, "unknown"):
                    d["status"] = "success"
        except Exception:
            pass
        # If marked running but has model_id or metrics, treat as success (defensive for missed updates)
        try:
            if d.get("status") == "running" and (d.get("model_id") or d.get("metrics")) and not d.get("error"):
                d["status"] = "success"
        except Exception:
            pass
        # Overlay with in-memory job if available for this db row id
        try:
            dbid = d.get("id")
            if isinstance(dbid, int) and dbid in in_mem_by_dbid:
                mem = in_mem_by_dbid[dbid]
                mem_status = mem.get("status")
                # Prefer explicit in-memory terminal status
                if mem_status in ("success", "error"):
                    d["status"] = mem_status
                # Carry over additional context
                if "primary_result" in mem:
                    d["primary_result"] = mem.get("primary_result")
                if "sentiment_result" in mem:
                    d["sentiment_result"] = mem.get("sentiment_result")
        except Exception:
            pass
        # Ensure version/artifact and metrics are present when possible
        # 1) Try derive version from artifact_path filename (pattern: name__VERSION.ext)
        try:
            if not d.get("version") and d.get("artifact_path") and isinstance(d.get("artifact_path"), str):
                base = str(d["artifact_path"]).split("/")[-1]
                # Extract substring after last "__" and before extension
                if "__" in base:
                    ver_part = base.split("__", 1)[-1]
                    if "." in ver_part:
                        ver_part = ver_part.rsplit(".", 1)[0]
                    if ver_part:
                        d["version"] = ver_part
        except Exception:
            pass
        # 2) If model_id exists, consult registry for missing fields regardless of metrics presence
        if d.get("model_id") and (not d.get("version") or not d.get("artifact_path") or not d.get("metrics")):
            try:
                reg = await registry_repo.fetch_by_id(int(d["model_id"]))
                if reg:
                    if not d.get("metrics") and reg.get("metrics"):
                        d["metrics"] = reg.get("metrics")
                    if not d.get("version") and reg.get("version"):
                        d["version"] = reg.get("version")
                    if not d.get("artifact_path") and reg.get("artifact_path"):
                        d["artifact_path"] = reg.get("artifact_path")
            except Exception:
                pass
        # 3) If version still missing but model_name known, try infer from registry by closest created_at
        try:
            if not d.get("version") and d.get("model_name"):
                name = str(d.get("model_name"))
                # Fetch a handful of latest entries and choose by created_at proximity
                reg_rows = await registry_repo.fetch_latest(name, "supervised", limit=6)
                if reg_rows:
                    # Parse job created_at
                    import datetime as _dt
                    job_ts = None
                    try:
                        ca = d.get("created_at")
                        if isinstance(ca, str):
                            job_ts = _dt.datetime.fromisoformat(ca.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        job_ts = None
                    best = None; best_delta = None
                    for rr in reg_rows:
                        try:
                            rd = dict(rr)
                        except Exception:
                            rd = rr  # type: ignore
                        if job_ts is not None and rd.get("created_at"):
                            try:
                                rt = rd.get("created_at")
                                rts = rt if isinstance(rt, (int,float)) else _dt.datetime.fromisoformat(str(rt).replace("Z", "+00:00")).timestamp()
                                delta = abs(float(rts) - float(job_ts))
                            except Exception:
                                delta = None
                        else:
                            delta = None
                        if best is None or (delta is not None and (best_delta is None or delta < best_delta)):
                            best = rd; best_delta = delta
                    if best and best.get("version"):
                        d["version"] = best.get("version")
                        if not d.get("artifact_path") and best.get("artifact_path"):
                            d["artifact_path"] = best.get("artifact_path")
                        # mark inferred for transparency
                        d["version_inferred"] = True
        except Exception:
            pass
        # Normalize placeholder versions like 'n/a' or empty to None for cleaner UI
        try:
            v = d.get("version")
            if isinstance(v, str) and v.strip().lower() in ("n/a", "na", "none", "", "-", "—"):
                d["version"] = None
        except Exception:
            pass
        out.append(d)
    # Include active in-memory jobs that may not have DB rows yet (so list updates immediately on retrain)
    try:
        # Use negative IDs for synthetic rows and avoid duplicates by db_job_id
        seen_ids = {int(r.get("id")) for r in out if isinstance(r.get("id"),(int,))}
        sid = -10_000
        for jid in reversed(_training_job_order):
            j = _training_jobs.get(jid)
            if not j:
                continue
            if j.get("status") != "running":
                continue
            dbid = j.get("db_job_id")
            if isinstance(dbid, int) and dbid in seen_ids:
                continue
            created_ts = float(j.get("created_ts") or time.time())
            created_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created_ts))
            out.insert(0, {
                "id": (dbid if isinstance(dbid, int) else sid),
                "status": "running",
                "trigger": j.get("trigger") or "manual",
                "created_at": created_iso,
                "finished_at": None,
                "duration_seconds": None,
                "artifact_path": None,
                "model_id": None,
                "version": None,
                "metrics": None,
                "drift_feature": None,
                "drift_z": None,
                "error": None,
            })
            if not isinstance(dbid, int):
                sid -= 1
            if len(out) >= limit:
                break
    except Exception:
        pass
    # Fallback: if no training_jobs rows, synthesize from model_registry latest so UI isn't blank
    if not out:
        try:
            names: list[str] = []
            if cfg.auto_promote_model_name:
                names.append(cfg.auto_promote_model_name)
            try:
                from backend.apps.training.auto_promotion import ALT_MODEL_CANDIDATES as _ALTS  # type: ignore
                for c in _ALTS:
                    if c not in names:
                        names.append(c)
            except Exception:
                pass
            synth: list[dict] = []
            sid = -1
            for nm in names:
                try:
                    reg_rows = await registry_repo.fetch_latest(nm, "supervised", limit=min(10, max(1, limit)))
                except Exception:
                    reg_rows = []
                for rr in reg_rows:
                    # rr may be asyncpg.Record or dict
                    try:
                        rd = dict(rr)
                    except Exception:
                        rd = rr  # type: ignore
                    m = rd.get("metrics") or {}
                    if isinstance(m, str):
                        import json
                        try:
                            m = json.loads(m)
                        except Exception:
                            pass
                    created = rd.get("created_at")
                    synth.append({
                        "id": sid,
                        "status": "success" if m else "unknown",
                        "trigger": "legacy",
                        "created_at": created or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "artifact_path": rd.get("artifact_path"),
                        "model_id": rd.get("id"),
                        "version": rd.get("version"),
                        "metrics": m,
                        "drift_feature": None,
                        "drift_z": None,
                        "error": None,
                    })
                    sid -= 1
                    if len(synth) >= limit:
                        break
                if len(synth) >= limit:
                    break
            out = synth
        except Exception:
            # if fallback fails, return empty array gracefully
            pass
    # Final normalization: map placeholder versions like 'n/a' to None for UI consistency
    try:
        for _r in out:
            v = _r.get("version")
            if isinstance(v, str) and v.strip().lower() in ("n/a", "na", "none", "", "-", "—"):
                _r["version"] = None
    except Exception:
        pass
    return out

@app.post("/api/models/register")
async def register_model(payload: RegisterModelRequest, _auth: bool = Depends(require_api_key)):
    model_id = await registry_repo.register(
        name=payload.name,
        version=payload.version,
        model_type=payload.model_type,
        status=payload.status or "staging",
        artifact_path=payload.artifact_path,
        metrics=payload.metrics,
    )
    return {"model_id": model_id}

@app.get("/api/models/latest")
async def latest_models(name: str, model_type: str, limit: int = 5):
    rows = await registry_repo.fetch_latest(name, model_type, limit)
    return [{k: v for k, v in r.items()} for r in rows]

@app.post("/api/models/{model_id}/promote")
async def promote_model(model_id: int, _auth: bool = Depends(require_api_key)):
    # Capture previous production model for audit metadata
    from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository as _RegRepo
    repo = _RegRepo()
    prev_prod_id = None
    samples_old = None
    try:
        # Determine model family (name/model_type) via the target row
        target = await repo.fetch_by_id(model_id)
        used_name = target.get("name") if target else None
        used_type = target.get("model_type") if target else "supervised"
        if used_name:
            latest = await repo.fetch_latest(used_name, used_type, limit=5)
            for r in latest:
                if r.get("status") == "production":
                    prev_prod_id = r.get("id")
                    m = r.get("metrics") or {}
                    samples_old = m.get("samples")
                    break
    except Exception:
        pass
    row = await registry_repo.promote(model_id)
    # Update production gauges immediately (best-effort)
    try:
        await refresh_production_metrics()
    except Exception:
        pass
    # Audit promotion event (manual)
    try:
        audit = LifecycleAuditRepository()
        m = (row.get("metrics") or {}) if row else {}
        samples_new = m.get("samples") if m else None
        await audit.log_promotion(
            model_id=row.get("id") if row else model_id,
            previous_production_model_id=prev_prod_id,
            decision="promoted" if row else "error",
            reason="manual_promote",
            samples_old=samples_old,
            samples_new=samples_new,
        )
    except Exception:
        pass
    return {"promoted": bool(row), "model": {k: v for k, v in row.items()} if row else None}

@app.post("/api/models/{model_id}/rollback")
async def rollback_to_model(model_id: int, _auth: bool = Depends(require_api_key)):
    """Force-activate a specific model row and demote others with same (name, model_type)."""
    repo = ModelRegistryRepository()
    row = await repo.activate(model_id)
    if not row:
        raise HTTPException(status_code=404, detail="model_not_found")
    name = row.get("name")
    mtype = row.get("model_type")
    try:
        await repo.demote_others(name, mtype, row.get("id"))
    except Exception:
        pass
    return {"status": "ok", "activated": {k: v for k, v in row.items()}}

@app.delete("/api/models/{model_id}")
async def delete_model(model_id: int, _auth: bool = Depends(require_api_key)):
    """Soft-delete a model row. Idempotent and safe.

    - If row not found: 404
    - If already deleted: return status already_deleted (200)
    - If production: 400 with reason
    - Else: delete and return 200
    """
    repo = ModelRegistryRepository()
    row = await repo.fetch_by_id(model_id)
    if not row:
        raise HTTPException(status_code=404, detail="model_not_found")
    if row.get("status") == "deleted":
        return {"status": "already_deleted", "id": model_id}
    if row.get("status") == "production":
        raise HTTPException(status_code=400, detail="cannot_delete_production_model")
    ok = await repo.soft_delete(model_id)
    return {"status": "deleted" if ok else "noop", "id": model_id}

@app.get("/api/models/production/history")
async def production_history(name: Optional[str] = None, model_type: str = "supervised", limit: int = 10, _auth: bool = Depends(require_api_key)):
    repo = ModelRegistryRepository()
    model_name = name or cfg.auto_promote_model_name
    rows = await repo.fetch_production_history(model_name, model_type, limit=limit)
    out = []
    for r in rows:
        d = {k: (v if k != "metrics" else (v or {})) for k, v in r.items()}
        out.append(d)
    return {"status": "ok", "name": model_name, "rows": out}

@app.post("/api/models/promotion/recommend")
async def promote_recommended(_auth: bool = Depends(require_api_key)):
    """추천 후보( models_summary 로직 ) 중 1순위 자동 승격.

    Returns:
      status: ok|no_candidate|already_production|error
      promoted_model: 승격된 모델 정보 (있다면)
      reason: 승격 사유 문자열 (+AUC, +ECE 등)
      deltas: {auc_delta, ece_delta, brier_delta}
    """
    repo = ModelRegistryRepository()
    # 동일 로직 재사용 위해 models_summary 일부 축약 구현 (중복 최소화)
    candidate_names = [cfg.auto_promote_model_name] if cfg.auto_promote_model_name else []
    try:
        from backend.apps.training.auto_promotion import ALT_MODEL_CANDIDATES as _ALTS  # type: ignore
        for c in _ALTS:
            if c not in candidate_names:
                candidate_names.append(c)
    except Exception:
        pass
    rows = []
    for nm in candidate_names:
        try:
            rset = await repo.fetch_latest(nm, "supervised", limit=6)
            if rset:
                rows = rset
                break
        except Exception:
            continue
    if not rows:
        # audit skipped
        try:
            await LifecycleAuditRepository().log_promotion(
                model_id=None,
                previous_production_model_id=None,
                decision="skipped",
                reason="no_models",
                samples_old=None,
                samples_new=None,
            )
        except Exception:
            pass
        return {"status": "no_candidate", "reason": "no_models"}
    prod = None
    for r in rows:
        if r.get("status") == "production":
            prod = r; break
    if not prod:
        prod = rows[0]
    prod_metrics = (prod.get("metrics") or {}) if prod else {}
    prod_auc = prod_metrics.get("auc")
    prod_brier = prod_metrics.get("brier")
    prod_ece = prod_metrics.get("ece")
    best = None; best_auc_improve = 0.0; best_deltas = {}
    for r in rows:
        if prod and r.get("id") == prod.get("id"):
            continue
        m = r.get("metrics") or {}
        cur_auc = m.get("auc"); cur_brier = m.get("brier"); cur_ece = m.get("ece")
        cur_val_samples = m.get("val_samples")
        if prod_auc is None or cur_auc is None:
            continue
        auc_delta = cur_auc - prod_auc
        brier_delta = (cur_brier - prod_brier) if (prod_brier is not None and cur_brier is not None) else None
        ece_delta = (cur_ece - prod_ece) if (prod_ece is not None and cur_ece is not None) else None
        # threshold (환경변수) 체크
        try:
            def _f(name: str, default: float) -> float:
                _v = os.getenv(name, str(default))
                if isinstance(_v, str) and '#' in _v:
                    _v = _v.split('#', 1)[0].strip()
                try:
                    return float(_v)
                except Exception:
                    return default
            max_brier_deg = _f("PROMOTION_MAX_BRIER_DEGRADATION", 0.01)
            max_ece_deg = _f("PROMOTION_MAX_ECE_DEGRADATION", 0.01)
            min_auc_imp = _f("PROMOTION_MIN_ABS_AUC_DELTA", 0.001)
            # 최소 검증 샘플 수(옵션): 기본 0이면 비활성화
            _min_vs = os.getenv("PROMOTION_MIN_VAL_SAMPLES", "0")
            if isinstance(_min_vs, str) and '#' in _min_vs:
                _min_vs = _min_vs.split('#', 1)[0].strip()
            try:
                min_val_samples = int(float(_min_vs))
            except Exception:
                min_val_samples = 0
        except Exception:
            max_brier_deg = 0.01; max_ece_deg = 0.01; min_auc_imp = 0.001; min_val_samples = 0
        # val_samples 기준이 설정된 경우 체크
        if isinstance(min_val_samples, int) and min_val_samples > 0:
            try:
                if cur_val_samples is None or int(cur_val_samples) < min_val_samples:
                    continue
            except Exception:
                continue
        if auc_delta <= 0 or auc_delta < min_auc_imp:
            continue
        if brier_delta is not None and brier_delta > max_brier_deg:
            continue
        if ece_delta is not None and ece_delta > max_ece_deg:
            continue
        if auc_delta > best_auc_improve:
            best_auc_improve = auc_delta
            best = r
            best_deltas = {"auc_delta": auc_delta, "brier_delta": brier_delta, "ece_delta": ece_delta}
    if not best:
        # audit skipped
        try:
            await LifecycleAuditRepository().log_promotion(
                model_id=None,
                previous_production_model_id=prod.get("id") if prod else None,
                decision="skipped",
                reason="no_qualified_model",
                samples_old=(prod_metrics.get("samples") if prod_metrics else None),
                samples_new=None,
            )
        except Exception:
            pass
        return {"status": "no_candidate", "reason": "no_qualified_model"}
    # 이미 production 이라면 skip
    if best.get("status") == "production":
        # audit skipped
        try:
            await LifecycleAuditRepository().log_promotion(
                model_id=best.get("id"),
                previous_production_model_id=prod.get("id") if prod else None,
                decision="skipped",
                reason="already_production",
                samples_old=(prod_metrics.get("samples") if prod_metrics else None),
                samples_new=((best.get("metrics") or {}).get("samples") if best.get("metrics") else None),
            )
        except Exception:
            pass
        return {"status": "already_production", "model_id": best.get("id")}
    row = await repo.promote(best.get("id"))  # type: ignore[arg-type]
    if not row:
        # audit error
        try:
            await LifecycleAuditRepository().log_promotion(
                model_id=best.get("id"),
                previous_production_model_id=prod.get("id") if prod else None,
                decision="error",
                reason="promote_failed",
                samples_old=(prod_metrics.get("samples") if prod_metrics else None),
                samples_new=((best.get("metrics") or {}).get("samples") if best.get("metrics") else None),
            )
        except Exception:
            pass
        return {"status": "error", "reason": "promote_failed"}
    # Refresh gauges right after promotion
    try:
        await refresh_production_metrics()
    except Exception:
        pass
    # 사유 문자열 구성
    auc_delta_fmt = f"+{best_deltas['auc_delta']:.3f}" if best_deltas.get("auc_delta") is not None else "n/a"
    def _fmt_delta(label: str, val, better_when_lower: bool):
        if val is None:
            return f"{label}: n/a"
        sign = "+" if val < 0 and better_when_lower else ("+" if val > 0 and not better_when_lower else "")
        return f"{label}: {sign}{val:.3f}" + (" (허용)" if ((better_when_lower and val <= 0) or (not better_when_lower and val >= 0)) else "")
    brier_desc = _fmt_delta("BrierΔ", best_deltas.get("brier_delta"), better_when_lower=True)
    ece_desc = _fmt_delta("ECEΔ", best_deltas.get("ece_delta"), better_when_lower=True)
    reason = f"AUC {auc_delta_fmt} / {brier_desc} / {ece_desc}"
    # audit success
    try:
        await LifecycleAuditRepository().log_promotion(
            model_id=row.get("id") if row else best.get("id"),
            previous_production_model_id=prod.get("id") if prod else None,
            decision="promoted",
            reason=reason,
            samples_old=(prod_metrics.get("samples") if prod_metrics else None),
            samples_new=((row.get("metrics") or {}).get("samples") if row and row.get("metrics") else (best.get("metrics") or {}).get("samples")),
        )
    except Exception:
        pass
    return {
        "status": "ok",
        "promoted_model": {k: v for k, v in row.items()},
        "deltas": best_deltas,
        "reason": reason,
    }

@app.post("/api/admin/models/seed/force")
async def force_seed_model(_auth: bool = Depends(require_api_key)):
    """bottom_predictor seed 모델을 강제로 재삽입 (bottom-only).

    이미 존재하면 status=exists 와 함께 기존 모델 id 반환.
    존재하지 않을 경우 보수적 seed metric 으로 production 상태로 삽입.
    """
    repo = ModelRegistryRepository()
    existing = await repo.fetch_latest("bottom_predictor", "supervised", limit=1)
    if existing:
        return {"status": "exists", "model_id": existing[0].get("id"), "version": existing[0].get("version")}
    seed_metrics = {
        "auc": 0.50,
        "accuracy": 0.50,
        "brier": 0.25,
        "ece": 0.05,
        "mce": 0.05,
        "seed_baseline": True,
        "target": "bottom",
        "model_name": "bottom_predictor",
        "name": "bottom_predictor",
    }
    try:
        model_id = await repo.register(
            name="bottom_predictor",
            version="seed",
            model_type="supervised",
            status="production",
            artifact_path=None,
            metrics=seed_metrics,
        )
        return {"status": "inserted", "model_id": model_id}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}

@app.post("/api/models/{model_id}/metrics")
async def append_metrics(model_id: int, payload: MetricsAppendRequest, _auth: bool = Depends(require_api_key)):
    await registry_repo.append_metrics(model_id, payload.metrics)
    return {"status": "ok"}

@app.post("/api/models/lineage")
async def add_lineage(parent_id: int, child_id: int, _auth: bool = Depends(require_api_key)):
    await registry_repo.add_lineage(parent_id, child_id)
    return {"status": "ok"}

@app.post("/api/risk/evaluate")
async def risk_evaluate(symbol: str, price: float, size: float, atr: Optional[float] = None, _auth: bool = Depends(require_api_key)):
    result = risk_engine.evaluate_order(symbol, price, size, atr)
    allowed = result.get("allowed")
    RISK_ORDER_EVALS.labels(allowed=str(bool(allowed)).lower()).inc()
    if not allowed:
        reasons = result.get("reasons") or []
        # increment per reason (deduplicate to avoid double counting same reason list entries)
        for r in set(reasons):
            if r:
                RISK_ORDER_REJECTS.labels(reason=r).inc()
    return result

@app.get("/api/trading/orders")
async def trading_orders(
    limit: int = 50,
    source: Optional[str] = None,
    symbol: Optional[str] = None,
    since_reset: Optional[bool] = False,
    _auth: bool = Depends(require_api_key),
):
    """List recent orders.

    Query params:
      - limit: number of rows (1-1000)
      - source: 'exchange'|'db' (optional). If 'exchange' and exchange trading is enabled,
        will return orders directly from the exchange (filled trades). Otherwise falls back to DB.
    """
    svc = get_trading_service(risk_engine)
    src = (source or "").lower().strip()
    try:
        use_exchange = bool(getattr(svc, "_use_exchange", False) and getattr(svc, "_binance", None) is not None)
    except Exception:
        use_exchange = False
    # Decide which source to use
    if src == "db":
        want_exchange = False
    elif src == "exchange":
        want_exchange = True
    else:
        # default to exchange if available
        want_exchange = use_exchange

    if want_exchange and use_exchange:
        try:
            orders = await svc.list_orders_exchange(symbol=symbol, limit=limit)
            return {"orders": orders, "limit": limit, "persistent": False, "source": "exchange"}
        except Exception:
            # fall back to DB on any error
            pass
    # DB-backed list
    # If since_reset requested and symbol provided, fetch from DB with reset boundary for consistency across Risk/Performance/Orders
    if since_reset:
        try:
            from backend.apps.risk.repository.risk_repository import RiskRepository
            from backend.apps.trading.repository.trading_repository import TradingRepository
            rrepo = RiskRepository(getattr(risk_engine, "session_key", "default_session"))
            state = await rrepo.load_state()
            reset_ts = None
            try:
                reset_ts = float(state["last_reset_ts"]) if state and state.get("last_reset_ts") is not None else None
            except Exception:
                reset_ts = None
            trepo = TradingRepository()
            if symbol and isinstance(symbol, str) and symbol.strip():
                orders = await trepo.fetch_range(symbol.strip().upper(), from_ts=reset_ts, limit=limit)
                return {"orders": orders, "limit": limit, "persistent": True, "source": "db", "since_reset": True, "symbol": symbol.strip().upper()}
        except Exception:
            # fall back to default path below
            pass
    # default DB-backed list (in-memory with DB fallback)
    return {"orders": svc.list_orders(limit=limit), "limit": limit, "persistent": True, "source": "db"}

@app.delete("/api/trading/orders/{order_id}")
async def trading_delete_order(order_id: int, _auth: bool = Depends(require_api_key)):
    svc = get_trading_service(risk_engine)
    try:
        ok = await svc.delete_order(order_id)
        return {"status": "deleted" if ok else "not_found", "id": order_id}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/trading/clear_all")
async def trading_clear_all(symbol: Optional[str] = None, _auth: bool = Depends(require_api_key)):
    """Clear all recent orders AND positions so live trading can resume cleanly.

    - Deletes rows from trading_orders (optionally by symbol)
    - Deletes all positions for the active risk session
    - Clears in-memory caches for orders and positions
    """
    svc = get_trading_service(risk_engine)
    deleted_orders = 0
    try:
        from backend.apps.trading.repository.trading_repository import TradingRepository
        repo = TradingRepository()
        deleted_orders = await repo.delete_all(symbol)
    except Exception:
        deleted_orders = 0
    # Clear positions in DB and memory
    deleted_positions = 0
    try:
        from backend.apps.risk.repository.risk_repository import RiskRepository
        rrepo = RiskRepository(risk_engine.session_key)
        deleted_positions = await rrepo.clear_positions()
    except Exception:
        deleted_positions = 0
    # Clear in-memory state
    try:
        svc._orders.clear()  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        risk_engine.positions.clear()
    except Exception:
        pass
    return {
        "status": "ok",
        "deleted_orders": int(deleted_orders),
        "deleted_positions": int(deleted_positions),
        "symbol": symbol,
    }

@app.get("/api/trading/positions")
async def trading_positions(_auth: bool = Depends(require_api_key)):
    svc = get_trading_service(risk_engine)
    return {"positions": svc.get_positions()}

@app.delete("/api/trading/positions/{symbol}")
async def trading_delete_position(symbol: str, _auth: bool = Depends(require_api_key)):
    """Delete a single position by symbol (DB and memory)."""
    try:
        from backend.apps.risk.repository.risk_repository import RiskRepository
        repo = RiskRepository(risk_engine.session_key)
        # Setting size=0 triggers a delete in repository
        await repo.save_position(symbol.upper(), 0.0, 0.0)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"delete_position_failed:{e}")
    # Clear in-memory cache
    try:
        risk_engine.positions.pop(symbol.upper(), None)
    except Exception:
        pass
    return {"status": "deleted", "symbol": symbol.upper()}

@app.get("/api/risk/state")
async def risk_state(_auth: bool = Depends(require_api_key)):
    """Return risk state sourced directly from DB (single source of truth).

    Avoids relying on in-memory session so that multiple app instances and restarts
    always reflect the persisted state.
    """
    ps = pool_status()
    if not ps.get("has_pool"):
        return {"status": "degraded", "error": "db_unavailable", "db": ps}
    try:
        from backend.apps.risk.repository.risk_repository import RiskRepository
        repo = RiskRepository(getattr(risk_engine, "session_key", "default_session"))
        state = await repo.load_state()
        positions_rows = await repo.load_positions()
        session = {
            "starting_equity": float(state["starting_equity"]) if state else None,
            "peak_equity": float(state["peak_equity"]) if state else None,
            "current_equity": float(state["current_equity"]) if state else None,
            "cumulative_pnl": float(state["cumulative_pnl"]) if state else 0.0,
            "last_reset_ts": float(state["last_reset_ts"]) if state else None,
        }
        positions = [
            {"symbol": r["symbol"], "size": float(r["size"]), "entry_price": float(r["entry_price"])}
            for r in positions_rows
        ]
        # Echo current applied risk limits so UI can reflect DB-admin settings
        try:
            limits = {
                "max_notional": float(getattr(risk_engine.limits, "max_notional", 0.0) or 0.0),
                "max_daily_loss": float(getattr(risk_engine.limits, "max_daily_loss", 0.0) or 0.0),
                "max_drawdown": float(getattr(risk_engine.limits, "max_drawdown", 0.0) or 0.0),
                "atr_multiple": float(getattr(risk_engine.limits, "atr_multiple", 0.0) or 0.0),
            }
        except Exception:
            limits = {
                "max_notional": 0.0,
                "max_daily_loss": 0.0,
                "max_drawdown": 0.0,
                "atr_multiple": 0.0,
            }
        return {"status": "ok", "session": session, "positions": positions, "limits": limits}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"risk_state_failed:{e}")

@app.get("/api/risk/positions/db")
async def risk_positions_db(_auth: bool = Depends(require_api_key)):
    """Diagnostics: List positions directly from DB for the active risk session.

    Useful to compare in-memory state vs persisted rows when investigating discrepancies.
    """
    # Ensure repository available
    try:
        if not hasattr(risk_engine, "_repo") or getattr(risk_engine, "_repo", None) is None:
            await risk_engine.load()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"risk_repo_unavailable:{e}")
    # Fetch rows via repository
    try:
        from backend.apps.risk.repository.risk_repository import RiskRepository
        repo = RiskRepository(risk_engine.session_key)
        rows = await repo.load_positions()
        out = [{"symbol": r["symbol"], "size": float(r["size"]), "entry_price": float(r["entry_price"]) } for r in rows]
        return {"session_key": risk_engine.session_key, "positions": out}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"load_positions_failed:{e}")

@app.post("/api/risk/reset_equity")
async def risk_reset_equity(starting_equity: Optional[float] = None, reset_pnl: bool = True, touch_peak: bool = True, _auth: bool = Depends(require_api_key)):
    """Reset the risk session equity baseline.

    - If starting_equity is omitted, uses env RISK_STARTING_EQUITY or falls back to current starting.
    - Resets cumulative_pnl when reset_pnl=true (default)
    - Aligns peak/current to starting when touch_peak=true (default)
    """
    target = starting_equity
    if target is None:
        env_val = os.getenv("RISK_STARTING_EQUITY")
        if env_val is not None:
            try:
                target = float(env_val)
            except Exception:
                pass
    if target is None:
        target = risk_engine.session.starting_equity
    try:
        await risk_engine.reset_equity(float(target), reset_pnl=bool(reset_pnl), touch_peak=bool(touch_peak))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"reset_failed:{e}")
    return {
        "status": "ok",
        "starting_equity": risk_engine.session.starting_equity,
        "peak_equity": risk_engine.session.peak_equity,
        "current_equity": risk_engine.session.current_equity,
        "cumulative_pnl": risk_engine.session.cumulative_pnl,
        "last_reset_ts": risk_engine.session.last_reset_ts,
    }

@app.post("/api/trading/live/enable")
async def trading_live_enable(enabled: bool, _auth: bool = Depends(require_api_key)):
    app.state.live_trading_enabled = bool(enabled)
    return {"status": "ok", "enabled": bool(enabled)}

# --- Replay-only Backtester (real filled orders) ---

def _to_iso(ts_sec: Optional[Union[float, int]]) -> Optional[str]:
    try:
        if ts_sec is None:
            return None
        import datetime as _dt
        return _dt.datetime.utcfromtimestamp(float(ts_sec)).replace(tzinfo=_dt.timezone.utc).isoformat()
    except Exception:
        return None

def _sec_from_any(v: Optional[Union[float, int]]) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
        # Heuristic: ms vs sec
        if x > 1_000_000_000_000:  # ms
            return x / 1000.0
        return x
    except Exception:
        return None

@app.get("/api/backtest/replay", dependencies=[Depends(require_api_key)])
async def backtest_replay(
    symbol: Optional[str] = None,
    from_ts: Optional[float] = None,
    to_ts: Optional[float] = None,
):
    """Replay-only backtest using real filled orders stored in trading_orders.

    - Groups buys (entry + scale-ins) into buy bundles per trade
    - Handles partial exits and trade close when position returns to zero
    - Computes realized PnL net of fees (uses configured taker/maker fee mode)
    - Returns events, buy_bundles, trades, and a summary with counters

    Query params:
      - symbol: optional; defaults to configured symbol
      - from_ts, to_ts: unix timestamp in seconds (or ms; ms auto-detected)
    """
    sym = (symbol or cfg.symbol).upper()
    f_sec = _sec_from_any(from_ts)
    t_sec = _sec_from_any(to_ts)

    # Load fee configuration via risk engine (lazy)
    fee_mode = getattr(risk_engine, "_fee_mode", None)
    if fee_mode is None:
        # trigger lazy load by evaluating a dummy order (no-op path)
        try:
            _ = risk_engine.evaluate_order(sym, price=0.0, size=0.0)
        except Exception:
            pass
    fee_mode = getattr(risk_engine, "_fee_mode", "taker") or "taker"
    fee_taker = float(getattr(risk_engine, "_fee_taker", 0.001) or 0.001)
    fee_maker = float(getattr(risk_engine, "_fee_maker", 0.001) or 0.001)
    fee_rate = fee_taker if fee_mode == "taker" else fee_maker

    # Fetch filled orders for symbol in time range
    from backend.common.db.connection import init_pool as _init_pool
    pool = await _init_pool()
    orders: list[dict] = []
    if pool is None:
        return {"status": "db_unavailable", "orders": [], "trades": [], "buy_bundles": [], "events": [], "summary": {}}
    async with pool.acquire() as conn:  # type: ignore
        # Ensure table exists (idempotent)
        try:
            from backend.apps.trading.repository.trading_repository import CREATE_SQL as _TRADING_CREATE_SQL
            await conn.execute(_TRADING_CREATE_SQL)
        except Exception:
            pass
        where_clauses = ["symbol = $1", "status = 'filled'"]
        params: list[object] = [sym]
        if f_sec is not None:
            where_clauses.append("created_ts >= to_timestamp($%d)" % (len(params) + 1))
            params.append(float(f_sec))
        if t_sec is not None:
            where_clauses.append("created_ts <= to_timestamp($%d)" % (len(params) + 1))
            params.append(float(t_sec))
        sql = (
            "SELECT id, symbol, side, size::float AS size, price::float AS price, status, "
            "extract(epoch from created_ts) AS created_ts, extract(epoch from filled_ts) AS filled_ts, reason "
            "FROM trading_orders WHERE " + " AND ".join(where_clauses) + " ORDER BY id ASC"
        )
        rows = await conn.fetch(sql, *params)
        orders = [dict(r) for r in rows]

    # Build events list (enrich timestamps)
    events: list[dict] = []
    for o in orders:
        events.append({
            **o,
            "created_ts_ms": int(o["created_ts"] * 1000) if o.get("created_ts") else None,
            "filled_ts_ms": int(o["filled_ts"] * 1000) if o.get("filled_ts") else None,
            "created_iso": _to_iso(o.get("created_ts")),
            "filled_iso": _to_iso(o.get("filled_ts")),
        })

    # Replay to build buy bundles and trades
    buy_bundles: list[dict] = []
    trades: list[dict] = []
    counters = {"buys": 0, "partials": 0, "full_exits": 0, "trades": 0}

    pos_size = 0.0
    avg_price = 0.0
    cur_bundle: Optional[dict] = None
    cur_trade_realized = 0.0  # gross realized PnL before fees
    cur_trade_fees = 0.0      # total fees (buy + sell)

    for o in orders:
        side = (o.get("side") or "").lower()
        size = float(o.get("size") or 0.0)
        price = float(o.get("price") or 0.0)
        ts = float(o.get("filled_ts") or o.get("created_ts") or 0.0)
        if side not in ("buy", "sell") or size <= 0 or price <= 0:
            continue

        if side == "buy":
            fee = size * price * fee_rate  # buy fee
            cur_trade_fees += fee
            if pos_size <= 0.0:
                # new trade begins
                cur_bundle = {
                    "symbol": sym,
                    "entries": [],
                    "start_ts": ts,
                    "start_ts_ms": int(ts * 1000),
                    "start_iso": _to_iso(ts),
                }
                buy_bundles.append(cur_bundle)
                cur_trade_realized = 0.0
                cur_trade_fees = fee  # reset and include the first buy fee
                pos_size = 0.0
                avg_price = 0.0
            # append entry
            if cur_bundle is not None:
                cur_bundle["entries"].append({
                    "ts": ts,
                    "ts_ms": int(ts * 1000),
                    "iso": _to_iso(ts),
                    "size": size,
                    "price": price,
                    "fee": fee,
                })
            counters["buys"] += 1
            # update position
            new_size = pos_size + size
            avg_price = ((avg_price * pos_size) + (price * size)) / new_size if new_size != 0 else 0.0
            pos_size = new_size
        else:  # sell
            if pos_size <= 0.0:
                # ignore orphan sell
                continue
            sell_qty = min(size, pos_size)
            fee = sell_qty * price * fee_rate  # sell fee
            cur_trade_fees += fee
            # realized pnl for this partial (gross; fees tracked separately)
            realized_gross = (price - avg_price) * sell_qty
            cur_trade_realized += realized_gross
            pos_size -= sell_qty
            # partial vs full
            if pos_size > 0:
                counters["partials"] += 1
            else:
                counters["full_exits"] += 1
                # finalize trade
                trade = {
                    "symbol": sym,
                    "start_ts": buy_bundles[-1]["start_ts"] if buy_bundles else None,
                    "start_ts_ms": buy_bundles[-1]["start_ts_ms"] if buy_bundles else None,
                    "start_iso": buy_bundles[-1]["start_iso"] if buy_bundles else None,
                    "end_ts": ts,
                    "end_ts_ms": int(ts * 1000),
                    "end_iso": _to_iso(ts),
                    "realized_pnl": cur_trade_realized,  # gross
                    "fees": cur_trade_fees,
                    "net_pnl": cur_trade_realized - cur_trade_fees,
                    "avg_entry_price": avg_price,
                }
                trades.append(trade)
                counters["trades"] += 1
                # reset for next trade
                pos_size = 0.0
                avg_price = 0.0
                cur_bundle = None
                cur_trade_realized = 0.0
                cur_trade_fees = 0.0

    # Summary
    total_realized = sum(t.get("net_pnl", 0.0) for t in trades)
    total_fees = sum(t.get("fees", 0.0) for t in trades)
    summary = {
        "symbol": sym,
        "orders": len(orders),
        "trades": len(trades),
        "buy_bundles": len(buy_bundles),
        "total_realized_net": total_realized,
        "total_fees": total_fees,
        "fee_mode": fee_mode,
        "fee_taker": fee_taker,
        "fee_maker": fee_maker,
        "window": {
            "from_ts": f_sec,
            "to_ts": t_sec,
            "from_iso": _to_iso(f_sec) if f_sec else None,
            "to_iso": _to_iso(t_sec) if t_sec else None,
        },
        "counters": counters,
    }

    return {
        "status": "ok",
        "symbol": sym,
        "events": events,
        "buy_bundles": buy_bundles,
        "trades": trades,
        "summary": summary,
    }

class LiveParamsRequest(BaseModel):
    base_size: Optional[float] = None
    cooldown_sec: Optional[int] = None
    allow_short: Optional[bool] = None
    allow_scale_in: Optional[bool] = None
    scale_in_size_ratio: Optional[float] = None
    scale_in_max_legs: Optional[int] = None
    scale_in_min_price_move: Optional[float] = None
    # Δ-gate removed; keep for backward compatibility but ignored
    scale_in_prob_delta_gate: Optional[float] = None
    scale_in_gate_mode: Optional[str] = None  # 'and' | 'or'
    scale_in_cooldown_sec: Optional[int] = None
    # new runtime toggles
    exit_bypass_cooldown: Optional[bool] = None
    exit_require_net_profit: Optional[bool] = None
    exit_force_on_net_profit: Optional[bool] = None
    exit_slice_seconds: Optional[int] = None
    exit_profit_check_mode: Optional[str] = None  # 'net' | 'gross'
    scale_in_freeze_on_exit: Optional[bool] = None
    trailing_take_profit_pct: Optional[float] = None
    max_holding_seconds: Optional[int] = None

@app.post("/api/trading/live/params")
async def trading_live_params(req: LiveParamsRequest, _auth: bool = Depends(require_api_key)):
    if req.base_size is not None and req.base_size > 0:
        app.state.live_trading_base_size = float(req.base_size)
    if req.cooldown_sec is not None and req.cooldown_sec >= 0:
        app.state.live_trading_cooldown_sec = int(req.cooldown_sec)
    if req.allow_short is not None:
        app.state.live_allow_short = bool(req.allow_short)
    # Scale-in params
    if req.allow_scale_in is not None:
        app.state.live_allow_scale_in = bool(req.allow_scale_in)
    if req.scale_in_size_ratio is not None and req.scale_in_size_ratio >= 0:
        app.state.live_scale_in_size_ratio = float(req.scale_in_size_ratio)
    if req.scale_in_max_legs is not None and req.scale_in_max_legs >= 0:
        app.state.live_scale_in_max_legs = int(req.scale_in_max_legs)
    if req.scale_in_min_price_move is not None and req.scale_in_min_price_move >= 0:
        app.state.live_scale_in_min_price_move = float(req.scale_in_min_price_move)
    # Δ-gate removed: ignore incoming value
    if req.scale_in_prob_delta_gate is not None and req.scale_in_prob_delta_gate >= 0:
        app.state.live_scale_in_prob_delta_gate = float(req.scale_in_prob_delta_gate)
    if isinstance(req.scale_in_gate_mode, str) and req.scale_in_gate_mode.lower() in ("and","or"):
        app.state.live_scale_in_gate_mode = req.scale_in_gate_mode.lower()
    if req.scale_in_cooldown_sec is not None and req.scale_in_cooldown_sec >= 0:
        app.state.live_scale_in_cooldown_sec = int(req.scale_in_cooldown_sec)
    # Exit/runtime policy toggles
    if req.exit_bypass_cooldown is not None:
        app.state.exit_bypass_cooldown = bool(req.exit_bypass_cooldown)
    if req.exit_require_net_profit is not None:
        app.state.exit_require_net_profit = bool(req.exit_require_net_profit)
    if req.exit_force_on_net_profit is not None:
        app.state.exit_force_on_net_profit = bool(req.exit_force_on_net_profit)
    if req.exit_slice_seconds is not None and req.exit_slice_seconds >= 0:
        app.state.exit_slice_seconds = int(req.exit_slice_seconds)
    if isinstance(req.exit_profit_check_mode, str) and req.exit_profit_check_mode.lower() in ("net","gross"):
        app.state.exit_profit_check_mode = req.exit_profit_check_mode.lower()
    if req.scale_in_freeze_on_exit is not None:
        app.state.live_scale_in_freeze_on_exit = bool(req.scale_in_freeze_on_exit)
    if req.trailing_take_profit_pct is not None and req.trailing_take_profit_pct >= 0:
        app.state.live_trailing_take_profit_pct = float(req.trailing_take_profit_pct)
    if req.max_holding_seconds is not None and req.max_holding_seconds >= 0:
        app.state.live_max_holding_seconds = int(req.max_holding_seconds)
    return {
        "status": "ok",
        "params": {
            "base_size": app.state.live_trading_base_size,
            "cooldown_sec": app.state.live_trading_cooldown_sec,
            "allow_short": app.state.live_allow_short,
            "allow_scale_in": bool(getattr(app.state, 'live_allow_scale_in', False)),
            "scale_in_size_ratio": float(getattr(app.state, 'live_scale_in_size_ratio', 1.0)),
            "scale_in_max_legs": int(getattr(app.state, 'live_scale_in_max_legs', 0)),
            "scale_in_min_price_move": float(getattr(app.state, 'live_scale_in_min_price_move', 0.0)),
            # Δ-gate removed; return 0 for compatibility
            "scale_in_prob_delta_gate": float(getattr(app.state, 'live_scale_in_prob_delta_gate', 0.0)),
            "scale_in_gate_mode": str(getattr(app.state, 'live_scale_in_gate_mode', 'or')),
            "scale_in_cooldown_sec": int(getattr(app.state, 'live_scale_in_cooldown_sec', getattr(app.state, 'live_trading_cooldown_sec', 60))),
            "scale_in_freeze_on_exit": bool(getattr(app.state, 'live_scale_in_freeze_on_exit', True)),
            "exit_bypass_cooldown": bool(getattr(app.state, 'exit_bypass_cooldown', True)),
            "exit_slice_seconds": int(getattr(app.state, 'exit_slice_seconds', 0)),
            "exit_require_net_profit": bool(getattr(app.state, 'exit_require_net_profit', True)),
            "exit_force_on_net_profit": bool(getattr(app.state, 'exit_force_on_net_profit', True)),
            "exit_profit_check_mode": str(getattr(app.state, 'exit_profit_check_mode', 'net')),
            "trailing_take_profit_pct": float(getattr(app.state, 'live_trailing_take_profit_pct', 0.0)),
            "max_holding_seconds": int(getattr(app.state, 'live_max_holding_seconds', 0)),
        }
    }

@app.get("/api/trading/live/status")
async def trading_live_status(source: Optional[str] = None, _auth: bool = Depends(require_api_key)):
    svc = get_trading_service(risk_engine)
    src = (source or "").lower().strip()
    try:
        use_exchange = bool(getattr(svc, "_use_exchange", False) and getattr(svc, "_binance", None) is not None)
    except Exception:
        use_exchange = False
    # Decide orders source
    if src == "db":
        want_exchange = False
    elif src == "exchange":
        want_exchange = True
    else:
        # default to exchange when available
        want_exchange = use_exchange
    orders = None
    orders_source_val = "db"
    if want_exchange and use_exchange:
        try:
            ex_orders = await svc.list_orders_exchange(limit=20)
        except Exception:
            ex_orders = None
        # If exchange returned some orders, use them; otherwise, fall back to DB so UI shows recent activity
        if isinstance(ex_orders, list) and len(ex_orders) > 0:
            orders = ex_orders
            orders_source_val = "exchange"
        else:
            orders = svc.list_orders(limit=20)
            orders_source_val = "db"
    else:
        orders = svc.list_orders(limit=20)
        orders_source_val = "db"
    return {
        "enabled": bool(getattr(app.state, 'live_trading_enabled', False)),
        "params": {
            "base_size": getattr(app.state, 'live_trading_base_size', 1.0),
            "cooldown_sec": getattr(app.state, 'live_trading_cooldown_sec', 60),
            "allow_short": getattr(app.state, 'live_allow_short', False),
            "allow_scale_in": getattr(app.state, 'live_allow_scale_in', False),
            "scale_in_size_ratio": getattr(app.state, 'live_scale_in_size_ratio', 1.0),
            "scale_in_max_legs": getattr(app.state, 'live_scale_in_max_legs', 0),
            "scale_in_min_price_move": getattr(app.state, 'live_scale_in_min_price_move', 0.0),
            # Δ-gate removed; return 0 for compatibility
            "scale_in_prob_delta_gate": float(getattr(app.state, 'live_scale_in_prob_delta_gate', 0.0)),
            "scale_in_gate_mode": str(getattr(app.state, 'live_scale_in_gate_mode', 'or')),
            "scale_in_cooldown_sec": getattr(app.state, 'live_scale_in_cooldown_sec', getattr(app.state, 'live_trading_cooldown_sec', 60)),
            "scale_in_freeze_on_exit": getattr(app.state, 'live_scale_in_freeze_on_exit', True),
            "exit_slice_seconds": getattr(app.state, 'exit_slice_seconds', 0),
            "trailing_take_profit_pct": getattr(app.state, 'live_trailing_take_profit_pct', 0.0),
            "max_holding_seconds": getattr(app.state, 'live_max_holding_seconds', 0),
            "last_trade_ts": getattr(app.state, 'live_trading_last_ts', 0.0),
            "fee_mode": cfg.trading_fee_mode,
            "fee_taker": cfg.trading_fee_taker,
            "fee_maker": cfg.trading_fee_maker,
        },
        "equity": {
            "starting": risk_engine.session.starting_equity,
            "current": risk_engine.session.current_equity,
            "peak": risk_engine.session.peak_equity,
            "cumulative_pnl": risk_engine.session.cumulative_pnl,
        },
        "positions": svc.get_positions(),
        # source indicates where orders came from
        "orders": orders,
        "orders_source": orders_source_val,
    }

@app.get("/api/trading/no_signal_breakdown")
async def trading_no_signal_breakdown(_auth: bool = Depends(require_api_key)):
    """Explain why no live order is being placed right now with numeric diagnostics.

    Returns fields:
      - cooldown_remaining_sec: seconds remaining until next order allowed (0 if free)
      - latest_inference: { ts, probability, threshold, delta, decision }
      - risk: { allowed, reasons, checks, evaluated_price, size }
      - live_enabled: bool, params echo
    """
    # Live params
    live_enabled = bool(getattr(app.state, 'live_trading_enabled', False))
    base_size = float(getattr(app.state, 'live_trading_base_size', 1.0))
    cooldown_sec = int(getattr(app.state, 'live_trading_cooldown_sec', 60))
    last_trade_ts = float(getattr(app.state, 'live_trading_last_ts', 0.0) or 0.0)
    now = time.time()
    cooldown_remaining = max(0, int(cooldown_sec - (now - last_trade_ts))) if last_trade_ts else 0
    # Latest inference for configured symbol/interval
    repo = InferenceLogRepository()
    latest = None
    try:
        row = await repo.fetch_latest_for(cfg.symbol, cfg.kline_interval)
        if row:
            try:
                ts = row.get('created_at')
                ts_num = ts.timestamp() if hasattr(ts, 'timestamp') else float(ts) if ts is not None else None
            except Exception:
                ts_num = None
            p = row.get('probability'); thr = row.get('threshold'); dec = row.get('decision')
            delta = None
            try:
                if isinstance(p, (int,float)) and isinstance(thr, (int,float)):
                    delta = float(p) - float(thr)
            except Exception:
                delta = None
            latest = {"ts": ts_num, "probability": p, "threshold": thr, "delta": delta, "decision": dec,
                      "symbol": row.get('symbol'), "interval": row.get('interval')}
        else:
            # Fallback: take the most recent log globally to help diagnose why symbol-specific latest is missing
            rows_any = await repo.fetch_recent(limit=1, offset=0)
            if rows_any:
                r0 = rows_any[0]
                try:
                    ts = r0.get('created_at')
                    ts_num = ts.timestamp() if hasattr(ts, 'timestamp') else float(ts) if ts is not None else None
                except Exception:
                    ts_num = None
                p = r0.get('probability'); thr = r0.get('threshold'); dec = r0.get('decision')
                delta = None
                try:
                    if isinstance(p, (int,float)) and isinstance(thr, (int,float)):
                        delta = float(p) - float(thr)
                except Exception:
                    delta = None
                latest = {"ts": ts_num, "probability": p, "threshold": thr, "delta": delta, "decision": dec,
                          "symbol": r0.get('symbol'), "interval": r0.get('interval'), "mismatch_hint": True}
            # If still no log available, compute a one-shot inference so UI can render donuts (no side-effects)
            if latest is None:
                try:
                    # Prefer runtime override, else config
                    thr_ov = getattr(app.state, 'auto_inference_threshold_override', None)
                    thr_use = float(thr_ov) if isinstance(thr_ov, (int,float)) and 0 < float(thr_ov) < 1 else float(getattr(load_config(), 'inference_prob_threshold', 0.5) or 0.5)
                    svc_local = _training_service()
                    res_now = await svc_local.predict_latest(threshold=thr_use)
                    if isinstance(res_now, dict) and res_now.get('status') == 'ok' and isinstance(res_now.get('probability'), (int,float)):
                        p_now = float(res_now.get('probability'))
                        thr_now = float(res_now.get('threshold')) if isinstance(res_now.get('threshold'), (int,float)) else thr_use
                        dec_now = int(res_now.get('decision')) if isinstance(res_now.get('decision'), (int,)) else (-1 if p_now < thr_now else 1)
                        delta_now = None
                        try:
                            delta_now = p_now - thr_now
                        except Exception:
                            delta_now = None
                        latest = {
                            "ts": time.time(),
                            "probability": p_now,
                            "threshold": thr_now,
                            "delta": delta_now,
                            "decision": dec_now,
                            "symbol": cfg.symbol,
                            "interval": cfg.kline_interval,
                            "computed_now": True,
                        }
                except Exception:
                    pass
    except Exception as e:  # noqa: BLE001
        latest = None
    # Current price
    cur_price = None
    try:
        recents = await _ohlcv_fetch_recent(cfg.symbol, cfg.kline_interval, limit=1)
        cur_price = float(recents[-1]['close']) if recents else None
    except Exception:
        cur_price = None
    # Risk evaluation for hypothetical base_size buy
    risk_eval = None
    if isinstance(cur_price, (int,float)):
        try:
            risk_eval = risk_engine.evaluate_order(cfg.symbol, float(cur_price), float(base_size), None)
            if isinstance(risk_eval, dict):
                risk_eval = {**risk_eval, "evaluated_price": float(cur_price), "size": float(base_size)}
        except Exception:
            risk_eval = None
    # Scale-in diagnostics
    try:
        allow_scale = bool(getattr(app.state, 'live_allow_scale_in', False))
        max_legs = int(getattr(app.state, 'live_scale_in_max_legs', 0))
        ratio = float(getattr(app.state, 'live_scale_in_size_ratio', 1.0))
        si_cool = float(getattr(app.state, 'live_scale_in_cooldown_sec', cooldown_sec))
        min_drop = float(getattr(app.state, 'live_scale_in_min_price_move', 0.0))
        # Probability delta gate (optional)
        prob_gate = float(getattr(app.state, 'live_scale_in_prob_delta_gate', 0.0))
        scale_map = getattr(app.state, 'live_scale_in', {})
        st = scale_map.get(cfg.symbol, {"legs_used": 0, "last_ts": 0.0})
        legs_used = int(st.get("legs_used", 0))
        last_si_ts = float(st.get("last_ts", 0.0))
        delta = None
        if latest and isinstance(latest.get('probability'), (int,float)) and isinstance(latest.get('threshold'), (int,float)):
            try:
                delta = float(latest['probability']) - float(latest['threshold'])
            except Exception:
                delta = None
        # Gate evaluations mirroring trading loop semantics (OR/AND across active gates) plus decision prerequisite
        entry_price = None
        try:
            pos = risk_engine.positions.get(cfg.symbol)
            if pos is not None:
                entry_price = float(pos.entry_price)
        except Exception:
            entry_price = None
        # Use anchor price if set; fall back to entry price
        try:
            anchor_price = float(st.get("anchor_price")) if st.get("anchor_price") is not None else None
        except Exception:
            anchor_price = None
        ref_price = anchor_price if isinstance(anchor_price, (int,float)) and anchor_price > 0 else entry_price
        drop = None
        gate_price_ok = True if min_drop <= 0 else False
        if isinstance(ref_price, (int,float)) and isinstance(cur_price, (int,float)) and ref_price > 0:
            try:
                drop = (float(ref_price) - float(cur_price)) / float(ref_price)
                gate_price_ok = True if min_drop <= 0 else (drop >= min_drop)
            except Exception:
                gate_price_ok = (min_drop <= 0)
        else:
            gate_price_ok = (min_drop <= 0)
        # Probability delta gate evaluation
        gate_prob_ok = True
        if isinstance(prob_gate, (int,float)) and prob_gate > 0 and isinstance(delta, (int,float)):
            gate_prob_ok = (float(delta) >= float(prob_gate))
        # Decision prerequisite: scale-in is only considered in trading loop when decision==1
        decision_ok = False
        try:
            if latest and isinstance(latest.get('decision'), (int, float)):
                decision_ok = int(latest.get('decision')) == 1
            elif isinstance(delta, (int, float)):
                decision_ok = float(delta) >= 0.0
        except Exception:
            decision_ok = False
        active_gates: list[str] = []
        gate_mode = str(getattr(app.state, 'live_scale_in_gate_mode', 'or')).lower()
        if min_drop > 0:
            active_gates.append('price')
        if isinstance(prob_gate, (int,float)) and prob_gate > 0:
            active_gates.append('prob_delta')
        # Combine gates based on mode; if none active, pass
        active_results = []
        if 'price' in active_gates:
            active_results.append(bool(gate_price_ok))
        if 'prob_delta' in active_gates:
            active_results.append(bool(gate_prob_ok))
        if not active_results:
            gate_any_ok = True
        else:
            gate_any_ok = all(active_results) if gate_mode == 'and' else any(active_results)
        # Evaluate readiness and block reasons
        pos_size = 0.0
        try:
            pos = risk_engine.positions.get(cfg.symbol)
            pos_size = float(pos.size) if pos is not None else 0.0
        except Exception:
            pos_size = 0.0
        cooldown_ok = True
        try:
            cooldown_ok = (last_si_ts == 0.0) or ((now - last_si_ts) >= si_cool)
        except Exception:
            cooldown_ok = True
        ready = bool(
            allow_scale and
            (max_legs > 0) and
            (pos_size > 0) and
            (legs_used < max_legs) and
            cooldown_ok and
            bool(gate_any_ok) and
            bool(decision_ok)
        )
        blocked_reasons: list[str] = []
        if not allow_scale:
            blocked_reasons.append('disabled')
        if max_legs <= 0:
            blocked_reasons.append('no_legs_config')
        if pos_size <= 0:
            blocked_reasons.append('no_position')
        if legs_used >= max_legs and max_legs > 0:
            blocked_reasons.append('max_legs_reached')
        if not cooldown_ok:
            blocked_reasons.append('cooldown')
        if not decision_ok:
            blocked_reasons.append('decision')
        # Only add gate block reasons if OR across active gates fails
        gate_block_reasons: list[str] = []
        if min_drop > 0 and not gate_price_ok:
            gate_block_reasons.append('price_gate')
        if 'prob_delta' in active_gates and not gate_prob_ok:
            gate_block_reasons.append('prob_delta_gate')
        if not gate_any_ok:
            blocked_reasons.extend(gate_block_reasons)
        scale_in = {
            "allow": allow_scale,
            "max_legs": max_legs,
            "ratio": ratio,
            "cooldown_sec": si_cool,
            "min_price_move": min_drop,
            "prob_delta_gate": float(prob_gate),
            "state": {"legs_used": legs_used, "last_ts": last_si_ts, "anchor_price": (float(anchor_price) if isinstance(anchor_price, (int,float)) else None), "cooldown_remaining": max(0, int(si_cool - (now - last_si_ts))) if last_si_ts else 0},
            "delta": delta,
            "gates": {
                "gate_price_ok": bool(gate_price_ok),
                "gate_prob_ok": bool(gate_prob_ok),
                "gate_any_ok": bool(gate_any_ok),
                "gate_mode": gate_mode,
                "decision_ok": bool(decision_ok),
                "active_gates": active_gates,
                "entry_price": entry_price,
                "anchor_price": (float(anchor_price) if isinstance(anchor_price, (int,float)) else None),
                "current_price": cur_price,
                "price_drop": drop,
            },
            "position_size": pos_size,
            "ready": ready,
            "blocked_reasons": blocked_reasons,
        }
    except Exception:
        scale_in = None
    # Exit diagnostics (decision-based, trailing TP, max holding)
    try:
        # Position context
        pos = None
        pos_size = 0.0
        entry_price = None
        try:
            pos = risk_engine.positions.get(cfg.symbol)
            if pos is not None:
                pos_size = float(pos.size)
                entry_price = float(pos.entry_price)
        except Exception:
            pos = None
        # Config
        try:
            fee_mode = (cfg.trading_fee_mode or 'taker').lower()
            fee_rate = float(cfg.trading_fee_taker) if fee_mode == 'taker' else float(cfg.trading_fee_maker)
        except Exception:
            fee_rate = 0.001
        trailing_pct = float(getattr(app.state, 'live_trailing_take_profit_pct', 0.0) or 0.0)
        max_hold_sec = int(getattr(app.state, 'live_max_holding_seconds', 0) or 0)
        exit_bypass_cd = bool(getattr(app.state, 'exit_bypass_cooldown', True))
        require_net = bool(getattr(app.state, 'exit_require_net_profit', True))
        exit_allow_on_hold = bool(getattr(app.state, 'exit_allow_profit_take_on_hold', True))
        cooldown_sec_exit = int(getattr(app.state, 'live_trading_cooldown_sec', 60) or 60)
        last_trade_ts_exit = float(getattr(app.state, 'live_trading_last_ts', 0.0) or 0.0)
        # Breakeven and open pnl
        breakeven = None
        if isinstance(entry_price, (int,float)) and entry_price > 0:
            breakeven = float(entry_price) * (1.0 + 2.0 * max(0.0, float(fee_rate)))
        open_pnl = None
        try:
            if isinstance(cur_price, (int,float)) and isinstance(entry_price, (int,float)) and isinstance(pos_size, (int,float)):
                open_pnl = (float(cur_price) - float(entry_price)) * float(pos_size)
        except Exception:
            open_pnl = None
        # Decision path
        decision_ok = False
        try:
            if latest and isinstance(latest.get('decision'), (int,float)):
                decision_ok = int(latest.get('decision')) in (0, -1)
            elif latest and isinstance(latest.get('delta'), (int,float)):
                decision_ok = float(latest.get('delta')) < 0.0
        except Exception:
            decision_ok = False
        net_profit_ok = True
        if require_net:
            try:
                net_profit_ok = isinstance(cur_price, (int,float)) and isinstance(breakeven, (int,float)) and float(cur_price) >= float(breakeven)
            except Exception:
                net_profit_ok = False
        cooldown_ok_exit = True
        try:
            cooldown_ok_exit = exit_bypass_cd or (last_trade_ts_exit == 0.0) or ((now - last_trade_ts_exit) >= cooldown_sec_exit)
        except Exception:
            cooldown_ok_exit = True
        price_to_breakeven_abs = None
        price_to_breakeven_pct = None
        try:
            if isinstance(cur_price, (int,float)) and isinstance(breakeven, (int,float)):
                if float(cur_price) < float(breakeven):
                    price_to_breakeven_abs = float(breakeven) - float(cur_price)
                    price_to_breakeven_pct = (price_to_breakeven_abs / float(cur_price)) if float(cur_price) > 0 else None
                else:
                    price_to_breakeven_abs = 0.0
                    price_to_breakeven_pct = 0.0
        except Exception:
            price_to_breakeven_abs = None
            price_to_breakeven_pct = None
        # New policy: allow exit on HOLD if net-profit is satisfied
        decision_ready = bool(pos_size > 0 and ((decision_ok and net_profit_ok and cooldown_ok_exit) or (exit_allow_on_hold and net_profit_ok and cooldown_ok_exit)))
        # Trailing path
        trail_map = getattr(app.state, 'live_trailing', {})
        st_trail = trail_map.get(cfg.symbol)
        peak = None
        entry_ts = None
        try:
            if isinstance(st_trail, dict):
                if st_trail.get('peak') is not None:
                    peak = float(st_trail.get('peak'))
                if st_trail.get('entry_ts') is not None:
                    entry_ts = float(st_trail.get('entry_ts'))
        except Exception:
            pass
        pullback = None
        try:
            if isinstance(peak, (int,float)) and isinstance(cur_price, (int,float)) and float(peak) > 0:
                pullback = (float(peak) - float(cur_price)) / float(peak)
        except Exception:
            pullback = None
        trailing_enabled = trailing_pct > 0
        trailing_ready = bool(
            pos_size > 0 and trailing_enabled and isinstance(cur_price, (int,float)) and isinstance(breakeven, (int,float)) and float(cur_price) >= float(breakeven) and isinstance(pullback, (int,float)) and float(pullback) >= float(trailing_pct)
        )
        trailing_remaining = None
        try:
            if trailing_enabled and isinstance(pullback, (int,float)):
                trailing_remaining = max(0.0, float(trailing_pct) - float(pullback))
        except Exception:
            trailing_remaining = None
        # Max holding path
        age_sec = None
        try:
            if isinstance(entry_ts, (int,float)) and entry_ts > 0:
                age_sec = max(0, int(now - float(entry_ts)))
        except Exception:
            age_sec = None
        max_hold_enabled = max_hold_sec > 0
        max_hold_ready = bool(pos_size > 0 and max_hold_enabled and isinstance(age_sec, (int,)) and int(age_sec) >= int(max_hold_sec))
        hold_remaining_sec = None
        try:
            if max_hold_enabled and isinstance(age_sec, (int,)):
                hold_remaining_sec = max(0, int(max_hold_sec) - int(age_sec))
        except Exception:
            hold_remaining_sec = None
        # Overall
        exit_ready = bool(decision_ready or trailing_ready or max_hold_ready)
        # Reasons
        blocked_reasons: list[str] = []
        if pos_size <= 0:
            blocked_reasons.append('no_position')
        # Decision-path specific blocks
        if pos_size > 0 and not decision_ready:
            if not decision_ok:
                blocked_reasons.append('decision')
            if require_net and not net_profit_ok:
                blocked_reasons.append('net_profit')
            if not cooldown_ok_exit and not exit_bypass_cd:
                blocked_reasons.append('cooldown')
        # Trailing blocks
        if pos_size > 0 and trailing_enabled and not trailing_ready:
            if not (isinstance(cur_price, (int,float)) and isinstance(breakeven, (int,float)) and float(cur_price) >= float(breakeven)):
                blocked_reasons.append('trailing_net_profit')
            elif not (isinstance(pullback, (int,float)) and float(pullback) >= float(trailing_pct)):
                blocked_reasons.append('trailing_pullback')
        # Max-hold blocks
        if pos_size > 0 and max_hold_enabled and not max_hold_ready:
            blocked_reasons.append('holding_time')
        exit_diag = {
            "position_size": pos_size,
            "entry_price": entry_price,
            "current_price": cur_price,
            "breakeven_price": breakeven,
            "open_pnl": open_pnl,
            "triggers": {
                "decision": {
                    "enabled": True,
                    "decision_ok": bool(decision_ok),
                    "require_net_profit": bool(require_net),
                    "net_profit_ok": bool(net_profit_ok),
                    "cooldown_ok": bool(cooldown_ok_exit),
                    "cooldown_remaining": (0 if cooldown_ok_exit else max(0, int(cooldown_sec_exit - (now - last_trade_ts_exit))) if last_trade_ts_exit else 0),
                    "price_to_breakeven": price_to_breakeven_abs,
                    "price_to_breakeven_pct": price_to_breakeven_pct,
                    "allow_on_hold": bool(exit_allow_on_hold),
                    "ready": bool(decision_ready),
                },
                "trailing": {
                    "enabled": bool(trailing_enabled),
                    "trailing_pct": float(trailing_pct),
                    "peak": peak,
                    "pullback": pullback,
                    "remaining": trailing_remaining,
                    "ready": bool(trailing_ready),
                },
                "max_hold": {
                    "enabled": bool(max_hold_enabled),
                    "max_hold_sec": int(max_hold_sec),
                    "age_sec": age_sec,
                    "remaining_sec": hold_remaining_sec,
                    "ready": bool(max_hold_ready),
                },
            },
            "ready": bool(exit_ready),
            "blocked_reasons": blocked_reasons,
        }
    except Exception:
        exit_diag = None
    # Thresholds for UI fallback clarity
    thr_cfg = float(getattr(load_config(), "inference_prob_threshold", 0.5) or 0.0)
    thr_override = (float(getattr(app.state, 'auto_inference_threshold_override', 0.0)) if isinstance(getattr(app.state, 'auto_inference_threshold_override', None), (int,float)) else None)
    # Provide expected vs actual symbols in mismatch cases for easier debugging
    mismatch_info = None
    try:
        if latest and latest.get('symbol') and latest.get('interval'):
            exp_sym = cfg.symbol
            exp_itv = cfg.kline_interval
            if str(latest.get('symbol')).upper() != str(exp_sym).upper() or str(latest.get('interval')) != str(exp_itv):
                mismatch_info = {"expected_symbol": exp_sym, "expected_interval": exp_itv, "actual_symbol": latest.get('symbol'), "actual_interval": latest.get('interval')}
    except Exception:
        mismatch_info = None
    return {
        "live_enabled": live_enabled,
        "params": {"base_size": base_size, "cooldown_sec": cooldown_sec, "last_trade_ts": last_trade_ts},
        "cooldown_remaining_sec": cooldown_remaining,
        "latest_inference": latest,
        "scale_in": scale_in,
        "exit": exit_diag,
        "risk": risk_eval,
        # Provide threshold config/override so UI can render buy/exit donuts even before first log
        "threshold_config": thr_cfg,
        "threshold_override": thr_override,
        "inference_symbol_mismatch": mismatch_info,
    }

class LowBuyPreviewRequest(BaseModel):
    lookback: int = Field(50, ge=5, le=500)
    distance_pct: float = Field(0.005, gt=0, le=0.1, description="허용 거리 비율")
    min_drop_pct: float = Field(0.01, ge=0, le=0.5, description="최고가 대비 최소 하락 비율")
    volume_ratio: Optional[float] = Field(default=None, gt=0, le=10, description="현재 거래량/평균 거래량 최소 비율")
    rsi_max: Optional[float] = Field(default=None, ge=0, le=100, description="허용 RSI 최대값")
    symbol: Optional[str] = Field(default=None, description="기본 심볼 override")
    interval: Optional[str] = Field(default=None, description="기본 인터벌 override")


class LowBuyTriggerRequest(LowBuyPreviewRequest):
    auto_execute: bool = Field(default=True, description="조건 충족 시 자동 매수 실행")
    size: Optional[float] = Field(default=None, gt=0, description="주문 수량(미설정 시 기본값)")
    reason: Optional[str] = Field(default=None, description="주문 사유 태그")


@app.post("/api/trading/low_buy/preview")
async def trading_low_buy_preview(req: LowBuyPreviewRequest, _auth: bool = Depends(require_api_key)):
    if not LOW_BUY_ENABLED:
        raise HTTPException(status_code=404, detail="low_buy_disabled")
    svc = get_low_buy_service()
    return await svc.evaluate(req.dict(exclude_none=True))


@app.post("/api/trading/low_buy/trigger")
async def trading_low_buy_trigger(req: LowBuyTriggerRequest, _auth: bool = Depends(require_api_key)):
    if not LOW_BUY_ENABLED:
        raise HTTPException(status_code=404, detail="low_buy_disabled")
    svc = get_low_buy_service()
    params = req.dict(exclude_none=True)
    auto_execute = params.pop("auto_execute", True)
    size = params.pop("size", None)
    reason = params.pop("reason", None)
    return await svc.trigger(params, auto_execute=bool(auto_execute), size=size, reason=reason)


# ML signal endpoints
class MLPreviewRequest(BaseModel):
    debug: bool = False

class MLTriggerRequest(BaseModel):
    auto_execute: bool = True
    size: Optional[float] = None
    reason: Optional[str] = None

@app.post("/api/trading/ml/preview")
async def trading_ml_preview(_req: MLPreviewRequest, _auth: bool = Depends(require_api_key)):
    from backend.apps.trading.service.ml_signal_service import MLSignalService
    svc = MLSignalService(risk_engine, autopilot=_autopilot_service)
    return await svc.evaluate()

@app.post("/api/trading/ml/trigger")
async def trading_ml_trigger(req: MLTriggerRequest, _auth: bool = Depends(require_api_key)):
    from backend.apps.trading.service.ml_signal_service import MLSignalService
    svc = MLSignalService(risk_engine, autopilot=_autopilot_service)
    return await svc.trigger(auto_execute=bool(req.auto_execute), size=req.size, reason=req.reason)

# Admin: Reset ML cooldown (clear last emit timestamp)
@app.post("/admin/ml/cooldown/reset", dependencies=[Depends(require_api_key)])
async def admin_ml_cooldown_reset(symbol: Optional[str] = None):
    """Reset ML signal cooldown timestamp so next trigger isn't throttled.

    Args:
      symbol: optional symbol to reset; defaults to current cfg.symbol
    """
    try:
        from backend.apps.trading.service import ml_signal_service as _mls
        sym = symbol or cfg.symbol
        # Clear specific symbol or all
        if sym and isinstance(sym, str):
            try:
                _mls._LAST_EMIT_TS.pop(sym, None)  # type: ignore[attr-defined]
            except Exception:
                pass
        else:
            try:
                _mls._LAST_EMIT_TS.clear()  # type: ignore[attr-defined]
            except Exception:
                pass
        return {"status": "ok", "cleared": sym or "all"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"cooldown_reset_failed:{e}")


class SubmitOrderRequest(BaseModel):
    side: str
    size: float

@app.post("/api/trading/submit")
async def trading_submit(req: SubmitOrderRequest, _auth: bool = Depends(require_api_key)):
    # get latest price
    try:
        recents = await _ohlcv_fetch_recent(cfg.symbol, cfg.kline_interval, limit=1)
        price = float(recents[-1]['close']) if recents else None
    except Exception:
        price = None
    if not price:
        raise HTTPException(status_code=503, detail="price_unavailable")
    svc = get_trading_service(risk_engine)
    out = await svc.submit_market(symbol=cfg.symbol, side=req.side, size=float(req.size), price=price)
    return out

# Lightweight diagnostic endpoint to verify exchange connectivity and signed request health
@app.get("/api/trading/exchange/diagnose")
async def trading_exchange_diagnose(_auth: bool = Depends(require_api_key)):
    svc = get_trading_service(risk_engine)
    use_exchange = bool(getattr(svc, "_use_exchange", False) and getattr(svc, "_binance", None) is not None)
    info: dict[str, Any] = {
        "use_exchange": use_exchange,
        "exchange": getattr(load_config(), "exchange", None),
        "binance_testnet": getattr(load_config(), "binance_testnet", None),
        "account_type": getattr(load_config(), "binance_account_type", None),
        "symbol": getattr(load_config(), "symbol", None),
    }
    # Try an unsigned public request first (price)
    price_val = None
    try:
        if use_exchange and getattr(svc, "_binance", None):
            price_val = await svc._binance.get_price(info["symbol"])  # type: ignore[attr-defined]
    except Exception as e:
        price_val = None
        info["public_error"] = str(e)
    info["price"] = price_val
    # Try a signed request (myTrades) to validate API key/permissions
    signed_ok = False
    signed_error = None
    signed_code = None
    try:
        if use_exchange and getattr(svc, "_binance", None):
            _ = await svc._binance.my_trades(info["symbol"], limit=1)  # type: ignore[attr-defined]
            signed_ok = True
    except Exception as e:  # surface BinanceApiError fields when possible
        signed_ok = False
        signed_error = getattr(e, "msg", None) or str(e)
        signed_code = getattr(e, "code", None)
    info["signed_ok"] = signed_ok
    if not signed_ok:
        info["signed_error"] = signed_error
        info["signed_code"] = signed_code
    return info

def _compute_atr(candles: list[dict[str, float]], period: int = 14) -> Optional[float]:
    try:
        if len(candles) < period + 1:
            return None
        trs: list[float] = []
        prev_close = float(candles[0]["close"])
        for i in range(1, len(candles)):
            c = candles[i]
            high = float(c["high"]); low = float(c["low"]); close = float(c["close"])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
            prev_close = close
        if len(trs) < period:
            return None
        # simple moving average of TR over last 'period'
        window = trs[-period:]
        return float(sum(window) / len(window))
    except Exception:
        return None

def _quantile(values: list[float], q: float) -> Optional[float]:
    if not values:
        return None
    q = max(0.0, min(1.0, q))
    arr = sorted(values)
    idx = int(round((len(arr) - 1) * q))
    try:
        return float(arr[idx])
    except Exception:
        return None

@app.get("/api/trading/scale_in/recommend")
async def scale_in_recommend(window_seconds: int = 86_400, q_delta: float = 0.65, risk_fraction: float = 0.007, _auth: bool = Depends(require_api_key)):
    """Recommend scale-in parameters using recent inference logs and ATR.

    - window_seconds: lookback window for inference logs
    - q_delta: quantile for Δ gate when using wins (fallback uses this too)
    - risk_fraction: fraction of equity to risk per trade for sizing heuristics
    """
    now_ts = time.time()
    # Current price and ATR
    try:
        kl = await _ohlcv_fetch_recent(cfg.symbol, cfg.kline_interval, limit=200)
    except Exception:
        kl = []
    cur_price = float(kl[-1]["close"]) if kl else None
    atr14 = _compute_atr(list(reversed(kl)), 14) if kl else None  # reversed -> chronological for TR calc

    # Inference deltas
    repo = InferenceLogRepository()
    try:
        rows = await repo.fetch_recent(limit=2000, offset=0)
    except Exception:
        rows = []
    deltas_win: list[float] = []
    deltas_pos: list[float] = []
    t_cut = now_ts - float(window_seconds)
    for r in rows:
        try:
            if r.get("symbol") != cfg.symbol or r.get("interval") != cfg.kline_interval:
                continue
            ts = r.get("created_at")
            ts_num = ts.timestamp() if hasattr(ts, 'timestamp') else None
            if ts_num is None or ts_num < t_cut:
                continue
            p = r.get("probability"); thr = r.get("threshold"); dec = r.get("decision")
            if not isinstance(p, (int,float)) or not isinstance(thr, (int,float)):
                continue
            d = float(p) - float(thr)
            if int(dec) == 1:
                deltas_pos.append(d)
                realized = r.get("realized")
                if realized is not None:
                    try:
                        if int(realized) == 1:
                            deltas_win.append(d)
                    except Exception:
                        pass
        except Exception:
            continue

    # Δ gate recommendation
    delta_gate = None
    if len(deltas_win) >= 50:
        delta_gate = _quantile(deltas_win, q_delta)
    elif len(deltas_pos) >= 50:
        delta_gate = _quantile(deltas_pos, max(0.6, q_delta))
    if delta_gate is None:
        delta_gate = 0.012

    # min move recommendation (ATR anchored)
    min_move = 0.003
    try:
        if isinstance(atr14, (int,float)) and isinstance(cur_price, (int,float)) and cur_price > 0:
            min_move = max(min_move, 0.5 * float(atr14) / float(cur_price))
    except Exception:
        pass

    # cooldown recommendation from decision==1 intervals
    cooldown_sec = max(2 * int(cfg.inference_auto_loop_interval), 30)
    try:
        # extract sorted timestamps for positive decisions
        ts_list = sorted([
            (r.get("created_at").timestamp() if hasattr(r.get("created_at"), 'timestamp') else None)
            for r in rows
            if r.get("symbol") == cfg.symbol and r.get("interval") == cfg.kline_interval and int(r.get("decision") or 0) == 1
        ])
        ts_list = [t for t in ts_list if isinstance(t, (int,float)) and (t >= t_cut)]
        if len(ts_list) >= 5:
            gaps = [ts_list[i] - ts_list[i-1] for i in range(1, len(ts_list))]
            gaps = [g for g in gaps if g > 0]
            if gaps:
                gaps_sorted = sorted(gaps)
                cooldown_sec = max(cooldown_sec, int(gaps_sorted[len(gaps_sorted)//2]))  # median
    except Exception:
        pass

    # size ratio and max legs via risk budget (heuristic)
    equity = float(getattr(risk_engine.session, 'current_equity', 0.0) or 0.0)
    atr_multiple = float(getattr(risk_engine.limits, 'atr_multiple', 2.0) or 2.0)
    risk_budget = equity * float(risk_fraction)
    # propose L and r to keep total risk under budget, given size_base from risk budget itself
    rec_L = 2
    rec_r = 0.5
    try:
        if isinstance(cur_price, (int,float)) and isinstance(atr14, (int,float)) and cur_price > 0 and atr14 > 0 and risk_budget > 0:
            stop_dist = atr_multiple * float(atr14)
            # base size from risk budget for single leg
            size_base = risk_budget / (stop_dist * float(cur_price))
            # search L in [2,3,4] and r in [0.4..0.7]
            best = None
            for L in (2,3,4):
                for r in (0.4,0.5,0.6,0.67):
                    geom = sum(r**k for k in range(L))
                    total_risk = geom * size_base * stop_dist * float(cur_price)
                    if total_risk <= risk_budget:
                        cand = (L, r)
                        if best is None:
                            best = cand
                        else:
                            # prefer larger r then larger L within budget
                            if cand[1] > best[1] or (cand[1] == best[1] and cand[0] > best[0]):
                                best = cand
            if best:
                rec_L, rec_r = best
    except Exception:
        pass

    return {
        "symbol": cfg.symbol,
        "interval": cfg.kline_interval,
        "recommendation": {
            "scale_in_size_ratio": float(rec_r),
            "scale_in_max_legs": int(rec_L),
            "scale_in_min_price_move": float(min_move),
            # Δ-gate removed; keep field with 0.0 for compatibility
            "scale_in_prob_delta_gate": 0.0,
            "scale_in_cooldown_sec": int(cooldown_sec),
        },
        "context": {
            "atr_14": float(atr14) if isinstance(atr14, (int,float)) else None,
            "current_price": float(cur_price) if isinstance(cur_price, (int,float)) else None,
            "equity": equity,
            "risk_fraction": float(risk_fraction),
            "delta_samples_win": len(deltas_win),
            "delta_samples_pos": len(deltas_pos),
            "q_delta": float(q_delta),
            "cooldown_median_sec": cooldown_sec,
            "window_seconds": int(window_seconds),
        }
    }

@app.get("/api/training/retrain/events")
async def retrain_events(limit: int = 100, offset: int = 0, _auth: bool = Depends(require_api_key)):
    repo = LifecycleAuditRepository()
    try:
        rows = await repo.fetch_retrain_events(limit=limit, offset=offset)
    except Exception as e:
        logger.warning("fetch_retrain_events failed: %s", e)
        rows = []
    return {"events": [ {k: r[k] for k in r.keys()} for r in rows ], "limit": limit, "offset": offset}

@app.get("/api/models/promotion/events")
async def promotion_events(limit: int = 100, offset: int = 0, _auth: bool = Depends(require_api_key)):
    repo = LifecycleAuditRepository()
    try:
        rows = await repo.fetch_promotion_events(limit=limit, offset=offset)
    except Exception as e:
        logger.warning("fetch_promotion_events failed: %s", e)
        rows = []
    events: list[dict[str, Any]] = []
    for r in rows:
        item = {k: r[k] for k in r.keys()}
        reason = item.get("reason") or ""
        auc_improve = None
        ece_delta = None
        val_samples = None
        auc_ratio = None  # auc_improve / required_min_auc_delta
        ece_margin_ratio = None  # (max_allowed_ece_delta - actual_ece_delta) / max_allowed_ece_delta (>=0 good)
        # Patterns we expect:
        #   auc+0.0123_ece_delta-0.0010_val1234
        #   criteria_not_met_auc0.0010_ece_delta0.0050_val900
        #   insufficient_val_samples:500
        try:
            if reason.startswith("auc+"):
                # split underscores
                parts = reason.split("_")
                for p in parts:
                    if p.startswith("auc+"):
                        try: auc_improve = float(p[4:])
                        except Exception: pass
                    elif p.startswith("ece_delta"):
                        try: ece_delta = float(p.replace("ece_delta",""))
                        except Exception: pass
                    elif p.startswith("val"):
                        vs = p[3:]
                        if vs.isdigit(): val_samples = int(vs)
            elif reason.startswith("criteria_not_met_"):
                parts = reason.split("_")
                for p in parts:
                    if p.startswith("auc") and not p.startswith("auc+"):
                        try: auc_improve = float(p.replace("auc",""))
                        except Exception: pass
                    elif p.startswith("ece_delta"):
                        try: ece_delta = float(p.replace("ece_delta",""))
                        except Exception: pass
                    elif p.startswith("val"):
                        vs = p[3:]
                        if vs.isdigit(): val_samples = int(vs)
            elif reason.startswith("insufficient_val_samples:"):
                try:
                    vs = reason.split(":",1)[1]
                    if vs.isdigit(): val_samples = int(vs)
                except Exception:
                    pass
        except Exception:
            pass
        item.update({
            "auc_improve": auc_improve,
            "ece_delta": ece_delta,
            "val_samples": val_samples,
            "auc_ratio": auc_ratio if auc_ratio is not None else ( (auc_improve / cfg.training_promote_min_auc_delta) if (isinstance(auc_improve,(int,float)) and auc_improve is not None and cfg.training_promote_min_auc_delta>0) else None),
            "ece_margin_ratio": ece_margin_ratio if ece_margin_ratio is not None else ( ((cfg.training_promote_max_ece_delta - ece_delta)/cfg.training_promote_max_ece_delta) if (isinstance(ece_delta,(int,float)) and ece_delta is not None and cfg.training_promote_max_ece_delta!=0) else None),
        })
        events.append(item)
    return {"events": events, "limit": limit, "offset": offset}

@app.get("/api/models/promotion/summary")
async def promotion_summary(window: int = 200, _auth: bool = Depends(require_api_key)):
    """Aggregate summary over the most recent promotion decisions.

    window: number of recent events to include (capped to 1000 for safety)
    Returns promotion_rate, counts, avg/median deltas, sample stats.
    """
    if window <= 0:
        window = 200
    if window > 1000:
        window = 1000
    repo = LifecycleAuditRepository()
    try:
        rows = await repo.fetch_promotion_events(limit=window, offset=0)
    except Exception as e:  # noqa: BLE001
        logger.warning("promotion_summary fetch failed: %s", e)
        rows = []
    total = len(rows)
    promoted = 0
    skipped = 0
    errors = 0
    auc_vals: list[float] = []
    ece_vals: list[float] = []
    val_samples: list[int] = []
    auc_ratios: list[float] = []
    ece_margin_ratios: list[float] = []
    for r in rows:
        decision = r.get("decision")
        reason = r.get("reason") or ""
        if decision == "promoted":
            promoted += 1
        elif decision == "skipped":
            skipped += 1
        else:
            errors += 1
        # reuse parsing logic (duplicated small routine for isolation)
        try:
            auc_improve = None
            ece_delta = None
            vs = None
            if reason.startswith("auc+"):
                parts = reason.split("_")
                for p in parts:
                    if p.startswith("auc+"):
                        try: auc_improve = float(p[4:])
                        except Exception: pass
                    elif p.startswith("ece_delta"):
                        try: ece_delta = float(p.replace("ece_delta",""))
                        except Exception: pass
                    elif p.startswith("val"):
                        s = p[3:]
                        if s.isdigit(): vs = int(s)
            elif reason.startswith("criteria_not_met_"):
                parts = reason.split("_")
                for p in parts:
                    if p.startswith("auc") and not p.startswith("auc+"):
                        try: auc_improve = float(p.replace("auc",""))
                        except Exception: pass
                    elif p.startswith("ece_delta"):
                        try: ece_delta = float(p.replace("ece_delta",""))
                        except Exception: pass
                    elif p.startswith("val"):
                        s = p[3:]
                        if s.isdigit(): vs = int(s)
            elif reason.startswith("insufficient_val_samples:"):
                try:
                    s = reason.split(":",1)[1]
                    if s.isdigit(): vs = int(s)
                except Exception:
                    pass
            if isinstance(auc_improve, (int,float)) and auc_improve == auc_improve:
                auc_vals.append(float(auc_improve))
                if cfg.training_promote_min_auc_delta > 0:
                    try:
                        auc_ratios.append(float(auc_improve)/cfg.training_promote_min_auc_delta)
                    except Exception: pass
            if isinstance(ece_delta, (int,float)) and ece_delta == ece_delta:
                ece_vals.append(float(ece_delta))
                if cfg.training_promote_max_ece_delta != 0:
                    try:
                        ece_margin_ratios.append((cfg.training_promote_max_ece_delta - ece_delta)/cfg.training_promote_max_ece_delta)
                    except Exception: pass
            if isinstance(vs, int):
                val_samples.append(vs)
        except Exception:
            pass
    def _avg(arr: list[float]) -> Optional[float]:
        return sum(arr)/len(arr) if arr else None
    def _median(arr: list[float]) -> Optional[float]:
        if not arr: return None
        s = sorted(arr)
        n = len(s)
        mid = n//2
        if n % 2:
            return s[mid]
        return (s[mid-1] + s[mid]) / 2
    summary = {
        "window": window,
        "total": total,
        "promoted": promoted,
        "skipped": skipped,
        "errors": errors,
        "promotion_rate": (promoted/total) if total else None,
        "auc_improve_avg": _avg(auc_vals),
        "auc_improve_median": _median(auc_vals),
        "ece_delta_avg": _avg(ece_vals),
        "ece_delta_median": _median(ece_vals),
        "val_samples_avg": _avg([float(v) for v in val_samples]) if val_samples else None,
        "val_samples_median": _median([float(v) for v in val_samples]) if val_samples else None,
        "auc_ratio_avg": _avg(auc_ratios),
        "auc_ratio_median": _median(auc_ratios),
        "ece_margin_ratio_avg": _avg(ece_margin_ratios),
        "ece_margin_ratio_median": _median(ece_margin_ratios),
    }
    # Histograms (fixed bucket strategy tuned for small deltas)
    try:
        def build_hist(values: list[float], edges: list[float]):
            # edges must be sorted; returns list of {range, count}
            buckets: list[dict[str, object]] = []
            if not edges:
                return buckets
            counts = [0]*(len(edges)+1)
            for v in values:
                placed = False
                for i,e in enumerate(edges):
                    if v < e:
                        counts[i] += 1; placed = True; break
                if not placed:
                    counts[-1] += 1
            prev = float('-inf')
            for i,e in enumerate(edges):
                buckets.append({"bucket": f"({prev},{e})", "count": counts[i]})
                prev = e
            buckets.append({"bucket": f"[{prev},inf)", "count": counts[-1]})
            return buckets
        # AUC improvement typical scale ~0 to 0.02; include negative/large tails
        auc_edges = [-0.005, 0.0, 0.0025, 0.005, 0.01, 0.02]
        ece_edges = [-0.02, -0.01, -0.005, 0.0, 0.002, 0.005]
        summary["auc_improve_hist"] = build_hist(auc_vals, auc_edges)
        summary["ece_delta_hist"] = build_hist(ece_vals, ece_edges)
    except Exception as he:  # noqa: BLE001
        logger.debug("histogram_build_failed err=%s", he)
    return {"status": "ok", "summary": summary}

# --- Promotion Quality Alerting ---
LAST_PROMOTION_ALERT_TS: Optional[float] = None

async def promotion_alert_loop(interval: float = 300.0):  # every 5 minutes
    global LAST_PROMOTION_ALERT_TS
    while True:
        try:
            if not cfg.promotion_alert_enabled or not cfg.promotion_alert_webhook_url:
                await asyncio.sleep(interval)
                continue
            now = time.time()
            if LAST_PROMOTION_ALERT_TS and (now - LAST_PROMOTION_ALERT_TS) < cfg.promotion_alert_cooldown_seconds:
                await asyncio.sleep(interval)
                continue
            repo = LifecycleAuditRepository()
            rows = await repo.fetch_promotion_events(limit=max(cfg.promotion_alert_min_window, 50), offset=0)
            if len(rows) < cfg.promotion_alert_min_window:
                await asyncio.sleep(interval)
                continue
            auc_ratios: list[float] = []
            ece_margin_ratios: list[float] = []
            for r in rows:
                reason = r.get("reason") or ""
                auc_improve = None; ece_delta = None
                try:
                    if reason.startswith("auc+") or reason.startswith("criteria_not_met_"):
                        parts = reason.split("_")
                        for p in parts:
                            if p.startswith("auc+"):
                                try: auc_improve = float(p[4:])
                                except Exception: pass
                            elif p.startswith("auc") and not p.startswith("auc+"):
                                try: auc_improve = float(p.replace("auc",""))
                                except Exception: pass
                            elif p.startswith("ece_delta"):
                                try: ece_delta = float(p.replace("ece_delta",""))
                                except Exception: pass
                except Exception:
                    pass
                if isinstance(auc_improve,(int,float)) and cfg.training_promote_min_auc_delta>0:
                    auc_ratios.append(auc_improve/cfg.training_promote_min_auc_delta)
                if isinstance(ece_delta,(int,float)) and cfg.training_promote_max_ece_delta!=0:
                    ece_margin_ratios.append((cfg.training_promote_max_ece_delta - ece_delta)/cfg.training_promote_max_ece_delta)
            def _avg(arr: list[float]) -> Optional[float]:
                return sum(arr)/len(arr) if arr else None
            avg_auc_ratio = _avg(auc_ratios)
            avg_ece_margin = _avg(ece_margin_ratios)
            trigger = False
            reasons: list[str] = []
            if avg_auc_ratio is not None and avg_auc_ratio < cfg.promotion_alert_low_auc_ratio:
                trigger = True; reasons.append(f"low_auc_ratio:{avg_auc_ratio:.2f} < {cfg.promotion_alert_low_auc_ratio}")
            if avg_ece_margin is not None and avg_ece_margin < cfg.promotion_alert_low_ece_margin_ratio:
                trigger = True; reasons.append(f"low_ece_margin:{avg_ece_margin:.2f} < {cfg.promotion_alert_low_ece_margin_ratio}")
            if trigger:
                payload = {
                    "type": "promotion_quality_alert",
                    "ts": now,
                    "avg_auc_ratio": avg_auc_ratio,
                    "avg_ece_margin_ratio": avg_ece_margin,
                    "window": len(rows),
                    "reasons": reasons,
                }
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        await client.post(cfg.promotion_alert_webhook_url, json=payload)
                    LAST_PROMOTION_ALERT_TS = now
                except Exception as we:  # noqa: BLE001
                    logger.warning("promotion_alert_post_failed err=%s", we)
        except Exception as loop_err:  # noqa: BLE001
            logger.debug("promotion_alert_loop_error err=%s", loop_err)
        await asyncio.sleep(interval)

@app.post("/api/models/promotion/alert/test")
async def promotion_alert_test(_auth: bool = Depends(require_api_key)):
    if not cfg.promotion_alert_webhook_url:
        return {"status": "disabled", "reason": "missing_webhook"}
    payload = {"type": "promotion_quality_alert_test", "ts": time.time(), "message": "test alert"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(cfg.promotion_alert_webhook_url, json=payload)
        return {"status": "ok", "code": r.status_code}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}

@app.get("/api/models/promotion/alert/status")
async def promotion_alert_status(_auth: bool = Depends(require_api_key)):
    return {
        "enabled": cfg.promotion_alert_enabled,
        "webhook_configured": bool(cfg.promotion_alert_webhook_url),
        "min_window": cfg.promotion_alert_min_window,
        "low_auc_ratio": cfg.promotion_alert_low_auc_ratio,
        "low_ece_margin_ratio": cfg.promotion_alert_low_ece_margin_ratio,
        "cooldown_seconds": cfg.promotion_alert_cooldown_seconds,
        "last_alert_ts": LAST_PROMOTION_ALERT_TS,
        "now": time.time(),
        "in_cooldown": bool(LAST_PROMOTION_ALERT_TS and (time.time() - LAST_PROMOTION_ALERT_TS) < cfg.promotion_alert_cooldown_seconds),
    }

@app.get("/api/models/production/metrics")
async def production_model_metrics(_auth: bool = Depends(require_api_key)):
    repo = ModelRegistryRepository()
    rows = await repo.fetch_latest(cfg.auto_promote_model_name, "supervised", limit=5)
    prod = None
    for r in rows:
        if r["status"] == "production":
            prod = r
            break
    if not prod and rows:
        prod = rows[0]
    if not prod:
        return {"status": "no_models"}
    metrics = prod.get("metrics") or {}
    return {"status": "ok", "model": {"id": prod.get("id"), "version": prod.get("version"), "status": prod.get("status"), "metrics": metrics}}

@app.get("/api/models/production/calibration")
async def production_model_calibration(_auth: bool = Depends(require_api_key)):
    repo = ModelRegistryRepository()
    rows = await repo.fetch_latest(cfg.auto_promote_model_name, "supervised", limit=5)
    prod = None
    for r in rows:
        if r["status"] == "production":
            prod = r
            break
    if not prod and rows:
        prod = rows[0]
    if not prod:
        return {"status": "no_models"}
    metrics = prod.get("metrics") or {}
    calib = {k: metrics.get(k) for k in ["brier","ece","mce","reliability_bins"]}
    # Fallback: if reliability_bins missing in prod row, try training_jobs by version
    if (not calib.get("reliability_bins")) and prod.get("version") is not None:
        try:
            repo_jobs = TrainingJobRepository()
            jobs = await repo_jobs.fetch_recent(limit=200)
            for j in jobs:
                if j.get("status") == "success" and str(j.get("version")) == str(prod.get("version")) and isinstance(j.get("metrics"), dict):
                    rb = j["metrics"].get("reliability_bins")
                    if rb:
                        calib["reliability_bins"] = rb
                        break
        except Exception:
            pass
    return {"status": "ok", "model_version": prod.get("version"), "calibration": calib}

@app.get("/api/models/summary")
async def models_summary(limit: int = 6, name: Optional[str] = None, model_type: str = "supervised", _auth: bool = Depends(require_api_key)):
    """요약된 모델 레지스트리 뷰.

    Response structure:
      status: ok | no_models
      production: {id, version, metrics}
      recent: [ {id, version, status, is_production, registered_at, promoted_at, metrics: {...}, deltas: {auc_delta, brier_delta, ece_delta}} ]
      recommendation: optional promotion hint (string)
    """
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be > 0")
    repo = ModelRegistryRepository()
    rows = []
    used_name = None
    if name:
        try:
            rset = await repo.fetch_latest(name, model_type, limit=limit)
            rows = [x for x in rset if (x.get("status") != "deleted")]
            used_name = name
        except Exception as e:  # noqa: BLE001
            logger.debug("models_summary_fetch_failed name=%s err=%s", name, e)
            rows = []
    else:
        # Bottom-only mode: prefer bottom_predictor; fall back to configured name if provided
        candidate_names = []
        if cfg.auto_promote_model_name:
            candidate_names.append(cfg.auto_promote_model_name)
        if "bottom_predictor" not in candidate_names:
            candidate_names.insert(0, "bottom_predictor")
        # Import alternate list if available
        try:
            from backend.apps.training.auto_promotion import ALT_MODEL_CANDIDATES as _ALTS  # type: ignore
            for c in _ALTS:
                if c not in candidate_names:
                    candidate_names.append(c)
        except Exception:
            pass
        for nm in candidate_names:
            try:
                rset = await repo.fetch_latest(nm, model_type, limit=limit)
                if rset:
                    # filter deleted rows
                    rows = [x for x in rset if (x.get("status") != "deleted")]
                    used_name = nm
                    break
            except Exception as e:  # noqa: BLE001
                logger.debug("models_summary_fetch_failed name=%s err=%s", nm, e)
                continue
    if not rows:
        # 추가 진단: 각 후보별 레코드 수 (limit=1) 간단 조회
        if name:
            return {"status": "no_models", "name": name, "model_type": model_type}
        else:
            counts: dict[str,int] = {}
            for nm in candidate_names:
                try:
                    test_rows = await repo.fetch_latest(nm, model_type, limit=1)
                    counts[nm] = len(test_rows)
                except Exception:
                    counts[nm] = -1  # fetch 실패 표시
            return {"status": "no_models", "candidates": candidate_names, "counts": counts}
    prod = None
    for r in rows:
        if r.get("status") == "production":
            prod = r
            break
    if not prod:
        prod = rows[0]
    prod_metrics = (prod.get("metrics") or {}) if prod else {}
    # Fallback: if production metrics missing, try to pull from latest successful training job for same version
    if prod and (not prod_metrics or not isinstance(prod_metrics, dict) or (prod_metrics.get("auc") is None and prod_metrics.get("ece") is None)):
        try:
            # search training_jobs table for matching version
            repo_jobs = TrainingJobRepository()
            jobs = await repo_jobs.fetch_recent(limit=100)
            for j in jobs:
                if str(j.get("version")) == str(prod.get("version")) and j.get("status") == "success" and j.get("metrics"):
                    prod_metrics = j.get("metrics")
                    break
        except Exception:
            pass
    prod_auc = prod_metrics.get("auc") if isinstance(prod_metrics, dict) else None
    prod_brier = prod_metrics.get("brier") if isinstance(prod_metrics, dict) else None
    prod_ece = prod_metrics.get("ece") if isinstance(prod_metrics, dict) else None
    # Prepare a version->metrics map from recent successful training jobs as a fallback
    version_metrics: dict[str, dict] = {}
    try:
        repo_jobs = TrainingJobRepository()
        jobs = await repo_jobs.fetch_recent(limit=200)
        for j in jobs:
            try:
                if j.get("status") == "success" and j.get("version") and isinstance(j.get("metrics"), dict):
                    version_metrics[str(j.get("version"))] = j.get("metrics")  # type: ignore
            except Exception:
                continue
    except Exception:
        pass

    recent_out: list[dict] = []
    best_candidate = None
    best_auc_improve = 0.0
    for r in rows:
        m = r.get("metrics") or {}
        # If metrics are empty or missing core keys, try to backfill from training_jobs by version
        if (not isinstance(m, dict) or (m.get("auc") is None and m.get("ece") is None)) and r.get("version") is not None:
            try:
                mv = version_metrics.get(str(r.get("version")))
                if isinstance(mv, dict):
                    m = mv
            except Exception:
                pass
        cur_auc = m.get("auc") if isinstance(m, dict) else None
        cur_brier = m.get("brier") if isinstance(m, dict) else None
        cur_ece = m.get("ece") if isinstance(m, dict) else None
        auc_delta = (cur_auc - prod_auc) if (prod_auc is not None and cur_auc is not None) else None
        brier_delta = (cur_brier - prod_brier) if (prod_brier is not None and cur_brier is not None) else None  # positive => worse
        ece_delta = (cur_ece - prod_ece) if (prod_ece is not None and cur_ece is not None) else None  # positive => worse
        is_prod = (r.get("id") == prod.get("id")) if prod else False
        # Frontend 표준화를 위해 created_at 필드 직접 노출 (기존 registered_at 유지)
        # Flatten deltas for current frontend (expects m.auc_delta / m.ece_delta directly)
        entry = {
            "id": r.get("id"),
            "version": r.get("version"),
            "status": r.get("status"),
            "is_production": is_prod,
            "baseline": is_prod,
            "created_at": r.get("created_at"),
            "registered_at": r.get("created_at"),
            "promoted_at": r.get("promoted_at"),
            "metrics": m,
            # flattened for UI
            "auc_delta": (auc_delta if not is_prod else 0.0),
            "brier_delta": (brier_delta if not is_prod else 0.0),
            "ece_delta": (ece_delta if not is_prod else 0.0),
            # keep nested structure for future clients
            "deltas": {
                "auc_delta": auc_delta if not is_prod else 0.0,
                "brier_delta": brier_delta if not is_prod else 0.0,
                "ece_delta": ece_delta if not is_prod else 0.0,
            },
        }
        recent_out.append(entry)
        # Track candidate with positive auc improvement and non-worse calibration
        if r.get("id") != prod.get("id") if prod else False:
            if auc_delta is not None and auc_delta > 0:
                # Accept only if Brier/ECE not severely worse
                brier_ok = True
                ece_ok = True
                try:
                    def _f(name: str, default: float) -> float:
                        _v = os.getenv(name, str(default))
                        if isinstance(_v, str) and '#' in _v:
                            _v = _v.split('#', 1)[0].strip()
                        try:
                            return float(_v)
                        except Exception:
                            return default
                    max_brier_degradation = _f("PROMOTION_MAX_BRIER_DEGRADATION", 0.01)
                    max_ece_degradation = _f("PROMOTION_MAX_ECE_DEGRADATION", 0.01)
                except Exception:
                    max_brier_degradation = 0.01
                    max_ece_degradation = 0.01
                if brier_delta is not None and brier_delta > max_brier_degradation:
                    brier_ok = False
                if ece_delta is not None and ece_delta > max_ece_degradation:
                    ece_ok = False
                if brier_ok and ece_ok and auc_delta > best_auc_improve:
                    best_auc_improve = auc_delta
                    best_candidate = r
    recommendation = None
    if best_candidate:
        recommendation = f"consider_promote_version_{best_candidate.get('version')}"  # simple hint
        # Mark the recommended candidate row with promotion flag
        bid = best_candidate.get("id")
        for row in recent_out:
            if row.get("id") == bid:
                row["promote_recommend"] = True
            else:
                row.setdefault("promote_recommend", False)
    else:
        # Ensure flag present for UI simplicity
        for row in recent_out:
            row.setdefault("promote_recommend", False)
    return {
        "status": "ok",
        "has_model": bool(rows),
        "production": {
            "id": prod.get("id"),
            "version": prod.get("version"),
            "created_at": prod.get("created_at"),
            "promoted_at": prod.get("promoted_at"),
            "metrics": prod_metrics,
        } if prod else None,
    "model_name": used_name,
        "recent": recent_out,
        "recommendation": recommendation,
    }

@app.get("/api/inference/logs")
async def inference_logs(limit: int = 100, offset: int = 0, _auth: bool = Depends(require_api_key)):
    repo = InferenceLogRepository()
    rows = await repo.fetch_recent(limit=limit, offset=offset)
    out = []
    for r in rows:
        out.append({k: r[k] for k in r.keys()})
    return {"logs": out, "limit": limit, "offset": offset}

@app.get("/api/inference/probability/histogram")
async def inference_probability_histogram(window_seconds: int = 3600, _auth: bool = Depends(require_api_key)):
    repo = InferenceLogRepository()
    buckets = await repo.probability_histogram(window_seconds=window_seconds)
    return {"window_seconds": window_seconds, "buckets": buckets}

@app.post("/api/inference/realized")
async def inference_realized_backfill(payload: RealizedBatchRequest, _auth: bool = Depends(require_api_key)):
    if not payload.updates:
        return {"updated": 0}
    repo = InferenceLogRepository()
    count = await repo.update_realized_batch([
        {"id": u.id, "realized": u.realized} for u in payload.updates
    ])
    return {"updated": count}

_AUTO_LABELER_AUTO_MIN_INTERVAL_SEC = float(
    os.getenv("AUTO_LABELER_AUTO_MIN_INTERVAL_SEC", str(max(30, cfg.auto_labeler_interval)))
)

async def _maybe_auto_labeler_kick(reason: str, *, min_age: Optional[int] = None, limit: Optional[int] = None) -> bool:
    """Background-trigger the auto labeler when recent realized labels are missing."""
    if not cfg.auto_labeler_enabled:
        return False
    state = app.state
    if not hasattr(state, "auto_labeler_auto_min_interval"):
        state.auto_labeler_auto_min_interval = _AUTO_LABELER_AUTO_MIN_INTERVAL_SEC
    if getattr(state, "auto_labeler_auto_inflight", False):
        return False
    now = time.time()
    last = getattr(state, "auto_labeler_last_auto", 0.0)
    min_interval = getattr(state, "auto_labeler_auto_min_interval", _AUTO_LABELER_AUTO_MIN_INTERVAL_SEC)
    if now - last < min_interval:
        return False

    state.auto_labeler_auto_inflight = True

    async def _runner() -> None:
        try:
            from backend.apps.training.service.auto_labeler import get_auto_labeler_service

            svc = get_auto_labeler_service()
            result = await svc.run_once(min_age_seconds=min_age, limit=limit)
            logger.info(
                "auto_labeler_auto_kick reason=%s status=%s labeled=%s",
                reason,
                result.get("status") if isinstance(result, dict) else None,
                result.get("labeled") if isinstance(result, dict) else None,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("auto_labeler_auto_kick_failed reason=%s err=%s", reason, e)
        finally:
            state.auto_labeler_last_auto = time.time()
            state.auto_labeler_auto_inflight = False

    asyncio.create_task(_runner())
    return True

@app.get("/api/inference/calibration/live")
async def inference_live_calibration(
    window_seconds: int = 3600,
    bins: int = 10,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    target: Optional[str] = None,
    eager_label: bool = True,
    eager_limit: Optional[int] = None,
    eager_min_age_seconds: Optional[int] = None,
    _auth: bool = Depends(require_api_key),
):
    # Runtime overrides from DB-backed settings (if present and request uses defaults)
    try:
        if window_seconds == 3600 and hasattr(app.state, 'calibration_live_window_seconds'):
            ws = int(getattr(app.state, 'calibration_live_window_seconds'))
            if ws > 0:
                window_seconds = ws
    except Exception:
        pass
    try:
        if bins == 10 and hasattr(app.state, 'calibration_live_bins'):
            bb = int(getattr(app.state, 'calibration_live_bins'))
            if bb > 0:
                bins = bb
    except Exception:
        pass
    # Apply eager label runtime flags when not explicitly overridden in query
    try:
        if hasattr(app.state, 'calibration_eager_enabled'):
            eager_label = bool(getattr(app.state, 'calibration_eager_enabled'))
    except Exception:
        pass
    if eager_min_age_seconds is None:
        try:
            if hasattr(app.state, 'calibration_eager_min_age_seconds'):
                eager_min_age_seconds = int(getattr(app.state, 'calibration_eager_min_age_seconds'))
        except Exception:
            pass
    if eager_limit is None:
        try:
            if hasattr(app.state, 'calibration_eager_limit'):
                eager_limit = int(getattr(app.state, 'calibration_eager_limit'))
        except Exception:
            pass
    # 입력 검증
    if bins <= 0:
        raise HTTPException(status_code=400, detail="bins must be > 0")
    if bins > 500:  # 안전 가드 (비정상적으로 큰 bin 요청 방지)
        raise HTTPException(status_code=400, detail="bins too large (max 500)")
    if window_seconds <= 0:
        raise HTTPException(status_code=400, detail="window_seconds must be > 0")
    repo = InferenceLogRepository()
    try:
        rows = await repo.fetch_window_for_live_calibration(window_seconds=window_seconds, symbol=symbol, interval=interval, target=target)
    except Exception as e:
        logger.warning("live_calibration_fetch_failed window=%s bins=%s symbol=%s err=%s", window_seconds, bins, symbol, e)
        return {"status": "error", "error": "fetch_failed", "window_seconds": window_seconds, "bins": bins, "symbol": symbol, "interval": interval, "target": target}
    attempted_eager = False
    if not rows:
        # First, try background kick (non-blocking)
        auto_kick = await _maybe_auto_labeler_kick(
            "calibration_no_data",
            min_age=cfg.auto_labeler_min_age_seconds,
            limit=cfg.auto_labeler_batch_limit,
        )
        # Optionally run a bounded eager label pass synchronously to minimize no_data responses
        if eager_label:
            attempted_eager = True
            try:
                from backend.apps.training.service.auto_labeler import get_auto_labeler_service as _get_als
                _svc = _get_als()
                # Derive safe parameters
                _min_age = int(eager_min_age_seconds) if isinstance(eager_min_age_seconds, int) and eager_min_age_seconds >= 0 else int(getattr(cfg, 'auto_labeler_min_age_seconds', 120))
                _limit = int(eager_limit) if isinstance(eager_limit, int) and eager_limit > 0 else int(min(1000, getattr(cfg, 'auto_labeler_batch_limit', 1000)))
                # Avoid stampede: only one eager run at a time
                if not hasattr(app.state, 'calibration_eager_label_lock'):
                    import asyncio as _aio
                    app.state.calibration_eager_label_lock = _aio.Lock()
                lock = app.state.calibration_eager_label_lock
                _lab_sym = str(symbol or getattr(cfg, 'symbol', 'unknown'))
                _lab_int = str(interval or getattr(cfg, 'kline_interval', 'unknown'))
                _lab_result = 'attempted'
                if lock.locked():
                    # Another request is already trying eager labeling; skip to refetch
                    _lab_result = 'skipped_lock'
                else:
                    async with lock:  # type: ignore
                        CALIBRATION_EAGER_LABEL_RUNS.inc()
                        await _svc.run_once(min_age_seconds=_min_age, limit=_limit, only_symbol=symbol, only_interval=interval, only_target=target)
                # Re-fetch rows after eager labeling
                try:
                    rows = await repo.fetch_window_for_live_calibration(window_seconds=window_seconds, symbol=symbol, interval=interval, target=target)
                except Exception:
                    rows = []
                try:
                    CALIBRATION_EAGER_LABEL_RUNS_LABELED.labels(_lab_sym, _lab_int, _lab_result).inc()
                except Exception:
                    pass
            except Exception as ee:  # pragma: no cover
                try:
                    logger.warning("live_calibration_eager_label_failed window=%s symbol=%s err=%s", window_seconds, symbol, ee)
                except Exception:
                    pass
                try:
                    _lab_sym = str(symbol or getattr(cfg, 'symbol', 'unknown'))
                    _lab_int = str(interval or getattr(cfg, 'kline_interval', 'unknown'))
                    CALIBRATION_EAGER_LABEL_RUNS_LABELED.labels(_lab_sym, _lab_int, 'error').inc()
                except Exception:
                    pass
        # If still no data, return actionable hint
        if not rows:
            return {
                "status": "no_data",
                "window_seconds": window_seconds,
                "bins": bins,
                "symbol": symbol,
                "interval": interval,
                "target": target,
                "auto_labeler_triggered": auto_kick,
                "attempted_eager_label": attempted_eager,
                "hint": "No labeled inferences in the window. Labeler was triggered. Keep AUTO_LABELER_ENABLED=true or run /api/inference/labeler/run to backfill realized labels.",
            }
    probs: List[float] = []
    labels: List[int] = []
    for r in rows:
        try:
            p = float(r["probability"])
            if p < 0: p = 0.0
            if p > 1: p = 1.0
            probs.append(p)
            y_raw = r["realized"]
            labels.append(1 if (y_raw is not None and y_raw > 0) else 0)
        except Exception:
            continue
    n = len(probs)
    if n == 0:
        auto_kick = await _maybe_auto_labeler_kick(
            "calibration_empty",
            min_age=cfg.auto_labeler_min_age_seconds,
            limit=cfg.auto_labeler_batch_limit,
        )
        return {
            "status": "no_data",
            "window_seconds": window_seconds,
            "bins": bins,
            "symbol": symbol,
            "interval": interval,
            "target": target,
            "auto_labeler_triggered": auto_kick,
        }
    # If sample count is smaller than requested bins, adapt bins downward for a more informative table
    try:
        if isinstance(bins, int) and n < bins:
            # Keep at least 3 bins when possible
            bins = max(3, n)
    except Exception:
        pass
    # Shared calibration computation
    try:
        calc = compute_calibration(probs, labels, bins=bins)
    except Exception as e:  # noqa: BLE001
        logger.warning("live_calibration_compute_failed window=%s bins=%s symbol=%s err=%s", window_seconds, bins, symbol, e)
        return {"status": "error", "error": "compute_failed", "window_seconds": window_seconds, "bins": bins, "symbol": symbol, "interval": interval, "target": target}
    brier = calc.get("brier")
    ece = calc.get("ece")
    mce = calc.get("mce")
    reliability_bins = calc.get("reliability_bins", [])
    # Set live metrics
    try:
        INFERENCE_LIVE_BRIER.set(brier)
        INFERENCE_LIVE_ECE.set(ece)
    except Exception:
        pass
    live_ece = ece
    live_mce = mce
    # Fetch production ECE for delta
    prod_ece = await _fetch_production_ece()
    if prod_ece is None or live_ece is None:
        return {
            "status": "ok",
            "window_seconds": window_seconds,
            "bins": bins,
            "symbol": symbol,
            "interval": interval,
            "brier": brier,
            "ece": live_ece,
            "mce": live_mce,
            "reliability_bins": reliability_bins,
            "prod_ece": prod_ece,
            "target": target,
            "samples": len(probs),
            "n": len(probs),
            # Include configured thresholds for visibility
            "abs_threshold": float(getattr(cfg, 'calibration_monitor_ece_drift_abs', 0.05)) if getattr(cfg, 'calibration_monitor_ece_drift_abs', None) is not None else None,
            "rel_threshold": float(getattr(cfg, 'calibration_monitor_ece_drift_rel', 0.5)) if getattr(cfg, 'calibration_monitor_ece_drift_rel', None) is not None else None,
        }
    delta = abs(live_ece - prod_ece)
    try:
        INFERENCE_LIVE_ECE_DELTA.set(delta)
        CALIBRATION_LAST_LIVE_ECE.set(live_ece)
        CALIBRATION_LAST_PROD_ECE.set(prod_ece)
    except Exception:
        pass
    # Thresholds possibly overridden at runtime
    abs_th = float(getattr(app.state, 'calibration_monitor_ece_abs', getattr(cfg, 'calibration_monitor_ece_drift_abs', 0.05)))
    rel_th = float(getattr(app.state, 'calibration_monitor_ece_rel', getattr(cfg, 'calibration_monitor_ece_drift_rel', 0.5)))
    # relative denominator safeguard
    rel_gap = delta / prod_ece if prod_ece > 0 else None
    drift_abs = delta >= abs_th
    drift_rel = rel_gap is not None and rel_gap >= rel_th
    # Maintain streaks (module-level globals on app state)
    streak_abs = getattr(_streak_state, 'abs_streak', 0)
    streak_rel = getattr(_streak_state, 'rel_streak', 0)
    if drift_abs:
        CALIBRATION_DRIFT_EVENTS.labels(type="abs").inc()
        streak_abs += 1
    else:
        streak_abs = 0
    if drift_rel:
        CALIBRATION_DRIFT_EVENTS.labels(type="rel").inc()
        streak_rel += 1
    else:
        streak_rel = 0
    _streak_state.abs_streak = streak_abs
    _streak_state.rel_streak = streak_rel
    try:
        CALIBRATION_DRIFT_STREAK_ABS.set(streak_abs)
        CALIBRATION_DRIFT_STREAK_REL.set(streak_rel)
    except Exception:
        pass
        # Save last snapshot
    _streak_state.last_snapshot = {
        "ts": time.time(),
        "live_ece": live_ece,
        "live_mce": live_mce,
        "prod_ece": prod_ece,
        "delta": delta,
        "abs_threshold": abs_th,
        "rel_threshold": rel_th,
        "abs_drift": drift_abs,
        "rel_drift": drift_rel,
        "rel_gap": rel_gap,
        "sample_count": len(probs),
    }
    # Return final calibration snapshot when production ECE is available
    return {
        "status": "ok",
        "window_seconds": window_seconds,
        "bins": bins,
        "symbol": symbol,
        "interval": interval,
        "brier": brier,
        "ece": live_ece,
        "mce": live_mce,
        "reliability_bins": reliability_bins,
        "prod_ece": prod_ece,
        "target": target,
        "delta": delta,
        "abs_drift": drift_abs,
        "rel_drift": drift_rel,
        "rel_gap": rel_gap,
        "streak_abs": streak_abs,
        "streak_rel": streak_rel,
        "samples": len(probs),
        "n": len(probs),
    }

@app.post("/api/inference/labeler/run")
async def manual_labeler_run(
    force: bool = False,
    min_age_seconds: Optional[int] = None,
    limit: Optional[int] = None,
    lookahead: Optional[int] = None,
    drawdown: Optional[float] = None,
    rebound: Optional[float] = None,
    flush_queue: bool = False,
    only_target: Optional[str] = None,
    only_symbol: Optional[str] = None,
    only_interval: Optional[str] = None,
    _auth: bool = Depends(require_api_key),
):
    """수동 자동라벨러 1회 실행.

    Query:
      force: true 이면 AUTO_LABELER_ENABLED=false 여도 강제 실행 (기본 False)
    """
    if not cfg.auto_labeler_enabled and not force:
        raise HTTPException(status_code=400, detail="auto labeler disabled (use force=true to override)")
    from backend.apps.training.service.auto_labeler import get_auto_labeler_service
    svc = get_auto_labeler_service()
    # Optionally flush inference log queue to ensure latest predictions are persisted
    if bool(flush_queue):
        try:
            n = await get_inference_log_queue().flush_now()
        except Exception:
            n = 0
    result = await svc.run_once(
        min_age_seconds=min_age_seconds,
        limit=limit,
        lookahead=lookahead,
        drawdown=drawdown,
        rebound=rebound,
        only_target=only_target,
        only_symbol=only_symbol,
        only_interval=only_interval,
    )
    result["forced"] = bool(force and not cfg.auto_labeler_enabled)
    # If error, include a brief operator hint
    if isinstance(result, dict) and result.get("status") == "error":
        err = result.get("error")
        result["hint"] = (
            "Labeler failed. Ensure DB is reachable and feature_snapshot_meta/value tables exist. "
            "You can trigger feature backfill via /admin/features/backfill and then re-run."
        )
        if err and ("feature_snapshot" in str(err) or "relation" in str(err)):
            result["hint"] += " Likely missing feature schema; run backfill first."
    return result

# --- Admin: Auto Labeler status (observability) ---
@app.get("/admin/inference/labeler/status", dependencies=[Depends(require_api_key)])
async def admin_labeler_status():
    from backend.apps.training.service.auto_labeler import get_auto_labeler_service
    svc = get_auto_labeler_service()
    # Pull metrics gauges if available
    try:
        from backend.apps.training.service.auto_labeler import AUTO_LABELER_LAST_SUCCESS, AUTO_LABELER_LAST_ERROR, AUTO_LABELER_BACKLOG
        last_success = getattr(AUTO_LABELER_LAST_SUCCESS, '_value', None)
        last_error = getattr(AUTO_LABELER_LAST_ERROR, '_value', None)
        backlog = getattr(AUTO_LABELER_BACKLOG, '_value', None)
        # Prometheus client stores internal _value (float) per-process; guard for attribute shape changes
        try:
            last_success = float(last_success.get()) if hasattr(last_success, 'get') else None
        except Exception:
            pass
        try:
            last_error = float(last_error.get()) if hasattr(last_error, 'get') else None
        except Exception:
            pass
        try:
            backlog = float(backlog.get()) if hasattr(backlog, 'get') else None
        except Exception:
            pass
    except Exception:
        last_success = None
        last_error = None
        backlog = None
    # Effective params
    cfg_local = load_config()
    return {
        "status": "ok",
        "enabled": bool(cfg_local.auto_labeler_enabled),
        "interval": float(cfg_local.auto_labeler_interval),
        "min_age_seconds": int(cfg_local.auto_labeler_min_age_seconds),
        "batch_limit": int(cfg_local.auto_labeler_batch_limit),
        "bottom_lookahead": int(getattr(cfg_local, 'bottom_lookahead', 30)),
        "bottom_drawdown": float(getattr(cfg_local, 'bottom_drawdown', 0.005)),
        "bottom_rebound": float(getattr(cfg_local, 'bottom_rebound', 0.003)),
        "running": bool(getattr(svc, '_running', False)),
        "last_success_ts": last_success,
        "last_error_ts": last_error,
        "approx_backlog": backlog,
    }

# --- Admin: Auto Labeler runtime configuration (get/set) ---
@app.get("/admin/inference/labeler/config", dependencies=[Depends(require_api_key)])
async def admin_labeler_config_get():
    from backend.apps.training.service.auto_labeler import get_auto_labeler_service
    svc = get_auto_labeler_service()
    return {
        "status": "ok",
        "runtime": {
            "interval": getattr(svc, "_interval", None),
            "min_age_seconds": getattr(svc, "_min_age", None),
            "batch_limit": getattr(svc, "_batch_limit", None),
            "lookahead_override": getattr(svc, "_L_override", None),
            "drawdown_override": getattr(svc, "_DD_override", None),
            "rebound_override": getattr(svc, "_RB_override", None),
        },
        "config": {
            "interval": getattr(cfg, 'auto_labeler_interval', None),
            "min_age_seconds": getattr(cfg, 'auto_labeler_min_age_seconds', None),
            "batch_limit": getattr(cfg, 'auto_labeler_batch_limit', None),
            "bottom_lookahead": getattr(cfg, 'bottom_lookahead', None),
            "bottom_drawdown": getattr(cfg, 'bottom_drawdown', None),
            "bottom_rebound": getattr(cfg, 'bottom_rebound', None),
        },
    }

@app.post("/admin/inference/labeler/config", dependencies=[Depends(require_api_key)])
async def admin_labeler_config_set(
    interval: Optional[float] = None,
    min_age_seconds: Optional[int] = None,
    batch_limit: Optional[int] = None,
    lookahead: Optional[int] = None,
    drawdown: Optional[float] = None,
    rebound: Optional[float] = None,
):
    from backend.apps.training.service.auto_labeler import get_auto_labeler_service
    svc = get_auto_labeler_service()
    # Apply with best-effort validation (service method does guards)
    svc.apply_runtime_config(
        interval=interval,
        min_age_seconds=min_age_seconds,
        batch_limit=batch_limit,
        lookahead=lookahead,
        drawdown=drawdown,
        rebound=rebound,
    )
    # Echo current effective runtime values
    return {
        "status": "ok",
        "runtime": {
            "interval": getattr(svc, "_interval", None),
            "min_age_seconds": getattr(svc, "_min_age", None),
            "batch_limit": getattr(svc, "_batch_limit", None),
            "lookahead_override": getattr(svc, "_L_override", None),
            "drawdown_override": getattr(svc, "_DD_override", None),
            "rebound_override": getattr(svc, "_RB_override", None),
        },
    }

# --- Admin: DB-backed Settings (centralized) ---
@app.get("/admin/settings", dependencies=[Depends(require_api_key)])
async def admin_settings_list():
    repo = SettingsRepository()
    items = await repo.get_all()
    return {"status": "ok", "items": items}

@app.get("/admin/settings/{key}", dependencies=[Depends(require_api_key)])
async def admin_settings_get(key: str):
    repo = SettingsRepository()
    item = await repo.get(key)
    if not item:
        # If key is in UI namespace, serve a soft default to avoid noisy 404s on first read
        try:
            if isinstance(key, str) and key.startswith("ui."):
                suffix = key[3:]
                if suffix in UI_DEFAULTS:
                    return {"status": "ok", "item": {"key": key, "value": UI_DEFAULTS[suffix], "scope": None}}
            # Live trading namespace soft defaults
            if isinstance(key, str) and key.startswith("live_trading."):
                suffix = key.split(".", 1)[1]
                if suffix in LIVE_DEFAULTS:
                    return {"status": "ok", "item": {"key": key, "value": LIVE_DEFAULTS[suffix], "scope": None}}
            # Calibration namespace defaults
            if isinstance(key, str) and key.startswith("calibration."):
                suffix = key.split(".", 1)[1]
                if suffix in CALIB_DEFAULTS:
                    return {"status": "ok", "item": {"key": key, "value": CALIB_DEFAULTS[suffix], "scope": None}}
            # Drift namespace defaults
            if isinstance(key, str) and key.startswith("drift."):
                suffix = key.split(".", 1)[1]
                if suffix in DRIFT_DEFAULTS:
                    return {"status": "ok", "item": {"key": key, "value": DRIFT_DEFAULTS[suffix], "scope": None}}
            # Risk namespace defaults (from current applied runtime limits)
            if isinstance(key, str) and key.startswith("risk."):
                suffix = key.split(".", 1)[1]
                if suffix in ("max_notional", "max_daily_loss", "max_drawdown", "atr_multiple"):
                    try:
                        val = float(getattr(risk_engine.limits, suffix))
                    except Exception:
                        val = None
                    if val is not None:
                        return {"status": "ok", "item": {"key": key, "value": val, "scope": None}}
            # Training namespace soft defaults
            if isinstance(key, str) and key.startswith("training."):
                # Known keys:
                # training.bottom.min_train_labels
                # training.bottom.min_labels
                # training.bottom.ohlcv_fetch_cap
                from backend.common.config.base_config import load_config as _lc
                _cfg_local = _lc()
                if key == "training.bottom.min_labels":
                    try:
                        v = int(getattr(_cfg_local, 'bottom_min_labels', 150))
                    except Exception:
                        v = 150
                    return {"status": "ok", "item": {"key": key, "value": v, "scope": None}}
            # ML namespace soft defaults (cooldown)
            if isinstance(key, str) and key == "ml.signal.cooldown_sec":
                try:
                    from backend.common.config.base_config import load_config as _lc2
                    _cfg2 = _lc2()
                    v = float(getattr(_cfg2, 'ml_signal_cooldown_sec', 60.0) or 60.0)
                except Exception:
                    v = 60.0
                return {"status": "ok", "item": {"key": key, "value": v, "scope": None}}
                if key == "training.bottom.min_train_labels":
                    # Heuristic: half of min_labels, clamped 60..120
                    try:
                        base = int(getattr(_cfg_local, 'bottom_min_labels', 150))
                    except Exception:
                        base = 150
                    v = max(60, min(120, int(base * 0.5)))
                    return {"status": "ok", "item": {"key": key, "value": v, "scope": None}}
                if key == "training.bottom.ohlcv_fetch_cap":
                    try:
                        v = int(getattr(_cfg_local, 'bottom_ohlcv_fetch_cap', 5000))
                    except Exception:
                        v = 5000
                    return {"status": "ok", "item": {"key": key, "value": v, "scope": None}}
        except Exception:
            pass
        raise HTTPException(status_code=404, detail="not_found")
    return {"status": "ok", "item": item}

@app.put("/admin/settings/{key}", dependencies=[Depends(require_api_key)])
async def admin_settings_put(key: str, request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid_payload")
    value = payload.get("value")
    scope = payload.get("scope")
    updated_by = payload.get("updated_by") or "api"
    apply_now = bool(payload.get("apply", True))
    repo = SettingsRepository()
    saved = await repo.upsert(key, value, scope=scope, updated_by=updated_by)
    applied = False
    if apply_now:
        try:
            applied = await apply_runtime_setting(key, value)
        except Exception:
            applied = False
    return {"status": "ok", "item": saved, "applied": applied}

# --- Admin: Generate a batch of predictions (server-side helper) ---
@app.post("/admin/inference/predict/batch", dependencies=[Depends(require_api_key)])
async def admin_inference_predict_batch(
    count: int = 5,
    threshold: Optional[float] = None,
    delay_ms: int = 0,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    prefer_latest: bool = False,
    version: Optional[str] = None,
):
    """Run N predictions back-to-back and enqueue logs, for validation.

    Params:
      count: 1-100
      threshold: optional override; defaults to configured inference_prob_threshold
      delay_ms: sleep between predictions (milliseconds)
      symbol/interval: optional overrides
    """
    if count < 1 or count > 100:
        raise HTTPException(status_code=400, detail="count_out_of_range")
    use_symbol = symbol or cfg.symbol
    use_interval = interval or cfg.kline_interval
    # Resolve threshold: explicit > runtime override > config default
    thr_override = getattr(app.state, 'auto_inference_threshold_override', None)
    thr_cfg = float(getattr(cfg, 'inference_prob_threshold', 0.5))
    thr = (
        float(threshold) if (isinstance(threshold, (int, float)) and 0 < float(threshold) < 1)
        else (float(thr_override) if (isinstance(thr_override, (int, float)) and 0 < float(thr_override) < 1) else thr_cfg)
    )
    svc = TrainingService(use_symbol, use_interval, cfg.model_artifact_dir)
    ok = 0
    errors: list[str] = []
    last: dict | None = None
    for i in range(count):
        try:
            if hasattr(svc, 'predict_latest_bottom'):
                res = await svc.predict_latest_bottom(threshold=thr, debug=False, prefer_latest=bool(prefer_latest), version=version)  # type: ignore[attr-defined]
            else:
                res = await svc.predict_latest(threshold=thr, debug=False, prefer_latest=bool(prefer_latest), version=version)
            # enqueue log (same as /api/inference/predict)
            if isinstance(res, dict) and res.get("status") == "ok" and isinstance(res.get("probability"), (int,float)):
                try:
                    res.setdefault("target", "bottom")
                    # Prefer DB-applied overrides via labeler runtime; fallback to config defaults
                    try:
                        from backend.apps.training.service.auto_labeler import get_auto_labeler_service as _get_als
                        _svc_lbl = _get_als()
                        L = getattr(_svc_lbl, "_L_override", None)
                        DD = getattr(_svc_lbl, "_DD_override", None)
                        RB = getattr(_svc_lbl, "_RB_override", None)
                    except Exception:
                        L = None; DD = None; RB = None
                    res.setdefault("label_params", {
                        "lookahead": int(L if isinstance(L, (int, float)) else (getattr(cfg, 'bottom_lookahead', 30) or 30)),
                        "drawdown": float(DD if isinstance(DD, (int, float)) else (getattr(cfg, 'bottom_drawdown', 0.005) or 0.005)),
                        "rebound": float(RB if isinstance(RB, (int, float)) else (getattr(cfg, 'bottom_rebound', 0.003) or 0.003)),
                    })
                except Exception:
                    pass
                log_queue = get_inference_log_queue()
                _model_name_log = 'bottom_predictor'
                enq_ok = await log_queue.enqueue({
                    "symbol": use_symbol,
                    "interval": use_interval,
                    "model_name": _model_name_log,
                    "model_version": str(res.get("model_version")),
                    "probability": float(res.get("probability")),
                    "decision": int(res.get("decision")),
                    "threshold": float(res.get("threshold")),
                    "production": bool(res.get("used_production")),
                    "extra": {"auto_batch": True, "target": "bottom"},
                })
                if not enq_ok:
                    repo = InferenceLogRepository()
                    with contextlib.suppress(Exception):
                        await repo.bulk_insert([
                            {
                                "symbol": use_symbol,
                                "interval": use_interval,
                                "model_name": _model_name_log,
                                "model_version": str(res.get("model_version")),
                                "probability": float(res.get("probability")),
                                "decision": int(res.get("decision")),
                                "threshold": float(res.get("threshold")),
                                "production": bool(res.get("used_production")),
                                "extra": {"auto_batch": True, "fallback": True, "target": "bottom"},
                            }
                        ])
                ok += 1
                last = res
            else:
                errors.append(str(res))
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
        if delay_ms and delay_ms > 0:
            await asyncio.sleep(max(0.0, float(delay_ms) / 1000.0))
    return {"status": "ok", "attempted": count, "success": ok, "errors": errors[-3:], "symbol": use_symbol, "interval": use_interval, "threshold": thr, "last": last}

# --- Admin: Quick validation orchestrator ---
@app.post("/admin/inference/quick-validate", dependencies=[Depends(require_api_key)])
async def admin_inference_quick_validate(
    request: Request,
    count: int = 5,
    threshold: Optional[float] = None,
    delay_ms: int = 0,
    labeler_min_age_seconds: int = 0,
    labeler_limit: int = 200,
    lookahead: Optional[int] = 5,
    drawdown: Optional[float] = 0.005,
    rebound: Optional[float] = 0.003,
    window_seconds: int = 1800,
    bins: int = 10,
    target: Optional[str] = 'bottom',
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    # Optional: seed synthetic logs first (dev/test or ALLOW_ADMIN_DEV_TOOLS)
    seed_count: int = 0,
    seed_probability: Optional[float] = None,
    seed_threshold: Optional[float] = None,
    seed_decision: Optional[int] = None,
    seed_production: bool = False,
    seed_backdate_seconds: int = 0,
    # Optional timing controls
    pre_label_sleep_ms: int = 0,
    calibration_retries: int = 0,
    retry_delay_ms: int = 300,
    # Optional model selection passthrough to batch predictions
    prefer_latest: bool = False,
    version: Optional[str] = None,
    # Optional skip flags to isolate steps
    skip_predict: bool = False,
    skip_label: bool = False,
    # Optional: skip queue flush (useful when testing labeling/calibration only)
    skip_flush: bool = False,
    # Optional: compact response for dashboards/lightweight polling
    compact: bool = False,
    # Optional: number of decimals to round displayed numeric metrics
    decimals: Optional[int] = None,
    # Optional: health criteria for quick pass/fail
    ece_target: Optional[float] = None,
    min_samples: Optional[int] = None,
    max_mce: Optional[float] = None,
):
    """Server-side helper: generate predictions → flush → label → fetch live calibration.

    Designed for local validation and Windows shells where loops are awkward.
    """
    # 0) optionally seed synthetic logs to avoid shell loops (dev/test convenience)
    run_id = f"qv-{int(time.time()*1000)}-{random.randint(1000,9999)}"
    t0_all = time.perf_counter()
    seed_summary = None
    errors: list[str] = []
    if isinstance(seed_count, int) and seed_count > 0:
        try:
            seed_summary = await admin_seed_inference_logs(
                count=seed_count,
                probability=(seed_probability if isinstance(seed_probability, (int,float)) else 0.7),
                threshold=(seed_threshold if isinstance(seed_threshold, (int,float)) else (threshold if isinstance(threshold, (int,float)) else 0.5)),
                decision=seed_decision,
                production=seed_production,
                symbol=symbol,
                interval=interval,
                backdate_seconds=seed_backdate_seconds,
            )
        except HTTPException as _e:
            # propagate seed guard failures; otherwise continue best effort
            if _e.status_code == 403:
                raise
        except Exception as e:
            seed_summary = {"status": "error"}
            errors.append("seed_error")
    # 0.5) initial diagnose snapshot (scoped to target/symbol/interval if provided)
    try:
        diag_before = await admin_labeler_diagnose(min_age_seconds=labeler_min_age_seconds, limit=max(1000, labeler_limit), only_target=target, only_symbol=symbol, only_interval=interval)  # type: ignore[arg-type]
    except Exception:
        diag_before = {"status": "error"}
    # 1) batch predictions (optional)
    t_pred0 = time.perf_counter()
    batch_res = None
    if not bool(skip_predict):
        try:
            batch_res = await admin_inference_predict_batch(count=count, threshold=threshold, delay_ms=delay_ms, symbol=symbol, interval=interval, prefer_latest=prefer_latest, version=version)
        except Exception:
            errors.append("batch_error")
    t_pred1 = time.perf_counter()
    # 2) flush queue (optional)
    q = get_inference_log_queue()
    q_before = None
    try:
        if hasattr(q, "size") and callable(getattr(q, "size")):
            v = await q.size() if asyncio.iscoroutinefunction(q.size) else q.size()
            q_before = int(v)
    except Exception:
        q_before = None
    try:
        if not bool(skip_flush):
            flushed = await q.flush_now()
        else:
            flushed = None
    except Exception:
        flushed = 0
        errors.append("flush_error")
    # queue size after flush
    q_after = None
    try:
        if hasattr(q, "size") and callable(getattr(q, "size")):
            v = await q.size() if asyncio.iscoroutinefunction(q.size) else q.size()
            q_after = int(v)
    except Exception:
        q_after = None
    # 2.5) capture calibration snapshot before labeling (realized-only, so safe after flush)
    cal_before = await inference_live_calibration(window_seconds=window_seconds, bins=bins, symbol=symbol, interval=interval, target=target)  # type: ignore[arg-type]
    # 3) run labeler once with overrides
    from backend.apps.training.service.auto_labeler import get_auto_labeler_service
    svc = get_auto_labeler_service()
    t_label0 = time.perf_counter()
    label_res = None
    if not bool(skip_label):
        try:
            label_res = await svc.run_once(
                min_age_seconds=labeler_min_age_seconds,
                limit=labeler_limit,
                lookahead=lookahead,
                drawdown=drawdown,
                rebound=rebound,
                # Scope labeling to the same group used for predictions/calibration when provided
                only_target=target,
                only_symbol=symbol,
                only_interval=interval,
            )
        except Exception:
            errors.append("label_error")
    t_label1 = time.perf_counter()
    # 3.5) optional sleep to allow lookahead/flush effects
    if pre_label_sleep_ms and pre_label_sleep_ms > 0:
        try:
            await asyncio.sleep(max(0.0, float(pre_label_sleep_ms) / 1000.0))
        except Exception:
            pass
    # 4) fetch live calibration snapshot (with optional retries)
    try:
        cal = await inference_live_calibration(window_seconds=window_seconds, bins=bins, symbol=symbol, interval=interval, target=target)  # type: ignore[arg-type]
    except Exception:
        cal = {"status": "error"}
        errors.append("calibration_error")
    tries = 0
    while isinstance(cal, dict) and cal.get("status") == "no_data" and isinstance(calibration_retries, int) and tries < max(0, calibration_retries):
        tries += 1
        d = max(0.0, float(retry_delay_ms) / 1000.0) if isinstance(retry_delay_ms, (int, float)) else 0.3
        try:
            await asyncio.sleep(d)
        except Exception:
            break
        try:
            cal = await inference_live_calibration(window_seconds=window_seconds, bins=bins, symbol=symbol, interval=interval, target=target)  # type: ignore[arg-type]
        except Exception:
            cal = {"status": "error"}
            errors.append("calibration_error")
    # 5) post-label diagnose snapshot
    try:
        diag_after = await admin_labeler_diagnose(min_age_seconds=labeler_min_age_seconds, limit=max(1000, labeler_limit), only_target=target, only_symbol=symbol, only_interval=interval)  # type: ignore[arg-type]
    except Exception:
        diag_after = {"status": "error"}
    # Compute simple deltas for visibility
    def _safe_total(diag: Any) -> int | None:
        try:
            v = diag.get("total") if isinstance(diag, dict) else None
            return int(v) if isinstance(v, (int, float)) else None
        except Exception:
            return None
    def _group_count(diag: Any, sym: str | None, itv: str | None, tgt: str | None) -> int | None:
        try:
            if not (isinstance(diag, dict) and isinstance(diag.get("groups"), list)):
                return None
            for g in diag["groups"]:
                if not isinstance(g, dict):
                    continue
                if (
                    (sym is None or str(g.get("symbol")).upper() == str(sym).upper())
                    and (itv is None or str(g.get("interval")) == str(itv))
                    and (tgt is None or str(g.get("target")).lower() == str(tgt).lower())
                ):
                    try:
                        return int(g.get("count"))
                    except Exception:
                        return None
            return 0
        except Exception:
            return None
    total_before = _safe_total(diag_before)
    total_after = _safe_total(diag_after)
    delta_total = (total_after - total_before) if (total_before is not None and total_after is not None) else None
    scoped_before = _group_count(diag_before, symbol, interval, target)
    scoped_after = _group_count(diag_after, symbol, interval, target)
    delta_scoped = (scoped_after - scoped_before) if (scoped_before is not None and scoped_after is not None) else None
    # Summarize
    # Extract labeled count if present
    try:
        labeled_cnt = int(label_res.get("labeled")) if isinstance(label_res, dict) and label_res.get("labeled") is not None else None
    except Exception:
        labeled_cnt = None
    # pick up request_id from middleware if present
    try:
        req_id = getattr(request.state, 'request_id', None)
    except Exception:
        req_id = None
    summary = {
        "batch": ({k: batch_res.get(k) for k in ("attempted","success","errors","symbol","interval","threshold")} if isinstance(batch_res, dict) else None),
        "flushed": flushed,
        "label": label_res,
        "labeled": labeled_cnt,
        "seed": seed_summary,
        "scope": {"target": target, "symbol": symbol, "interval": interval},
        "now_ts": time.time(),
        "run_id": run_id,
        "request_id": req_id,
    "queue": {"before": q_before, "after": q_after},
        "params": {
            "count": count,
            "threshold": threshold,
            "delay_ms": delay_ms,
            "labeler_min_age_seconds": labeler_min_age_seconds,
            "labeler_limit": labeler_limit,
            "lookahead": lookahead,
            "drawdown": drawdown,
            "rebound": rebound,
            "window_seconds": window_seconds,
            "bins": bins,
            "prefer_latest": prefer_latest,
            "version": version,
            "skip_predict": skip_predict,
            "skip_label": skip_label,
            "skip_flush": skip_flush,
            "compact": compact,
            "decimals": decimals,
            "ece_target": ece_target,
            "min_samples": min_samples,
            "max_mce": max_mce,
            "pre_label_sleep_ms": pre_label_sleep_ms,
            "calibration_retries": calibration_retries,
            "retry_delay_ms": retry_delay_ms,
        },
        "timing": {
            "predict_sec": (t_pred1 - t_pred0),
            "label_sec": (t_label1 - t_label0),
            "total_sec": None,  # filled after end
        },
        "calibration_retries_used": tries,
        "slept_ms_before_label": pre_label_sleep_ms,
        "diagnose_before": {k: diag_before.get(k) for k in ("status","total","groups")} if isinstance(diag_before, dict) else None,
        "diagnose_after": {k: diag_after.get(k) for k in ("status","total","groups")} if isinstance(diag_after, dict) else None,
        "diagnose_delta_total": delta_total,
        "diagnose_delta_scoped": delta_scoped,
        "errors": errors,
    }
    # Build calibration summaries (before/after) and deltas
    def _cal_sum(obj: Any) -> Dict[str, Any]:
        if isinstance(obj, dict):
            return {k: obj.get(k) for k in ("status","ece","brier","mce","samples","window_seconds","bins","target")}
        return {"status": "unknown"}
    cal_sum = _cal_sum(cal)
    cal_before_sum = _cal_sum(cal_before)
    def _num(x: Any) -> float | None:
        try:
            return float(x)
        except Exception:
            return None
    def _intn(x: Any) -> int | None:
        try:
            return int(x)
        except Exception:
            return None
    cal_delta = {
        "ece": (_num(cal_sum.get("ece")) - _num(cal_before_sum.get("ece"))
                 if (_num(cal_sum.get("ece")) is not None and _num(cal_before_sum.get("ece")) is not None) else None),
        "brier": (_num(cal_sum.get("brier")) - _num(cal_before_sum.get("brier"))
                   if (_num(cal_sum.get("brier")) is not None and _num(cal_before_sum.get("brier")) is not None) else None),
        "mce": (_num(cal_sum.get("mce")) - _num(cal_before_sum.get("mce"))
                 if (_num(cal_sum.get("mce")) is not None and _num(cal_before_sum.get("mce")) is not None) else None),
        "samples": (_intn(cal_sum.get("samples")) - _intn(cal_before_sum.get("samples"))
                     if (_intn(cal_sum.get("samples")) is not None and _intn(cal_before_sum.get("samples")) is not None) else None),
    }
    # Production ECE lookup and live gap vs production (drift context)
    prod_ece: float | None = None
    try:
        repo = ModelRegistryRepository()
        rows = await repo.fetch_latest(cfg.auto_promote_model_name, "supervised", limit=5)
        prod = None
        for r in rows:
            try:
                if r.get("status") == "production":
                    prod = r
                    break
            except Exception:
                continue
        if not prod and rows:
            prod = rows[0]
        if prod:
            mets = prod.get("metrics") or {}
            v = mets.get("ece")
            if isinstance(v, (int, float)):
                prod_ece = float(v)
    except Exception:
        prod_ece = None
    ece_gap_to_prod: float | None = None
    ece_gap_to_prod_rel: float | None = None
    try:
        live_ece = cal_sum.get("ece") if isinstance(cal_sum, dict) else None
        if isinstance(live_ece, (int, float)) and isinstance(prod_ece, (int, float)):
            ece_gap_to_prod = float(live_ece) - float(prod_ece)
            if float(prod_ece) != 0:
                ece_gap_to_prod_rel = ece_gap_to_prod / float(prod_ece)
    except Exception:
        ece_gap_to_prod = None
        ece_gap_to_prod_rel = None
    summary["production_ece"] = prod_ece
    summary["ece_gap_to_prod"] = ece_gap_to_prod
    summary["ece_gap_to_prod_rel"] = ece_gap_to_prod_rel
    # Result code & progress flags for quick triage
    result_code = "ok"
    try:
        if isinstance(labeled_cnt, int) and labeled_cnt > 0:
            result_code = "labeled"
        elif isinstance(cal, dict) and cal.get("status") == "no_data":
            result_code = "no_data"
        elif isinstance(cal, dict) and cal.get("status") == "ok":
            result_code = "calibrated"
    except Exception:
        pass
    progress = {
        "diagnose_scoped_reduced": (delta_scoped is not None and isinstance(delta_scoped, (int, float)) and delta_scoped < 0),
        "diagnose_total_reduced": (delta_total is not None and isinstance(delta_total, (int, float)) and delta_total < 0),
        "labeled_positive": (isinstance(labeled_cnt, int) and labeled_cnt > 0),
        "calibration_improved": (cal_delta.get("ece") is not None and isinstance(cal_delta.get("ece"), (int, float)) and float(cal_delta.get("ece")) < 0),
        "samples_increase": (cal_delta.get("samples") is not None and isinstance(cal_delta.get("samples"), (int, float)) and float(cal_delta.get("samples")) > 0),
    }
    # warnings/hints for UI visibility
    warnings: list[str] = []
    try:
        if isinstance(cal, dict) and cal.get("status") == "no_data":
            warnings.append("calibration_no_data")
    except Exception:
        pass
    if bool(skip_predict):
        warnings.append("predict_skipped")
    if bool(skip_label):
        warnings.append("label_skipped")
    if bool(skip_flush):
        warnings.append("flush_skipped")
    if isinstance(tries, int) and tries > 0:
        warnings.append("calibration_retried")
    summary["result_code"] = result_code
    summary["progress"] = progress
    summary["warnings"] = warnings
    # human-friendly hints based on warnings/result
    hints: list[str] = []
    if "calibration_no_data" in warnings:
        hints.append("no_data: 라벨 생성 후 잠시 대기하거나 calibration_retries/retry_delay_ms, window_seconds를 조정하세요")
    if "predict_skipped" in warnings:
        hints.append("skip_predict=true: 최근 예측 없이 라벨/캘리브레이션만 점검 중")
    if "label_skipped" in warnings:
        hints.append("skip_label=true: 라벨 생성 없이 예측/큐/캘리브레이션만 확인 중")
    if "flush_skipped" in warnings:
        hints.append("skip_flush=true: 큐에 미처리 예측이 남아 있을 수 있음")
    if "calibration_retried" in warnings:
        hints.append("캘리브레이션 재시도 수행됨: window_seconds 또는 labeler_min_age_seconds 조정 고려")
    if isinstance(summary.get("errors"), list) and summary["errors"]:
        hints.append("에러 발생: summary.errors 확인")
    if result_code == "labeled":
        hints.append("라벨 생성됨: 샘플 증가 및 ECE 변화를 확인하세요")
    if result_code == "calibrated":
        hints.append("캘리브레이션 데이터 확보: ECE/brier/mce 추이를 확인하세요")
    try:
        if isinstance(ece_gap_to_prod, (int, float)) and ece_gap_to_prod > 0:
            hints.append("live ECE가 prod보다 높음: 드리프트 가능성 점검")
    except Exception:
        pass
    summary["hints"] = hints

    # Health criteria evaluation
    health_pass = True
    health_reasons: list[str] = []
    try:
        cal_status = cal_sum.get("status") if isinstance(cal_sum, dict) else None
        if cal_status != "ok":
            health_pass = False
            health_reasons.append("no_calibration")
        ece_val = cal_sum.get("ece") if isinstance(cal_sum, dict) else None
        mce_val = cal_sum.get("mce") if isinstance(cal_sum, dict) else None
        samples_val = cal_sum.get("samples") if isinstance(cal_sum, dict) else None
        if isinstance(ece_target, (int, float)) and isinstance(ece_val, (int, float)):
            if float(ece_val) > float(ece_target):
                health_pass = False
                health_reasons.append("ece_above_target")
        if isinstance(min_samples, int) and isinstance(samples_val, (int, float)):
            if int(samples_val) < int(min_samples):
                health_pass = False
                health_reasons.append("insufficient_samples")
        if isinstance(max_mce, (int, float)) and isinstance(mce_val, (int, float)):
            if float(mce_val) > float(max_mce):
                health_pass = False
                health_reasons.append("mce_above_max")
        if isinstance(errors, list) and errors:
            health_pass = False
            health_reasons.append("step_errors")
    except Exception:
        health_pass = False
        health_reasons.append("health_eval_error")
    summary["health"] = {
        "pass": bool(health_pass),
        "reasons": health_reasons,
        "ece_target": ece_target,
        "min_samples": min_samples,
        "max_mce": max_mce,
    }
    # Surface model metadata from last batch item when available
    try:
        if isinstance(batch_res, dict) and isinstance(batch_res.get("last"), dict):
            last = batch_res["last"]
            summary["model"] = {
                "name": last.get("model_name"),
                "version": last.get("model_version"),
                "production": last.get("used_production"),
            }
    except Exception:
        pass
    # finalize total timing
    try:
        summary["timing"]["total_sec"] = time.perf_counter() - t0_all
    except Exception:
        pass

    if bool(compact):
        # Build minimal summary for dashboards / lightweight polling
        batch_attempted = None
        batch_success = None
        if isinstance(summary.get("batch"), dict):
            try:
                batch_attempted = summary["batch"].get("attempted")
                batch_success = summary["batch"].get("success")
            except Exception:
                batch_attempted = None
                batch_success = None
        compact_summary = {
            "result_code": summary.get("result_code"),
            "progress": summary.get("progress"),
            "warnings": summary.get("warnings"),
            "hints": summary.get("hints"),
            "errors": summary.get("errors"),
            "scope": summary.get("scope"),
            "now_ts": summary.get("now_ts"),
            "request_id": summary.get("request_id"),
            "run_id": summary.get("run_id"),
            "batch_attempted": batch_attempted,
            "batch_success": batch_success,
            "flushed": summary.get("flushed"),
            "labeled": summary.get("labeled"),
            "total_sec": (summary.get("timing") or {}).get("total_sec"),
            "model": summary.get("model"),
            "queue": summary.get("queue"),
            "health": summary.get("health"),
            "ece_gap_to_prod": summary.get("ece_gap_to_prod"),
            "ece_gap_to_prod_rel": summary.get("ece_gap_to_prod_rel"),
        }
        # Apply rounding if requested
        if isinstance(decimals, int) and decimals >= 0:
            try:
                if isinstance(compact_summary.get("total_sec"), (int, float)):
                    compact_summary["total_sec"] = round(float(compact_summary["total_sec"]), decimals)
                if isinstance(compact_summary.get("ece_gap_to_prod"), (int, float)):
                    compact_summary["ece_gap_to_prod"] = round(float(compact_summary["ece_gap_to_prod"]), decimals)
                if isinstance(compact_summary.get("ece_gap_to_prod_rel"), (int, float)):
                    compact_summary["ece_gap_to_prod_rel"] = round(float(compact_summary["ece_gap_to_prod_rel"]), decimals)
                # calibration_delta present at top-level
                for k in ("ece", "brier", "mce", "samples"):
                    v = cal_delta.get(k)
                    if isinstance(v, (int, float)):
                        cal_delta[k] = round(float(v), decimals)
            except Exception:
                pass
        return {
            "status": "ok",
            "compact": True,
            "summary": compact_summary,
            "calibration_delta": cal_delta,
        }

    # Optional rounding for selected numeric outputs
    if isinstance(decimals, int) and decimals >= 0:
        def _round_inplace(obj: dict, keys: list[str]):
            for k in keys:
                v = obj.get(k)
                try:
                    if isinstance(v, (int, float)):
                        obj[k] = round(float(v), decimals)
                except Exception:
                    pass
        # Round calibration deltas and metrics
        _round_inplace(cal_delta, ["ece", "brier", "mce", "samples"])  # samples is int; safe to round
        _round_inplace(cal_sum, ["ece", "brier", "mce", "samples"])
        _round_inplace(cal_before_sum, ["ece", "brier", "mce", "samples"])
        # Round timing seconds
        if isinstance(summary.get("timing"), dict):
            _round_inplace(summary["timing"], ["predict_sec", "label_sec", "total_sec"])
        # Round production_ece and ece_gap_to_prod
        try:
            if isinstance(summary.get("production_ece"), (int, float)):
                summary["production_ece"] = round(float(summary["production_ece"]), decimals)
            if isinstance(summary.get("ece_gap_to_prod"), (int, float)):
                summary["ece_gap_to_prod"] = round(float(summary["ece_gap_to_prod"]), decimals)
            if isinstance(summary.get("ece_gap_to_prod_rel"), (int, float)):
                summary["ece_gap_to_prod_rel"] = round(float(summary["ece_gap_to_prod_rel"]), decimals)
        except Exception:
            pass
    return {"status": "ok", "summary": summary, "calibration_before": cal_before_sum, "calibration": cal_sum, "calibration_delta": cal_delta}

# --- Admin: Labeler diagnose (counts by group) ---
@app.get("/admin/inference/labeler/diagnose", dependencies=[Depends(require_api_key)])
async def admin_labeler_diagnose(min_age_seconds: int = 0, limit: int = 500, only_target: Optional[str] = None, only_symbol: Optional[str] = None, only_interval: Optional[str] = None):
    from backend.apps.training.repository.inference_log_repository import InferenceLogRepository
    import json as _json
    repo = InferenceLogRepository()
    rows = await repo.fetch_unlabeled_candidates(min_age_seconds=min_age_seconds, limit=limit)
    summary: dict[tuple[str,str,str], int] = {}
    total = 0
    for r in rows:
        sym = str(r.get("symbol") or cfg.symbol)
        itv = str(r.get("interval") or cfg.kline_interval)
        extra = r.get("extra") or {}
        if isinstance(extra, str):
            try:
                extra = _json.loads(extra)
            except Exception:
                extra = {}
        tgt = str((extra.get("target") if isinstance(extra, dict) else None) or "bottom")
        if only_target and tgt.lower() != str(only_target).lower():
            continue
        if only_symbol and sym.upper() != str(only_symbol).upper():
            continue
        if only_interval and itv != str(only_interval):
            continue
        key = (sym, itv, tgt)
        summary[key] = summary.get(key, 0) + 1
        total += 1
    # format as list for JSON friendliness
    items = [
        {"symbol": k[0], "interval": k[1], "target": k[2], "count": v}
        for k, v in sorted(summary.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return {"status": "ok", "total": total, "groups": items}

@app.get("/api/inference/activity/summary")
async def inference_activity_summary(_auth: bool = Depends(require_api_key)):
    """최근 의사결정 활동 요약.

    응답 필드:
      last_decision_ts: 마지막 결정 unix epoch (float, 없으면 null)
      decisions_1m / decisions_5m: 최근 60초 / 300초 내 승인된(inference_log) 결정 수
      auto_loop_enabled: 자동 inference 루프 활성 여부
      interval: 자동 루프 주기(초) (활성 아닐 시 null)
    """
    # Fetch recent inference logs counts
    repo = InferenceLogRepository()
    now = time.time()
    # heuristic: fetch recent N rows and count in python (N bounded)
    rows = await repo.fetch_recent(limit=500)
    last_ts = None
    c1 = 0
    c5 = 0
    for r in rows:
        created_at = r.get("created_at")
        if created_at is None:
            continue
        try:
            ts = created_at.timestamp() if hasattr(created_at, "timestamp") else float(created_at)
        except Exception:
            continue
        if last_ts is None or ts > last_ts:
            last_ts = ts
        age = now - ts
        if age <= 60:
            c1 += 1
        if age <= 300:
            c5 += 1
    runtime_enabled = getattr(app.state, 'auto_inference_enabled_override', None)
    interval_override = getattr(app.state, 'auto_inference_interval_override', None)
    auto_enabled = cfg.inference_auto_loop_enabled if runtime_enabled is None else bool(runtime_enabled)
    interval = None
    if auto_enabled:
        interval = interval_override if (isinstance(interval_override, (int,float)) and interval_override and interval_override > 0) else cfg.inference_auto_loop_interval
    disable_reason = None
    if not auto_enabled:
        if runtime_enabled is False:
            disable_reason = "runtime_disabled"
        elif runtime_enabled is None and not cfg.inference_auto_loop_enabled:
            disable_reason = "config_disabled"
        elif interval is None:
            disable_reason = disable_reason or "inactive"
    return {
        "status": "ok",
        "last_decision_ts": last_ts,
        "decisions_1m": c1,
        "decisions_5m": c5,
        "auto_loop_enabled": auto_enabled,
        "interval": interval,
        "override": runtime_enabled is not None,
        "interval_override": interval_override if runtime_enabled is not None else None,
        "disable_reason": disable_reason,
    }

# --- Admin: Seed synthetic inference logs (dev/test helper) ---
@app.post("/admin/inference/seed-logs", dependencies=[Depends(require_api_key)])
async def admin_seed_inference_logs(
    count: int = 5,
    probability: float = 0.7,
    threshold: float = 0.5,
    decision: Optional[int] = None,
    production: bool = False,
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    backdate_seconds: int = 0,
    model_name: Optional[str] = None,
    model_version: Optional[str] = None,
):
    """Insert synthetic inference logs for quick validation without client loops.

    Notes:
      - Adds extra.target='bottom' so labeler can scope correctly.
      - Use backdate_seconds>0 to make them immediately eligible for labeling (min_age).
    """
    # Safety: restrict in non-dev/test unless explicitly allowed
    try:
        app_env = str(getattr(cfg, 'app_env', '')).lower()
    except Exception:
        app_env = ''
    if app_env not in ("dev", "test"):
        import os as _os
        allow = str(_os.getenv("ALLOW_ADMIN_DEV_TOOLS", "")).lower() in ("1", "true", "yes")
        if not allow:
            raise HTTPException(status_code=403, detail="seed_logs_disabled_in_env")
    if count < 1 or count > 1000:
        raise HTTPException(status_code=400, detail="count_out_of_range")
    try:
        p = float(probability)
    except Exception:
        p = 0.7
    if p < 0: p = 0.0
    if p > 1: p = 1.0
    try:
        thr = float(threshold)
    except Exception:
        thr = 0.5
    if thr <= 0 or thr >= 1:
        thr = 0.5
    use_symbol = symbol or cfg.symbol
    use_interval = interval or cfg.kline_interval
    mname = model_name or "bottom_predictor"
    mver = model_version or "seed"
    dec = int(decision) if isinstance(decision, int) else (1 if p >= thr else 0)
    repo = InferenceLogRepository()
    ids: list[int] = []
    # Insert individually to get IDs
    for _ in range(count):
        try:
            new_id = await repo.insert(
                use_symbol,
                use_interval,
                mname,
                mver,
                p,
                dec,
                thr,
                bool(production),
                {"seed": True, "target": "bottom"},
            )
            if isinstance(new_id, int):
                ids.append(new_id)
        except Exception as e:  # noqa: BLE001
            # Continue best-effort; collect partial
            continue
    # Optionally backdate created_at to pass min_age immediately
    if ids and backdate_seconds and backdate_seconds > 0:
        try:
            pool = pool_status().get("pool")  # not available; use connection helper below
        except Exception:
            pool = None
        # use connection helper directly
        try:
            from backend.common.db.connection import init_pool as _init_pool
            pool = await _init_pool()
            if pool is not None:
                async with pool.acquire() as conn:  # type: ignore[attr-defined]
                    await conn.execute(
                        """
                        UPDATE model_inference_log
                        SET created_at = created_at - ($1 || ' seconds')::interval
                        WHERE id = ANY($2::int[])
                        """,
                        int(backdate_seconds),
                        ids,
                    )
        except Exception:
            pass
    return {
        "status": "ok",
        "inserted": len(ids),
        "symbol": use_symbol,
        "interval": use_interval,
        "threshold": thr,
        "probability": p,
        "decision": dec,
        "backdated_sec": int(backdate_seconds) if backdate_seconds and backdate_seconds > 0 else 0,
    }

@app.get("/api/monitor/calibration/status")
async def calibration_monitor_status():
    """Return current calibration drift monitor status with streaks and last snapshot.

    Fields:
      enabled: whether monitor enabled via config
      abs_streak / rel_streak: current consecutive drift counts (reset on non-drift)
      last_snapshot: latest evaluation payload (or null if none yet)
      thresholds: absolute & relative thresholds from config
      window_seconds / min_samples / interval: monitor config values for observability
    """
    abs_streak = getattr(_streak_state, 'abs_streak', 0)
    rel_streak = getattr(_streak_state, 'rel_streak', 0)
    last_snapshot = getattr(_streak_state, 'last_snapshot', None)
    # Derive recommendation
    reasons: list[str] = []
    recommend_retrain = False
    if cfg.calibration_monitor_enabled and last_snapshot:
        # If any threshold currently breached and streak exceeds trigger
        abs_trigger = cfg.calibration_monitor_abs_streak_trigger
        rel_trigger = cfg.calibration_monitor_rel_streak_trigger
        if last_snapshot.get("abs_drift") and abs_streak >= abs_trigger:
            recommend_retrain = True
            reasons.append(f"abs_drift_streak_{abs_streak}_gte_{abs_trigger}")
        if last_snapshot.get("rel_drift") and rel_streak >= rel_trigger:
            recommend_retrain = True
            reasons.append(f"rel_drift_streak_{rel_streak}_gte_{rel_trigger}")
        # Additional heuristic: large current delta (2x abs threshold) even without streak
        delta = last_snapshot.get("delta")
        if isinstance(delta, (int, float)) and delta is not None:
            if delta >= 2 * cfg.calibration_monitor_ece_drift_abs:
                recommend_retrain = True
                reasons.append("delta_gt_2x_abs_threshold")
    # Retrain 효과 평가: scheduler STATE 에 저장된 delta_before 가 있고 retrain 이후 최소 한 번 스냅샷이 업데이트 된 경우 개선율 계산
    delta_before = None
    delta_improvement = None
    effect_evaluated_ts = None
    try:
        from backend.apps.training.auto_retrain_scheduler import STATE as _rt_state  # type: ignore
        delta_before = getattr(_rt_state, 'last_calibration_delta_before', None)
        last_retrain_ts = getattr(_rt_state, 'last_calibration_retrain_ts', None)
        last_effect_snapshot_ts = getattr(_rt_state, 'last_calibration_effect_snapshot_ts', None)
        if delta_before is not None and isinstance(last_snapshot, dict):
            snap_ts = last_snapshot.get('ts')
            cur_delta = last_snapshot.get('delta')
            if isinstance(cur_delta, (int, float)) and isinstance(snap_ts, (int, float)) and last_retrain_ts is not None:
                # retrain 이후 snapshot 이고 아직 이 snapshot 으로 평가 안했으면 진행
                if snap_ts >= last_retrain_ts and (last_effect_snapshot_ts is None or snap_ts > last_effect_snapshot_ts):
                    if delta_before > 0:
                        delta_improvement = (delta_before - cur_delta) / delta_before
                    else:
                        delta_improvement = 0.0 if cur_delta == 0 else None
                    effect_evaluated_ts = time.time()
                    result_label = None
                    if delta_improvement is not None:
                        if delta_improvement > 0.0001:
                            result_label = "improved"
                        elif delta_improvement < -0.0001:
                            result_label = "worsened"
                        else:
                            result_label = "no_change"
                    try:
                        CALIBRATION_RETRAIN_DELTA_IMPROVEMENT.set(delta_improvement if delta_improvement is not None else 0)
                        CALIBRATION_RETRAIN_EFFECT_EVAL_TS.set(effect_evaluated_ts)
                        setattr(_rt_state, 'last_calibration_effect_evaluated_ts', effect_evaluated_ts)
                        setattr(_rt_state, 'last_calibration_effect_snapshot_ts', snap_ts)
                        if result_label:
                            CALIBRATION_RETRAIN_IMPROVEMENT_EVENTS.labels(result=result_label).inc()
                    except Exception:
                        pass
    except Exception:
        pass
    # Emit metrics for recommendation and transitions
    try:
        prev = getattr(_streak_state, 'last_recommend', False)
        if recommend_retrain:
            CALIBRATION_RETRAIN_RECOMMEND.set(1)
            if not prev:
                CALIBRATION_RETRAIN_RECOMMEND_EVENTS.labels(state="enter").inc()
        else:
            CALIBRATION_RETRAIN_RECOMMEND.set(0)
            if prev:
                CALIBRATION_RETRAIN_RECOMMEND_EVENTS.labels(state="exit").inc()
        _streak_state.last_recommend = recommend_retrain
    except Exception:
        pass
    return {
        "enabled": bool(cfg.calibration_monitor_enabled),
        "labeler_enabled": bool(cfg.auto_labeler_enabled),
        "abs_streak": abs_streak,
        "rel_streak": rel_streak,
        "last_snapshot": last_snapshot,
        "thresholds": {
            "abs": cfg.calibration_monitor_ece_drift_abs,
            "rel": cfg.calibration_monitor_ece_drift_rel,
            "abs_streak_trigger": cfg.calibration_monitor_abs_streak_trigger,
            "rel_streak_trigger": cfg.calibration_monitor_rel_streak_trigger,
        },
        "window_seconds": cfg.calibration_monitor_window_seconds,
        "min_samples": cfg.calibration_monitor_min_samples,
        "interval": cfg.calibration_monitor_interval,
        "recommend_retrain": recommend_retrain,
        "recommend_retrain_reasons": reasons,
        "calibration_retrain_delta_before": delta_before,
        "calibration_retrain_delta_improvement": delta_improvement,
        "calibration_retrain_effect_evaluated_ts": effect_evaluated_ts,
    }

@app.get("/api/system/status")
async def system_status(_auth: bool = Depends(require_api_key)):
    return await _build_system_status()

async def _build_system_status():
    """공통 시스템 상태 스냅샷 빌더 (엔드포인트 + 메트릭 루프 공유).

    Returns dict:
      timestamp, overall, fast_startup, skipped_components, degraded_components,
      db, components{ ingestion, features, news, drift, calibration_monitor, risk }
    """
    now = time.time()
    fast = bool(getattr(app.state, 'fast_startup', False))
    skipped = list(getattr(app.state, 'skipped_components', []))
    degraded = list(getattr(app.state, 'degraded_components', []))
    db_info = pool_status()
    # ingestion
    try:
        ing = await ingestion_status()
    except Exception as e:  # pragma: no cover
        ing = {"status": "error", "error": str(e)}
    # features
    try:
        feat = await feature_status()
        if feat.get("running") and feat.get("last_success_ts"):
            lag = now - feat["last_success_ts"] if feat["last_success_ts"] else None
            if lag is not None:
                feat["lag_sec"] = lag
                feat["health"] = "ok" if lag <= cfg.health_max_feature_lag_sec else "degraded"
        else:
            feat["health"] = "stopped" if not feat.get("running") else "pending"
    except Exception as e:
        feat = {"status": "error", "error": str(e)}
    # news
    try:
        svc: NewsService = getattr(app.state, 'news_service')
        nst = svc.status()
        nst["fast_startup_skipped"] = 'news_ingestion' in skipped
        news_snap = nst
    except Exception as e:
        news_snap = {"status": "error", "error": str(e)}
    # drift summary
    drift_snap: Optional[dict[str, Any]] = None
    try:
        hist: list[dict[str, Any]] = getattr(app.state, 'drift_history', [])  # type: ignore
        if hist:
            last = hist[-1]
            drift_snap = {k: last.get(k) for k in ["ts","drift_count","total","max_abs_z","top_feature","applied_threshold"]}
        elif getattr(app.state, 'last_drift_summary', None):
            drift_snap = getattr(app.state, 'last_drift_summary')  # type: ignore
    except Exception:
        drift_snap = {"status": "error"}
    # calibration
    try:
        calib = await calibration_monitor_status()
    except Exception as e:
        calib = {"status": "error", "error": str(e)}
    # risk
    try:
        sess = risk_engine.session
        risk_snap = {
            "current_equity": sess.current_equity,
            "peak_equity": sess.peak_equity,
            "drawdown_ratio": ((sess.peak_equity - sess.current_equity)/sess.peak_equity if sess.peak_equity > 0 else 0.0),
        }
        total_notional = 0.0
        for pos in risk_engine.positions.values():
            if pos.size != 0 and pos.entry_price > 0:
                total_notional += abs(pos.size * pos.entry_price)
        risk_snap["notional_exposure"] = total_notional
        risk_snap["notional_utilization"] = (total_notional / risk_engine.limits.max_notional) if risk_engine.limits.max_notional > 0 else 0.0
    except Exception as e:
        risk_snap = {"status": "error", "error": str(e)}
    # overall
    overall = "ok"
    if not db_info.get("has_pool"):
        overall = "error"
    flags = []
    if isinstance(ing, dict) and ing.get("enabled") and (ing.get("running") is False or ing.get("stale")):
        flags.append("degraded")
    if isinstance(feat, dict) and feat.get("health") in ("degraded","stopped"):
        flags.append("degraded")
    if degraded:
        flags.append("degraded")
    if overall != "error" and any(f == "degraded" for f in flags):
        overall = "degraded"
    return {
        "timestamp": now,
        "overall": overall,
        "fast_startup": fast,
        "skipped_components": skipped,
        "degraded_components": degraded,
        "db": db_info,
        "components": {
            "ingestion": ing,
            "features": feat,
            "news": news_snap,
            "drift": drift_snap,
            "calibration_monitor": calib,
            "risk": risk_snap,
        },
    }

def _status_to_numeric(status: str) -> int:
    if status == "ok": return 2
    if status == "degraded": return 1
    if status == "error": return 0
    return -1

async def system_health_metrics_loop(interval: float = 10.0):
    """Background loop to periodically emit overall & component health gauges using system_status logic.
    Keeps scrape overhead low by centralizing computation.
    """
    while True:
        try:
            snap = await _build_system_status()
            SYSTEM_OVERALL_STATUS.set(_status_to_numeric(snap.get("overall")))
            # component mappings
            def set_comp(name: str, health: Optional[str]):
                if health is None:
                    SYSTEM_COMPONENT_HEALTH.labels(component=name).set(-1)
                else:
                    SYSTEM_COMPONENT_HEALTH.labels(component=name).set(_status_to_numeric(health if health in ("ok","degraded","error") else ("degraded" if health in ("stopped","pending") else "ok")))
            comps = snap.get("components", {}) or {}
            # ingestion
            ing = comps.get("ingestion") or {}
            if ing.get("enabled"):
                if ing.get("running") is False or ing.get("stale"):
                    set_comp("ingestion", "degraded")
                else:
                    set_comp("ingestion", "ok")
            else:
                SYSTEM_COMPONENT_HEALTH.labels(component="ingestion").set(-1)
            # features
            feat = comps.get("features") or {}
            set_comp("features", feat.get("health"))
            # news
            news = comps.get("news") or {}
            if news.get("fast_startup_skipped"):
                SYSTEM_COMPONENT_HEALTH.labels(component="news").set(-1)
            else:
                SYSTEM_COMPONENT_HEALTH.labels(component="news").set(2)
            # drift (informational)
            SYSTEM_COMPONENT_HEALTH.labels(component="drift").set(2)
            # calibration
            calib = comps.get("calibration_monitor") or {}
            SYSTEM_COMPONENT_HEALTH.labels(component="calibration_monitor").set(2 if not calib.get("recommend_retrain") else 1)
            # risk
            SYSTEM_COMPONENT_HEALTH.labels(component="risk").set(2)
        except Exception:
            # catastrophic error -> set overall error (0)
            try:
                SYSTEM_OVERALL_STATUS.set(0)
            except Exception:
                pass
        await asyncio.sleep(interval)

from backend.apps.ingestion.backfill.gap_backfill_service import GapBackfillService  # gap backfill

# ---------------------------------------------------------------------------
# OHLCV WebSocket 실시간 전송 매니저
#  - snapshot: 초기 연결 시 최근 N개 (ASC) + meta (간단 completeness / largest gap)
#  - append: 새로 확정(closed)된 캔들들 (배치) ASC
#  - repair: (향후) 갭 복구로 뒤늦게 채워진 과거 캔들들
# 이벤트 형식 (type 필드): snapshot | append | repair
# candles 항목 공통 필드: open_time, close_time, open, high, low, close, volume, is_closed
# 정책:
#   * snapshot: is_closed=true 만 (include_open 미포함) + optional partial 향후 고려
#   * append: 항상 is_closed=true 만 전송, open_time ASC
#   * repair: 과거 구간 재삽입 (open_time ASC) + meta_delta(optional)
# ---------------------------------------------------------------------------
import json as _json
from typing import Set, Tuple
from fastapi import WebSocket, WebSocketDisconnect

class OhlcvWebSocketManager:
    def __init__(self, symbol: str, interval: str):
        self.symbol = symbol
        self.interval = interval
        self._clients: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        # snapshot 구성 시 사용할 기본 제한
        self.snapshot_limit = 500
        # 진행중 partial (단일 분) 상태 (open_time -> dict)
        self._partial: dict[int, dict] = {}
        self._last_partial_sent: Optional[float] = None
        # gap 추적: append 진행 중 누락된 구간을 세그먼트로 적재 (in-memory, 단일 프로세스 한정)
        # 세그먼트 구조: {
        #   'from_ts': int, 'to_ts': int, 'missing': int, 'state': 'open'|'closed',
        #   '_remaining': set[int]  # 내부 관리용: 아직 미복구 open_time 집합
        # }
        self._gaps: list[dict] = []
        # 가장 최근 확정(append)된 캔들의 open_time (순방향 gap 탐지 기준)
        self._last_closed_open_time: Optional[int] = None
        # delta 제공용 repair 히스토리 버퍼 (open_time ASC 유지 가정). 크기 제한으로 메모리 보호.
        self._repair_history: list[dict] = []  # 각 항목: { 'open_time': int, 'candle': {...}, 'ts_recorded': epoch_ms }
        self._repair_history_max = 5000
        # gap gauge 업데이트용 (prometheus) - lazy import handling
        try:
            from prometheus_client import Gauge as _Gauge  # type: ignore
            global OHLCV_GAP_OPEN_SEGMENTS
            if 'OHLCV_GAP_OPEN_SEGMENTS' not in globals():
                OHLCV_GAP_OPEN_SEGMENTS = _Gauge('ohlcv_gap_open_segments', 'Number of currently open gap segments', ['symbol','interval'])
        except Exception:
            pass

    # ---- Gap 내부 유틸 ----
    def _interval_to_ms(self) -> int:
        it = self.interval
        try:
            unit = it[-1]; val = int(it[:-1])
            if unit == 'm':
                return val * 60_000
            if unit == 'h':
                return val * 60 * 60_000
            if unit == 'd':
                return val * 24 * 60 * 60_000
        except Exception:
            pass
        return 60_000  # fallback 1m

    async def _detect_gap_on_forward(self, new_ot: int):
        """새로운 forward append 캔들 open_time(new_ot)을 관찰했을 때 직전 확정 캔들과의 간극 분석.
        last_closed 기준으로 interval 보다 큰 간극이면 gap_detected 브로드캐스트.
        """
        if self._last_closed_open_time is None:
            self._last_closed_open_time = new_ot
            return
        interval_ms = self._interval_to_ms()
        delta = new_ot - self._last_closed_open_time
        if delta <= interval_ms:  # 정상 연속
            self._last_closed_open_time = new_ot
            return
        # gap 발생
        missing_bars = (delta // interval_ms) - 1
        if missing_bars <= 0:
            self._last_closed_open_time = new_ot
            return
        # 세그먼트 경계 (누락된 구간: last+interval .. new_ot-interval)
        seg_from = self._last_closed_open_time + interval_ms
        seg_to = new_ot - interval_ms
        # 예상 open_time 리스트 (균등 인터벌)
        remaining = set(range(seg_from, seg_to + 1, interval_ms))
        return {
            'from_ts': seg_from,
            'to_ts': seg_to,
            'missing': missing_bars,
            "symbol": self.symbol if hasattr(self, 'symbol') else None,
            "interval": self.interval if hasattr(self, 'interval') else None,
            'state': 'open',
            '_remaining': remaining,
        }
        self._gaps.append(seg)
        # 외부 전송용 사본 (내부 remaining 제거)
        pub_seg = {k: seg.get(k) for k in ['from_ts', 'to_ts', 'missing', 'state', 'symbol', 'interval']}
        try:
            await self.broadcast_gap_detected(pub_seg)
        except Exception:
            pass
        # gauge 업데이트
        try:
            if 'OHLCV_GAP_OPEN_SEGMENTS' in globals():
                open_cnt = sum(1 for g in self._gaps if g.get('state')=='open')
                OHLCV_GAP_OPEN_SEGMENTS.labels(symbol=self.symbol, interval=self.interval).set(open_cnt)
        except Exception:
            pass
        # 마지막 closed 갱신
        self._last_closed_open_time = new_ot

    async def _mark_repaired(self, repaired_open_times: list[int]):
        if not repaired_open_times:
            return
        # 오래된 gap 먼저 순회 (append 순방향 생성이므로 생성 순서대로 보존)
        changed: list[dict] = []
        for ot in repaired_open_times:
            for seg in self._gaps:
                if seg['state'] != 'open':
                    continue
                if ot < seg['from_ts'] or ot > seg['to_ts']:
                    continue
                if ot in seg['_remaining']:
                    seg['_remaining'].discard(ot)
                    if not seg['_remaining']:
                        seg['state'] = 'closed'
                        changed.append(seg)
                # 한 open_time 은 단일 세그먼트에만 속한다고 가정
                break
        for seg in changed:
            pub_seg = {k: seg[k] for k in ['from_ts', 'to_ts', 'missing', 'state']}
            try:
                await self.broadcast_gap_repaired(pub_seg)
            except Exception:
                pass
        # gauge 업데이트 (변경된 세그먼트가 있다면 재계산)
        if changed:
            try:
                if 'OHLCV_GAP_OPEN_SEGMENTS' in globals():
                    open_cnt = sum(1 for g in self._gaps if g.get('state')=='open')
                    OHLCV_GAP_OPEN_SEGMENTS.labels(symbol=self.symbol, interval=self.interval).set(open_cnt)
            except Exception:
                pass

    async def register(self, ws: WebSocket):
        async with self._lock:
            self._clients.add(ws)

    async def unregister(self, ws: WebSocket):
        async with self._lock:
            if ws in self._clients:
                self._clients.remove(ws)

    async def _build_snapshot(self) -> dict:
        # 최근 snapshot_limit 개 DESC → ASC 변환 (실제 전달 캔들)
        rows = await fetch_kline_recent(self.symbol, self.interval, limit=self.snapshot_limit)
        rows_asc = list(reversed(rows))
        # config 기반 진행중 partial 캔들 포함 옵션
        try:
            from backend.common.config.base_config import load_config as _load_cfg
            _cfg = _load_cfg()
            if _cfg.ohlcv_ws_snapshot_include_open:
                # 가장 최신 진행중 캔들 1개 조회 (is_closed=false)
                from backend.common.db.connection import init_pool as _init_pool2
                pool_partial = await _init_pool2()
                if pool_partial is not None:
                    async with pool_partial.acquire() as conn:  # type: ignore
                        r_partial = await conn.fetchrow(
                            """
                            SELECT open_time, close_time, open, high, low, close, volume, trade_count AS trades, taker_buy_volume, taker_buy_quote_volume, is_closed
                            FROM ohlcv_candles
                            WHERE symbol=$1 AND interval=$2 AND is_closed=false
                            ORDER BY open_time DESC
                            LIMIT 1
                            """,
                            self.symbol.upper(), self.interval,
                        )
                        if r_partial:
                            pt = {k: r_partial[k] for k in r_partial.keys()}
                            if (not rows_asc) or rows_asc[-1]["open_time"] != pt["open_time"]:
                                rows_asc.append(pt)
        except Exception:
            pass
        # 기본 meta
        earliest = rows_asc[0]["open_time"] if rows_asc else None
        latest = rows_asc[-1]["open_time"] if rows_asc else None
        count = len(rows_asc)
        completeness_percent = None
        largest_gap_bars = 0
        largest_gap_span_ms = 0
        sample_for_gap = 2000

        # interval ms 계산 (간단 파서 재사용)
        def _interval_to_ms(it: str) -> int:
            try:
                unit = it[-1]; val = int(it[:-1])
                if unit == 'm': return val * 60_000
                if unit == 'h': return val * 60 * 60_000
                if unit == 'd': return val * 24 * 60 * 60_000
            except Exception:
                pass
            return 60_000
        interval_ms = _interval_to_ms(self.interval)

        # 추가 메타 계산 (DB 전체 스팬 기반) - 실패해도 snapshot 은 진행
        try:  # pragma: no cover (오류 시 조용히 무시)
            from backend.common.db.connection import init_pool as _init_pool
            pool = await _init_pool()
            if pool is not None:
                async with pool.acquire() as conn:  # type: ignore
                    row = await conn.fetchrow(
                        """
                        SELECT MIN(open_time) AS earliest, MAX(open_time) AS latest, COUNT(*) AS cnt
                        FROM ohlcv_candles
                        WHERE symbol=$1 AND interval=$2
                        """,
                        self.symbol.upper(), self.interval,
                    )
                    if row and row["cnt"] > 0:
                        earliest_db = int(row["earliest"])
                        latest_db = int(row["latest"])
                        total_cnt = int(row["cnt"])
                        if latest_db >= earliest_db:
                            expected = ((latest_db - earliest_db) // interval_ms) + 1
                            if expected > 0:
                                completeness_percent = total_cnt / expected
                        # gap 스캔 (최근 sample_for_gap)
                        if total_cnt > 1:
                            limit_scan = min(sample_for_gap, total_cnt)
                            rows_scan = await conn.fetch(
                                """
                                SELECT open_time FROM ohlcv_candles
                                WHERE symbol=$1 AND interval=$2
                                ORDER BY open_time DESC
                                LIMIT $3
                                """,
                                self.symbol.upper(), self.interval, limit_scan,
                            )
                            scan_list = [int(r["open_time"]) for r in rows_scan]
                            scan_list.reverse()
                            prev = None
                            for ot in scan_list:
                                if prev is not None:
                                    delta = ot - prev
                                    if delta > interval_ms:
                                        missing_bars = (delta // interval_ms) - 1
                                        if missing_bars > largest_gap_bars:
                                            largest_gap_bars = missing_bars
                                            largest_gap_span_ms = delta - interval_ms
                                prev = ot
                        # earliest/latest/count 는 전체 스팬 기준으로 덮어쓰기 (더 정확)
                        earliest = earliest_db
                        latest = latest_db
                        count = total_cnt
        except Exception:
            pass

        meta = {
            "earliest_open_time": earliest,
            "latest_open_time": latest,
            "count": count,
            "completeness_percent": completeness_percent,
            "largest_gap_bars": largest_gap_bars,
            "largest_gap_span_ms": largest_gap_span_ms,
        }
        return {"type": "snapshot", "symbol": self.symbol, "interval": self.interval, "candles": rows_asc, "meta": meta}

    async def send_snapshot(self, ws: WebSocket):
        payload = await self._build_snapshot()
        await ws.send_text(_json.dumps(payload, separators=(",", ":")))

    async def broadcast_append(self, closed_batch: list[dict]):
        if not closed_batch:
            return
        # closed_batch 은 open_time DESC 일 가능성 → ASC 보장
        if len(closed_batch) >= 2 and closed_batch[0]["open_time"] > closed_batch[-1]["open_time"]:
            closed_batch = list(reversed(closed_batch))
        # gap 탐지: 순방향 open_time 기반으로 새 캔들 처리 (append 는 항상 forward 라 가정)
        for c in closed_batch:
            try:
                await self._detect_gap_on_forward(int(c['open_time']))
            except Exception:
                # gap 탐지 실패해도 append 전송은 계속
                pass
        payload = {"type": "append", "symbol": self.symbol, "interval": self.interval, "count": len(closed_batch), "candles": closed_batch}
        data = _json.dumps(payload, separators=(",", ":"))
        await self._fanout(data)

    async def broadcast_repair(self, repaired: list[dict], meta_delta: Optional[dict] = None):
        if not repaired:
            return
        if len(repaired) >= 2 and repaired[0]["open_time"] > repaired[-1]["open_time"]:
            repaired = list(reversed(repaired))
        payload = {"type": "repair", "symbol": self.symbol, "interval": self.interval, "count": len(repaired), "candles": repaired}
        if meta_delta:
            payload["meta_delta"] = meta_delta
        data = _json.dumps(payload, separators=(",", ":"))
        await self._fanout(data)
        # delta 노출용 repair 히스토리 저장
        now_ms = int(time.time() * 1000)
        for c in repaired:
            rec = { 'open_time': int(c['open_time']), 'candle': c, 'ts_recorded': now_ms }
            self._repair_history.append(rec)
        # 오래된 것 또는 초과 trimming (정책: 오래된 먼저 제거)
        if len(self._repair_history) > self._repair_history_max:
            overflow = len(self._repair_history) - self._repair_history_max
            if overflow > 0:
                self._repair_history = self._repair_history[overflow:]
        # gap 복구 여부 평가 (repair 는 과거 시점 메꿀 수 있으므로 broadcast 이후 평가)
        try:
            await self._mark_repaired([int(c['open_time']) for c in repaired])
        except Exception:
            pass

    async def broadcast_partial_update(self, candle: dict):
        """진행중 partial 캔들 스냅샷 전송 (is_closed=false). candle: open_time 기반."""
        ot = int(candle.get("open_time"))
        self._partial[ot] = candle
        payload = {"type": "partial_update", "symbol": self.symbol, "interval": self.interval, "candle": candle}
        data = _json.dumps(payload, separators=(",", ":"))
        await self._fanout(data)

    async def broadcast_partial_close(self, open_time: int, final_candle: dict, latency_ms: Optional[int] = None):
        """partial 종료(확정 직전) 알림. 이후 append 로 최종 확정 캔들도 전송될 수 있음."""
        if open_time in self._partial:
            self._partial.pop(open_time, None)
        payload = {"type": "partial_close", "symbol": self.symbol, "interval": self.interval, "open_time": open_time, "final": final_candle}
        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        data = _json.dumps(payload, separators=(",", ":"))
        await self._fanout(data)

    async def broadcast_gap_detected(self, segment: dict):
        payload = {"type": "gap_detected", "symbol": self.symbol, "interval": self.interval, "segment": segment}
        data = _json.dumps(payload, separators=(",", ":"))
        await self._fanout(data)

    async def broadcast_gap_repaired(self, segment: dict):
        payload = {"type": "gap_repaired", "symbol": self.symbol, "interval": self.interval, "segment": segment}
        data = _json.dumps(payload, separators=(",", ":"))
        await self._fanout(data)

    async def _fanout(self, data: str):
        # 실패한 클라이언트는 조용히 제거
        async with self._lock:
            dead: list[WebSocket] = []
            for ws in list(self._clients):
                try:
                    await ws.send_text(data)
                except Exception:
                    dead.append(ws)
            for d in dead:
                self._clients.discard(d)

# 전역 매니저 (단일 심볼/인터벌 가정; 멀티 심볼 필요 시 dict 확장)
ohlcv_ws_manager: Optional[OhlcvWebSocketManager] = OhlcvWebSocketManager(cfg.symbol, cfg.kline_interval)

# ---------------------------------------------------------------------------
# Repair 브로드캐스트 메트릭 (append 와 대칭)
# ---------------------------------------------------------------------------
try:  # pragma: no cover
    from prometheus_client import Counter as _PromCounter, Histogram as _PromHistogram, Gauge as _PromGauge
    REPAIR_BROADCAST_ATTEMPTS = _PromCounter("ohlcv_ws_broadcast_repair_attempts_total", "WS repair broadcast attempts", ["symbol"])
    REPAIR_BROADCAST_SENT = _PromCounter("ohlcv_ws_broadcast_repair_sent_total", "WS repair messages successfully queued", ["symbol"])
    PARTIAL_UPDATE_SENT = _PromCounter("ohlcv_ws_partial_update_sent_total", "WS partial_update messages queued", ["symbol"])
    PARTIAL_CLOSE_SENT = _PromCounter("ohlcv_ws_partial_close_sent_total", "WS partial_close messages queued", ["symbol"])
    GAP_DETECTED_SENT = _PromCounter("ohlcv_ws_gap_detected_sent_total", "WS gap_detected messages queued", ["symbol"])
    GAP_REPAIRED_SENT = _PromCounter("ohlcv_ws_gap_repaired_sent_total", "WS gap_repaired messages queued", ["symbol"])
    PARTIAL_CLOSE_LATENCY = _PromHistogram(
        "ohlcv_partial_close_latency_ms",
        "Latency from theoretical close boundary to partial_close broadcast (ms)",
        buckets=(50,100,200,300,400,500,750,1000,1500,2000,3000,5000)
    )
    PARTIAL_CLOSE_LAST_LATENCY = _PromGauge(
        "ohlcv_partial_close_last_latency_ms",
        "Last observed partial_close latency (ms)"
    )
except Exception:  # pragma: no cover
    REPAIR_BROADCAST_ATTEMPTS = None  # type: ignore
    REPAIR_BROADCAST_SENT = None  # type: ignore
    PARTIAL_UPDATE_SENT = None  # type: ignore
    PARTIAL_CLOSE_SENT = None  # type: ignore
    GAP_DETECTED_SENT = None  # type: ignore
    GAP_REPAIRED_SENT = None  # type: ignore
    PARTIAL_CLOSE_LATENCY = None  # type: ignore
    PARTIAL_CLOSE_LAST_LATENCY = None  # type: ignore

_deleted_candles_buffer: list[dict] = []  # 최근 삭제된 캔들 버퍼 (mock repair 용)

@app.websocket("/ws/ohlcv")
async def ws_ohlcv(websocket: WebSocket, symbol: Optional[str] = None, interval: Optional[str] = None):
    # 심볼/인터벌 검증 (현재 단일 구성만 허용)
    sym = symbol or cfg.symbol
    itv = interval or cfg.kline_interval
    if sym.lower() != cfg.symbol.lower() or itv != cfg.kline_interval:
        # 단일 구성만 지원
        await websocket.close(code=4403)
        return
    await websocket.accept()
    if ohlcv_ws_manager is None:
        # 초기화 레이스 방지
        await websocket.close(code=1011)
        return
    await ohlcv_ws_manager.register(websocket)
    try:
        # 초기 스냅샷 전송
        await ohlcv_ws_manager.send_snapshot(websocket)
        # 수신 루프 (ping/pong 호환) – 클라이언트 메세지는 현재 무시
        while True:
            try:
                _ = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception:
                break
    finally:
        await ohlcv_ws_manager.unregister(websocket)

# ---------------------------------------------------------------------------
# Admin: 갭 생성을 위한 캔들 삭제 (earliest 기준 offset)
# ---------------------------------------------------------------------------
@app.post("/admin/ohlcv/delete_offset_range")
async def admin_ohlcv_delete_offset_range(start_offset: int, count: int):
    if start_offset < 0 or count < 1 or count > 50:
        raise HTTPException(status_code=400, detail="invalid_offset_or_count")
    from backend.common.db.connection import init_pool
    pool = await init_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="db_pool_unavailable")
    global _deleted_candles_buffer
    deleted: list[dict] = []
    async with pool.acquire() as conn:  # type: ignore
        rows = await conn.fetch(
            """
            SELECT open_time, close_time, open, high, low, close, volume, trade_count, taker_buy_volume, taker_buy_quote_volume
            FROM ohlcv_candles
            WHERE symbol=$1 AND interval=$2
            ORDER BY open_time ASC
            OFFSET $3 LIMIT $4
            """,
            cfg.symbol.upper(), cfg.kline_interval, start_offset, count
        )
        if not rows:
            return {"deleted": 0, "candles": []}
        open_times = [r["open_time"] for r in rows]
        deleted = [
            {
                "open_time": int(r["open_time"]),
                "close_time": int(r["close_time"]),
                "open": float(r["open"]) if r["open"] is not None else None,
                "high": float(r["high"]) if r["high"] is not None else None,
                "low": float(r["low"]) if r["low"] is not None else None,
                "close": float(r["close"]) if r["close"] is not None else None,
                "volume": float(r["volume"]) if r["volume"] is not None else None,
                "trade_count": int(r["trade_count"]) if r["trade_count"] is not None else None,
                "taker_buy_volume": float(r["taker_buy_volume"]) if r["taker_buy_volume"] is not None else None,
                "taker_buy_quote_volume": float(r["taker_buy_quote_volume"]) if r["taker_buy_quote_volume"] is not None else None,
                "is_closed": True,
            }
            for r in rows
        ]
        await conn.execute(
            """
            DELETE FROM ohlcv_candles
            WHERE symbol=$1 AND interval=$2 AND open_time = ANY($3::bigint[])
            """,
            cfg.symbol.upper(), cfg.kline_interval, open_times
        )
    _deleted_candles_buffer = deleted
    return {"deleted": len(deleted), "open_times": [c["open_time"] for c in deleted]}

# ---------------------------------------------------------------------------
# Admin: mock repair (삭제했던 캔들 재삽입 후 WS repair 브로드캐스트)
# ---------------------------------------------------------------------------
@app.post("/admin/ohlcv/mock_repair")
async def admin_ohlcv_mock_repair():
    if not _deleted_candles_buffer:
        raise HTTPException(status_code=400, detail="no_deleted_buffer")
    if ohlcv_ws_manager is None:
        raise HTTPException(status_code=503, detail="ws_manager_not_ready")
    from backend.common.db.connection import init_pool
    pool = await init_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="db_pool_unavailable")
    async with pool.acquire() as conn:  # type: ignore
        for c in _deleted_candles_buffer:
            await conn.execute(
                """
                INSERT INTO ohlcv_candles(symbol, interval, open_time, close_time, open, high, low, close, volume, trade_count, taker_buy_volume, taker_buy_quote_volume)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                ON CONFLICT (symbol, interval, open_time) DO UPDATE SET
                   close_time=EXCLUDED.close_time,
                   open=EXCLUDED.open,
                   high=EXCLUDED.high,
                   low=EXCLUDED.low,
                   close=EXCLUDED.close,
                   volume=EXCLUDED.volume,
                   trade_count=EXCLUDED.trade_count,
                   taker_buy_volume=EXCLUDED.taker_buy_volume,
                   taker_buy_quote_volume=EXCLUDED.taker_buy_quote_volume
                """,
                cfg.symbol.upper(), cfg.kline_interval,
                c["open_time"], c["close_time"], c["open"], c["high"], c["low"], c["close"], c["volume"], c.get("trade_count"), c.get("taker_buy_volume"), c.get("taker_buy_quote_volume")
            )
    repaired = sorted(_deleted_candles_buffer, key=lambda x: x["open_time"])
    if REPAIR_BROADCAST_ATTEMPTS:
        with contextlib.suppress(Exception):
            REPAIR_BROADCAST_ATTEMPTS.labels(cfg.symbol.lower()).inc()
    try:
        await ohlcv_ws_manager.broadcast_repair(repaired, meta_delta=None)
        if REPAIR_BROADCAST_SENT:
            with contextlib.suppress(Exception):
                REPAIR_BROADCAST_SENT.labels(cfg.symbol.lower()).inc()
    except Exception as e:  # noqa: BLE001
        return {"repair": "broadcast_failed", "error": str(e), "count": len(repaired)}
    return {"repair": "broadcast", "count": len(repaired), "open_times": [c["open_time"] for c in repaired]}

@app.post("/admin/ohlcv/mock_partial")
async def admin_ohlcv_mock_partial(offset_minutes: int = 0):
    """진행중(is_closed=false) partial 캔들을 인위적으로 생성/업서트.

    offset_minutes: 0 현재 분, 1 -> 한 분 앞(미래) 등; 음수면 과거 시각.
    생성 후 /ws/ohlcv snapshot (OHLCV_WS_SNAPSHOT_INCLUDE_OPEN=true) 에서 마지막 캔들이 is_closed=false 로 포함되는지 확인.
    """
    from backend.common.db.connection import init_pool as _init_pool
    from backend.common.config.base_config import load_config as _load_cfg
    import time as _t
    cfg_local = _load_cfg()
    interval = cfg_local.kline_interval
    symbol = cfg_local.symbol.upper()

    # interval 파싱 (m/h/d 최소 지원)
    def _interval_ms(iv: str) -> int:
        try:
            unit = iv[-1]; val = int(iv[:-1])
            if unit == 'm': return val * 60_000
            if unit == 'h': return val * 60 * 60_000
            if unit == 'd': return val * 24 * 60 * 60_000
        except Exception:
            pass
        return 60_000
    ims = _interval_ms(interval)
    now_ms = int(_t.time() * 1000)
    base_open = (now_ms // ims) * ims
    open_time = base_open + offset_minutes * 60_000
    if offset_minutes > 0:
        # 미래 분 생성 시 open_time 가 interval 크기 기준으로 맞춰지도록 재정렬
        open_time = ((open_time // ims) * ims)
    close_time = open_time + ims - 1
    pool = await _init_pool()
    if pool is None:
        raise HTTPException(status_code=500, detail="DB pool not ready")
    async with pool.acquire() as conn:  # type: ignore
        # 간단 업서트: close=false 유지, 가격/볼륨 임의 값
        await conn.execute(
            """
            INSERT INTO ohlcv_candles (symbol, interval, open_time, close_time, open, high, low, close, volume, trade_count, taker_buy_volume, taker_buy_quote_volume, is_closed)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,false)
            ON CONFLICT (symbol, interval, open_time) DO UPDATE SET
              close_time=EXCLUDED.close_time,
              high=GREATEST(ohlcv_candles.high, EXCLUDED.high),
              low=LEAST(ohlcv_candles.low, EXCLUDED.low),
              close=EXCLUDED.close,
              volume=ohlcv_candles.volume + EXCLUDED.volume,
              trade_count=ohlcv_candles.trade_count + EXCLUDED.trade_count,
              taker_buy_volume=ohlcv_candles.taker_buy_volume + EXCLUDED.taker_buy_volume,
              taker_buy_quote_volume=ohlcv_candles.taker_buy_quote_volume + EXCLUDED.taker_buy_quote_volume,
              is_closed=false
            """,
            symbol, interval, open_time, close_time,
            0.1, 0.11, 0.09, 0.1,  # open/high/low/close
            123.45, 7, 12.3, 45.6,
        )
    return {"partial_upserted": True, "symbol": symbol, "interval": interval, "open_time": open_time, "offset_minutes": offset_minutes}

@app.post("/admin/ohlcv/mock_partial_update")
async def admin_ohlcv_mock_partial_update(open_time: Optional[int] = None, price: float = 0.0, volume_delta: float = 0.0):
    """메모리상 partial_update 이벤트 강제 발생 (DB write 생략). open_time 미지정 시 최근 partial 존재 여부 탐색 없이 현재 분 사용."""
    from backend.common.config.base_config import load_config as _load_cfg
    cfg_local = _load_cfg()
    if ohlcv_ws_manager is None:
        raise HTTPException(status_code=503, detail="ws_manager_not_ready")
    # interval ms 계산
    def _interval_ms(iv: str) -> int:
        try:
            unit = iv[-1]; val = int(iv[:-1])
            if unit == 'm': return val * 60_000
            if unit == 'h': return val * 60 * 60_000
            if unit == 'd': return val * 24 * 60 * 60_000
        except Exception:
            pass
        return 60_000
    ims = _interval_ms(cfg_local.kline_interval)
    now_ms = int(time.time() * 1000)
    if open_time is None:
        open_time = (now_ms // ims) * ims
    progress = max(0.0, min(1.0, (now_ms - open_time) / ims))
    candle = {
        "open_time": open_time,
        "close_time": open_time + ims - 1,
        "open": price or 0.1,
        "high": price or 0.1,
        "low": price or 0.1,
        "close": price or 0.1,
        "volume": volume_delta,
        "is_closed": False,
        "progress": progress,
    }
    try:
        await ohlcv_ws_manager.broadcast_partial_update(candle)
        if PARTIAL_UPDATE_SENT:
            with contextlib.suppress(Exception):
                PARTIAL_UPDATE_SENT.labels(cfg_local.symbol.lower()).inc()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"partial_update_failed: {e}")
    return {"partial_update": True, "open_time": open_time, "progress": progress}

@app.post("/admin/ohlcv/mock_partial_close")
async def admin_ohlcv_mock_partial_close(open_time: Optional[int] = None):
    from backend.common.config.base_config import load_config as _load_cfg
    cfg_local = _load_cfg()
    if ohlcv_ws_manager is None:
        raise HTTPException(status_code=503, detail="ws_manager_not_ready")
    def _interval_ms(iv: str) -> int:
        try:
            unit = iv[-1]; val = int(iv[:-1])
            if unit == 'm': return val * 60_000
            if unit == 'h': return val * 60 * 60_000
            if unit == 'd': return val * 24 * 60 * 60_000
        except Exception:
            pass
        return 60_000
    ims = _interval_ms(cfg_local.kline_interval)
    now_ms = int(time.time() * 1000)
    if open_time is None:
        open_time = (now_ms // ims) * ims
    final_candle = {
        "open_time": open_time,
        "close_time": open_time + ims - 1,
        "open": 0.1,
        "high": 0.11,
        "low": 0.09,
        "close": 0.105,
        "volume": 999.0,
        "is_closed": True,
    }
    latency_ms = max(0, int(time.time() * 1000) - (open_time + ims)) if now_ms > (open_time + ims) else 0
    try:
        await ohlcv_ws_manager.broadcast_partial_close(open_time, final_candle, latency_ms=latency_ms)
        if PARTIAL_CLOSE_SENT:
            with contextlib.suppress(Exception):
                PARTIAL_CLOSE_SENT.labels(cfg_local.symbol.lower()).inc()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"partial_close_failed: {e}")
    return {"partial_close": True, "open_time": open_time, "latency_ms": latency_ms}

# ---------------------------------------------------------------------------
# Delta: since 이후 확정 캔들 + repairs (P1 repairs 비활성/빈 배열)
# ---------------------------------------------------------------------------
try:  # pragma: no cover
    from prometheus_client import Counter as _DeltaCounter
    DELTA_REQUESTS = _DeltaCounter("ohlcv_delta_requests_total", "Delta endpoint requests", ["truncated"])
except Exception:
    DELTA_REQUESTS = None  # type: ignore

@app.get("/api/ohlcv/delta")
async def api_ohlcv_delta(since: int, limit: int = 500, overlap: Optional[int] = None):
    cfg_local = load_config()
    limit = max(1, min(limit, cfg_local.ohlcv_delta_max_limit))
    # 최근 candles (DESC) 가져와서 필터
    rows = await _ohlcv_fetch_recent(cfg_local.symbol.upper(), cfg_local.kline_interval, limit=limit+5)  # 약간 여유로 가져옴
    if not rows:
        return {"base_from": None, "base_to": None, "candles": [], "repairs": [], "truncated": False}
    # rows 는 최신 DESC 이므로 ASC 로 변환 후 since 보다 큰 open_time 필터
    rows_asc = list(reversed(rows))
    base_from = rows_asc[0]["open_time"]
    base_to = rows_asc[-1]["open_time"]
    if since < 0:
        raise HTTPException(status_code=400, detail="invalid_since")
    # since 가 너무 과거: base_from 보다 작으면 snapshot 유도 에러
    if since < base_from:
        raise HTTPException(status_code=400, detail="since_out_of_range_use_snapshot")
    # Safety overlap: fetch one interval before 'since' to mitigate off-by-one / missed repair merges.
    # Caller can pass overlap ms explicitly; default = interval_ms derived from config if available.
    try:
        interval_ms = 60_000
        iv = cfg_local.kline_interval
        unit = iv[-1]; val = int(iv[:-1])
        if unit == 'm': interval_ms = val * 60_000
        elif unit == 'h': interval_ms = val * 60 * 60_000
        elif unit == 'd': interval_ms = val * 24 * 60 * 60_000
    except Exception:
        interval_ms = 60_000
    eff_overlap = overlap if isinstance(overlap, int) and overlap >= 0 else interval_ms
    boundary = since - eff_overlap
    new_candles = [c for c in rows_asc if c["open_time"] > boundary and c.get("is_closed", True)]
    # De-duplicate client-side later; we include <=since extra bar intentionally.
    truncated = False
    if len(new_candles) > limit:
        new_candles = new_candles[:limit]
        truncated = True
    if DELTA_REQUESTS:
        with contextlib.suppress(Exception):
            DELTA_REQUESTS.labels(str(truncated).lower()).inc()
    # repairs: in-memory repair history 중 since 이후 open_time 이거나, repair 기록 시각 기준 since 이후?
    # 계약: delta 는 since 이후 확정된 bar 와 더불어 "since 보다 큰 open_time 을 가진 repair 대상"을 제공.
    repairs: list[dict] = []
    try:
        if ohlcv_ws_manager is not None:  # type: ignore[name-defined]
            # history 는 ASC 가정
            for rec in ohlcv_ws_manager._repair_history:  # type: ignore[attr-defined]
                ot = rec.get('open_time')
                if isinstance(ot, int) and ot > since:
                    repairs.append({"open_time": ot, "candle": rec.get('candle')})
    except Exception:
        repairs = []
    return {
        "base_from": base_from,
        "base_to": base_to,
        "candles": new_candles,
        "repairs": repairs,
        "truncated": truncated,
    }

# ---------------------------------------------------------------------------
# Self-check: 전체 OHLCV 상태 및 브로드캐스트 경로 점검
# ---------------------------------------------------------------------------
@app.get("/api/ohlcv/self_check")
async def api_ohlcv_self_check(append_probe: bool = True, repair_probe: bool = False, history_limit: int = 5):
    from time import time as _now
    from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as _fetch_recent
    import contextlib
    # 전역 메트릭 심볼 참조 (존재하지 않을 경우 None)
    global BROADCAST_APPEND_ATTEMPTS, BROADCAST_APPEND_SENT  # type: ignore
    try:
        BROADCAST_APPEND_ATTEMPTS  # noqa: B018
    except NameError:  # pragma: no cover
        BROADCAST_APPEND_ATTEMPTS = None  # type: ignore
    try:
        BROADCAST_APPEND_SENT  # noqa: B018
    except NameError:  # pragma: no cover
        BROADCAST_APPEND_SENT = None  # type: ignore
    # 이미 등록된 후 전역 참조가 None 으로 남은 경우 Prometheus REGISTRY 에서 재획득
    from prometheus_client import REGISTRY as _REG  # type: ignore
    if BROADCAST_APPEND_ATTEMPTS is None:  # type: ignore
        with contextlib.suppress(Exception):
            BROADCAST_APPEND_ATTEMPTS = getattr(_REG, '_names_to_collectors', {}).get('ohlcv_ws_broadcast_append_attempts_total')  # type: ignore[attr-defined]
    if BROADCAST_APPEND_SENT is None:  # type: ignore
        with contextlib.suppress(Exception):
            BROADCAST_APPEND_SENT = getattr(_REG, '_names_to_collectors', {}).get('ohlcv_ws_broadcast_append_sent_total')  # type: ignore[attr-defined]
    # recent
    recent_rows = []
    try:
        recent_rows = await _fetch_recent(cfg.symbol.upper(), cfg.kline_interval, limit=100)
    except Exception as e:  # noqa: BLE001
        recent_err = str(e)
    else:
        recent_err = None
    # history (간단: recent_rows 뒤에서 history_limit ASC 재구성)
    history_part = []
    if recent_rows:
        asc = list(reversed(recent_rows))
        history_part = asc[-history_limit:]
    # meta 재사용: 내부 snapshot 빌더에서 meta 추출
    ws_snapshot = None
    snapshot_meta = None
    try:
        if ohlcv_ws_manager:
            snap = await ohlcv_ws_manager._build_snapshot()  # type: ignore[attr-defined]
            ws_snapshot = {
                "candle_count": len(snap.get("candles", [])),
                "latest_open_time": snap.get("meta", {}).get("latest_open_time"),
                "earliest_open_time": snap.get("meta", {}).get("earliest_open_time"),
            }
            snapshot_meta = snap.get("meta")
    except Exception as e:  # noqa: BLE001
        ws_snapshot = {"error": str(e)}
    # meta API 호환 형태
    meta_view = snapshot_meta or {}
    # Broadcast baseline metrics 이전 값 수집
    from prometheus_client import REGISTRY  # type: ignore
    def _metric_value(name: str) -> float:
        try:
            for m in REGISTRY.collect():  # type: ignore
                if m.name == name:
                    total = 0.0
                    for s in m.samples:  # type: ignore
                        try:
                            total += float(s.value)
                        except Exception:
                            pass
                    return total
        except Exception:
            return 0.0
        return 0.0
    attempts_before = _metric_value("ohlcv_ws_broadcast_append_attempts_total")
    sent_before = _metric_value("ohlcv_ws_broadcast_append_sent_total")
    rap_before = _metric_value("ohlcv_ws_broadcast_repair_attempts_total")
    rsp_before = _metric_value("ohlcv_ws_broadcast_repair_sent_total")
    # append probe (mock)
    append_probe_result = {"attempted": False}
    if append_probe and ohlcv_ws_manager:
        try:
            # recent 하나만 사용
            probe_candle = recent_rows[0] if recent_rows else None
            if probe_candle:
                out = [{
                    "open_time": probe_candle.get("open_time"),
                    "close_time": probe_candle.get("close_time"),
                    "open": probe_candle.get("open"),
                    "high": probe_candle.get("high"),
                    "low": probe_candle.get("low"),
                    "close": probe_candle.get("close"),
                    "volume": probe_candle.get("volume"),
                    "is_closed": True,
                }]
                if BROADCAST_APPEND_ATTEMPTS:
                    with contextlib.suppress(Exception):
                        BROADCAST_APPEND_ATTEMPTS.labels(cfg.symbol.lower()).inc()  # type: ignore
                await ohlcv_ws_manager.broadcast_append(out)
                if BROADCAST_APPEND_SENT:
                    with contextlib.suppress(Exception):
                        BROADCAST_APPEND_SENT.labels(cfg.symbol.lower()).inc()  # type: ignore
                append_probe_result = {"attempted": True, "count": 1, "ok": True}
            else:
                append_probe_result = {"attempted": True, "skipped": True, "reason": "no_recent"}
        except Exception as e:  # noqa: BLE001
            append_probe_result = {"attempted": True, "error": str(e)}
    # repair probe (optional) - 삭제 버퍼 기반
    repair_probe_result = {"attempted": False}
    if repair_probe and _deleted_candles_buffer and ohlcv_ws_manager:
        try:
            if REPAIR_BROADCAST_ATTEMPTS:
                with contextlib.suppress(Exception):
                    REPAIR_BROADCAST_ATTEMPTS.labels(cfg.symbol.lower()).inc()
            repaired = sorted(_deleted_candles_buffer, key=lambda x: x["open_time"])
            await ohlcv_ws_manager.broadcast_repair(repaired, meta_delta=None)
            if REPAIR_BROADCAST_SENT:
                with contextlib.suppress(Exception):
                    REPAIR_BROADCAST_SENT.labels(cfg.symbol.lower()).inc()
            repair_probe_result = {"attempted": True, "count": len(repaired), "ok": True}
        except Exception as e:  # noqa: BLE001
            repair_probe_result = {"attempted": True, "error": str(e)}
    # After metrics
    attempts_after = _metric_value("ohlcv_ws_broadcast_append_attempts_total")
    sent_after = _metric_value("ohlcv_ws_broadcast_append_sent_total")
    rap_after = _metric_value("ohlcv_ws_broadcast_repair_attempts_total")
    rsp_after = _metric_value("ohlcv_ws_broadcast_repair_sent_total")
    report = {
        "timestamp": int(_now()),
        "recent_count": len(recent_rows),
        "recent_tail_open_time": recent_rows[0]["open_time"] if recent_rows else None,
        "history_preview": history_part,
        "meta": meta_view,
        "ws_snapshot": ws_snapshot,
        "append_probe": append_probe_result,
        "repair_probe": repair_probe_result,
        "metrics_delta": {
            "append_attempts_delta": attempts_after - attempts_before,
            "append_sent_delta": sent_after - sent_before,
            "repair_attempts_delta": rap_after - rap_before,
            "repair_sent_delta": rsp_after - rsp_before,
        },
        "config_flags": {
            "ohlcv_include_open_default": getattr(cfg, 'ohlcv_include_open_default', None),
            "ohlcv_ws_snapshot_include_open": getattr(cfg, 'ohlcv_ws_snapshot_include_open', None),
        },
    }
    # append probe 추가 설명 필드 (delta=0 진단)
    if append_probe_result.get("ok"):
        ad = report["metrics_delta"]["append_attempts_delta"]
        report["append_probe_incremented"] = (ad > 0)
        if ad == 0:
            report["append_probe_note"] = "prometheus counter not incremented (lazy registration or lookup failure)"
    else:
        report["append_probe_incremented"] = False
    # 단순 상태 판정
    warnings: list[str] = []
    if report["recent_count"] == 0:
        warnings.append("no_recent_rows")
    if isinstance(ws_snapshot, dict) and ws_snapshot.get("error"):
        warnings.append("snapshot_error")
    if append_probe and not append_probe_result.get("ok"):
        warnings.append("append_probe_failed")
    if repair_probe and not repair_probe_result.get("ok"):
        warnings.append("repair_probe_failed")
    report["warnings"] = warnings
    report["status"] = "ok" if not warnings else ("degraded" if warnings else "ok")
    return report



@app.get("/api/ohlcv/gaps/status")
async def ohlcv_gap_backfill_status(_auth: bool = Depends(require_api_key)):
    consumer: Optional[KlineConsumer] = getattr(app.state, "kline_consumer", None)
    backfill = getattr(app.state, "gap_backfill", None)
    if not consumer:
        return {"status": "no_consumer"}
    gaps = consumer.get_gaps()
    segments: list[dict] = []
    for g in gaps:
        if isinstance(g, (list, tuple)) and len(g) >= 2:
            start, end = int(g[0]), int(g[1])
            missing = 0
            if end > start:
                # 분 단위 간격 추정 (기본 60s) - 향후 interval ms 파서로 개선 가능
                missing = max(0, ((end - start) // 60_000) - 1)
            segments.append({"from_ts": start, "to_ts": end, "missing": missing, "state": "open"})
        elif isinstance(g, dict):
            seg = dict(g)
            seg.setdefault("state", "open")
            segments.append(seg)
    return {
        "status": "ok",
        "open_segments": len(segments),
        "segments": segments,
        "backfill_running": bool(backfill and backfill._running),
        "interval": consumer.interval,
    }

@app.post("/api/ohlcv/gaps/fill")
async def ohlcv_gaps_fill(
    symbol: Optional[str] = None,
    interval: Optional[str] = None,
    max_segments: int = 200,
    _auth: bool = Depends(require_api_key),
):
    """Scan DB from earliest to now for missing closed candles and upsert them via Binance REST.

    Params:
      symbol, interval: override defaults
      max_segments: safety cap for number of gap segments to process in one call

    Returns a summary of segments found and recovered bars.
    """
    sym = (symbol or cfg.symbol).upper()
    itv = (interval or cfg.kline_interval)

    def _interval_to_ms(it: str) -> int:
        it = (it or "1m").strip().lower()
        if it.endswith("ms"):  # raw ms
            try:
                return max(1, int(it[:-2]))
            except Exception:
                return 60_000
        if it.endswith("s") and not it.endswith("ms"):
            try:
                return max(1, int(it[:-1]) * 1000)
            except Exception:
                return 60_000
        if it.endswith("m"):
            try:
                return max(1, int(it[:-1]) * 60_000)
            except Exception:
                return 60_000
        if it.endswith("h"):
            try:
                return max(1, int(it[:-1]) * 60 * 60_000)
            except Exception:
                return 60_000
        if it.endswith("d"):
            try:
                return max(1, int(it[:-1]) * 24 * 60 * 60_000)
            except Exception:
                return 60_000
        return 60_000

    interval_ms = _interval_to_ms(itv)
    # 1) Find gaps by scanning open_time ASC in chunks
    from backend.common.db.connection import init_pool as _init_pool
    pool = await _init_pool()
    if pool is None:
        return {"status": "db_unavailable"}

    gap_segments: list[dict] = []
    earliest = None
    latest = None
    # quick min/max
    async with pool.acquire() as conn:  # type: ignore
        row = await conn.fetchrow(
            """
            SELECT MIN(open_time) AS earliest, MAX(open_time) AS latest, COUNT(*) AS cnt
            FROM ohlcv_candles WHERE symbol=$1 AND interval=$2
            """,
            sym,
            itv,
        )
        if not row or not row["cnt"]:
            return {"status": "empty", "segments": [], "recovered_total": 0}
        earliest = int(row["earliest"]) if row["earliest"] is not None else None
        latest = int(row["latest"]) if row["latest"] is not None else None

    # walk ascending in chunks and detect deltas > interval
    prev_ot: Optional[int] = None
    chunk = 50_000
    cursor = earliest
    processed = 0
    while True:
        async with pool.acquire() as conn:  # type: ignore
            rows = await conn.fetch(
                """
                SELECT open_time FROM ohlcv_candles
                WHERE symbol=$1 AND interval=$2 AND open_time >= $3
                ORDER BY open_time ASC
                LIMIT $4
                """,
                sym,
                itv,
                cursor,
                chunk,
            )
        if not rows:
            break
        for r in rows:
            ot = int(r["open_time"])  # asc
            if prev_ot is not None:
                delta = ot - prev_ot
                if delta > interval_ms:
                    missing = (delta // interval_ms) - 1
                    from_open = prev_ot + interval_ms
                    to_open = ot - interval_ms
                    gap_segments.append({
                        "from_open_time": from_open,
                        "to_open_time": to_open,
                        "missing_bars": int(missing),
                    })
                    if len(gap_segments) >= max_segments:
                        break
            prev_ot = ot
        processed += len(rows)
        if len(rows) < chunk or len(gap_segments) >= max_segments:
            break
        cursor = int(rows[-1]["open_time"]) + 1

    # tail gap up to now (optional)
    try:
        now_ms = int(time.time() * 1000)
        if latest is not None and now_ms - latest > interval_ms:
            # fill only closed bars up to last fully closed slot
            last_closed = now_ms - (now_ms % interval_ms) - interval_ms
            if last_closed > latest:
                missing_tail = max(0, ((last_closed - latest) // interval_ms))
                if missing_tail:
                    gap_segments.append({
                        "from_open_time": latest + interval_ms,
                        "to_open_time": last_closed,
                        "missing_bars": int(missing_tail),
                    })
    except Exception:
        pass

    # 2) For each gap segment, fetch klines from Binance and upsert
    recovered_total = 0
    errors: list[str] = []
    details: list[dict] = []
    if not gap_segments:
        return {"status": "ok", "segments": [], "recovered_total": 0}

    try:
        import aiohttp  # lazy import
        from backend.apps.ingestion.repository.ohlcv_repository import bulk_upsert as _bulk_upsert
    except Exception as e:
        return {"status": "error", "error": f"missing deps: {e}"}

    base_url = "https://fapi.binance.com/fapi/v1/klines"
    async with aiohttp.ClientSession() as session:
        for seg in gap_segments:
            if recovered_total >= 1_000_000:  # hard safety
                break
            start_ot = int(seg["from_open_time"])
            end_ot = int(seg["to_open_time"]) + interval_ms  # inclusive end boundary for REST
            seg_recovered = 0
            start_cursor = start_ot
            try:
                while start_cursor < end_ot:
                    params = {
                        "symbol": sym,
                        "interval": itv,
                        "startTime": start_cursor,
                        "endTime": end_ot,
                        "limit": 1500,
                    }
                    req_ts = time.perf_counter()
                    async with session.get(base_url, params=params, timeout=15) as resp:
                        if resp.status != 200:
                            errors.append(f"http_{resp.status}:{start_cursor}")
                            break
                        data = await resp.json()
                        if not isinstance(data, list) or not data:
                            break
                        payload = []
                        for item in data:
                            ot = int(item[0])
                            if ot < start_ot or ot > end_ot:
                                continue
                            k = {
                                "t": ot,
                                "T": int(item[6]),
                                    "scale_in_cooldown_sec": getattr(app.state, 'live_scale_in_cooldown_sec', 0),
                                    # fee snapshot
                                    "fee_mode": cfg.trading_fee_mode,
                                    "fee_taker": cfg.trading_fee_taker,
                                    "fee_maker": cfg.trading_fee_maker,
                                "i": itv,
                                "o": item[1],
                                "h": item[2],
                                "l": item[3],
                                "c": item[4],
                                "v": item[5],
                                "n": int(item[8]),
                                "V": item[9],
                                "Q": item[10],
                                "x": True,
                            }
                            payload.append(k)
                        if payload:
                            await _bulk_upsert(payload, ingestion_source="gap_recover_api")
                            seg_recovered += len(payload)
                            recovered_total += len(payload)
                            # advance cursor to after last returned open_time
                            last_ot = int(data[-1][0])
                            # prevent infinite loop on odd payload
                            if last_ot <= start_cursor:
                                start_cursor = last_ot + interval_ms
                            else:
                                start_cursor = last_ot + interval_ms
                        else:
                            break
                details.append({
                    "from_open_time": start_ot,
                    "to_open_time": int(seg["to_open_time"]),
                    "missing_bars": int(seg["missing_bars"]),
                    "recovered_bars": seg_recovered,
                })
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
                details.append({
                    "from_open_time": start_ot,
                    "to_open_time": int(seg["to_open_time"]),
                    "missing_bars": int(seg["missing_bars"]),
                    "recovered_bars": seg_recovered,
                    "error": str(e),
                })

    # Opportunistic: trigger meta refresh via existing helper if backfill service exists
    try:
        consumer: Optional[KlineConsumer] = getattr(app.state, "kline_consumer", None)
        if consumer and getattr(consumer, "_backfill_service", None):
            svc = getattr(consumer, "_backfill_service")
            # Best-effort refresh
            await svc._refresh_canonical_meta(force=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    # Build remaining segments (simple: details with partial recovery)
    remaining_segments: list[dict] = []
    try:
        for d in details:
            miss = int(d.get("missing_bars", 0))
            rec = int(d.get("recovered_bars", 0))
            rem = max(0, miss - rec)
            if rem > 0:
                remaining_segments.append({
                    "from_open_time": d.get("from_open_time"),
                    "to_open_time": d.get("to_open_time"),
                    "missing_bars": rem,
                })
    except Exception:
        remaining_segments = []

    return {
        "status": "ok",
        "symbol": sym,
        "interval": itv,
        "segments_found": len(gap_segments),
        "recovered_total": recovered_total,
        "details": details[:max_segments],
        "remaining_segments": remaining_segments,
        "errors": errors,
    }

# 1-year historical backfill trigger & status ---------------------------------
@app.post("/api/ohlcv/backfill/year", dependencies=[Depends(require_api_key)])
async def api_year_backfill_start(symbol: Optional[str] = None, interval: Optional[str] = None, overwrite: bool = True, purge: bool = False):
    res = await start_year_backfill(symbol=symbol, interval=interval, overwrite=overwrite, purge=purge)
    return res

@app.get("/api/ohlcv/backfill/year/status", dependencies=[Depends(require_api_key)])
async def api_year_backfill_status(symbol: Optional[str] = None, interval: Optional[str] = None):
    state = await get_year_backfill_status(symbol=symbol, interval=interval)
    if not state:
        return {"status": "idle"}
    return state

@app.post("/api/ohlcv/backfill/year/start", dependencies=[Depends(require_api_key)])
async def api_year_backfill_start_public(symbol: Optional[str] = None, interval: Optional[str] = None, overwrite: bool = True, purge: bool = False):
    """Public start endpoint for 1y backfill to wire Job Center actions."""
    return await start_year_backfill(symbol=symbol, interval=interval, overwrite=overwrite, purge=purge)

@app.post("/api/ohlcv/backfill/year/cancel", dependencies=[Depends(require_api_key)])
async def api_year_backfill_cancel(symbol: Optional[str] = None, interval: Optional[str] = None):
    """Cancel the in-progress 1y backfill if any."""
    return await cancel_year_backfill(symbol=symbol, interval=interval)

# ---------------------------------------------------------------------------
# SSE: Feature Backfill Runs stream (lightweight)
# ---------------------------------------------------------------------------
@app.get("/stream/runs", dependencies=[Depends(require_api_key)])
async def stream_runs(request: StarletteRequest, symbol: Optional[str] = None, interval: Optional[str] = None, status: Optional[str] = None, limit: int = 50):
    """Server-Sent Events stream of recent feature_backfill_runs summary.

    Emits a snapshot (top 50) every ~5s with a heartbeat ping.
    Intended for UI live updates; not a durable event log.
    """
    sym = symbol or cfg.symbol
    itv = interval or cfg.kline_interval
    st = (status or '').strip().lower() or None
    lim = max(1, min(200, int(limit)))

    async def event_gen():
        pool = await init_pool()
        last_hash = None
        try:
            while True:
                # stop if client disconnected
                try:
                    if await request.is_disconnected():
                        break
                except Exception:
                    pass
                async with pool.acquire() as conn:
                    if st:
                        rows = await conn.fetch(
                            """
                            SELECT id, symbol, interval, status, inserted, requested_target, used_window,
                                   started_at, finished_at, error
                            FROM feature_backfill_runs
                            WHERE symbol=$1 AND interval=$2 AND status=$3
                            ORDER BY started_at DESC
                            LIMIT $4
                            """,
                            sym, itv, st, lim,
                        )
                    else:
                        rows = await conn.fetch(
                            """
                            SELECT id, symbol, interval, status, inserted, requested_target, used_window,
                                   started_at, finished_at, error
                            FROM feature_backfill_runs
                            WHERE symbol=$1 AND interval=$2
                            ORDER BY started_at DESC
                            LIMIT $3
                            """,
                            sym, itv, lim,
                        )
                    items = [dict(r) for r in rows]
                # emit when changed (best-effort)
                import hashlib, json
                payload = {"type": "snapshot", "items": items, "ts": time.time()}
                blob = json.dumps(payload, default=str)
                h = hashlib.md5(blob.encode()).hexdigest()
                if h != last_hash:
                    last_hash = h
                    yield f"id: {int(time.time()*1000)}\n"
                    yield "event: message\n"
                    yield f"data: {blob}\n\n"
                # heartbeat
                yield "event: ping\n"
                yield f"data: {int(time.time())}\n\n"
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(event_gen(), headers=headers)

# ---------------------------------------------------------------------------
# SSE: Risk State stream (lightweight)
# ---------------------------------------------------------------------------
@app.get("/stream/risk", dependencies=[Depends(require_api_key)])
async def stream_risk(request: StarletteRequest):
    """Server-Sent Events stream of risk state (session + positions) every ~3s."""

    async def event_gen():
        try:
            while True:
                try:
                    if await request.is_disconnected():
                        break
                except Exception:
                    pass
                try:
                    data = await risk_state()  # reuse handler directly
                except Exception as e:  # noqa: BLE001
                    data = {"status": "error", "error": str(e)}
                import json
                yield f"id: {int(time.time()*1000)}\n"
                yield "event: message\n"
                yield f"data: {json.dumps(data, default=str)}\n\n"
                await asyncio.sleep(3)
        except asyncio.CancelledError:
            pass

    headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(event_gen(), headers=headers)

# ---------------------------------------------------------------------------
# Sentiment: ingestion (MVP)
# ---------------------------------------------------------------------------
from typing import Any
from pydantic import BaseModel
from datetime import datetime

from backend.apps.sentiment.repository import insert_tick as sentiment_insert_tick
from backend.apps.sentiment.repository import fetch_range as sentiment_fetch_range
from backend.apps.sentiment.service import aggregate_with_windows
from prometheus_client import Counter, Histogram, Gauge

# -------------------------- Sentiment Metrics -------------------------------
SENTIMENT_INGEST_TOTAL = Counter("sentiment_ingest_total", "Total number of sentiment ticks ingested")
SENTIMENT_INGEST_ERRORS_TOTAL = Counter("sentiment_ingest_errors_total", "Total number of sentiment ingest errors")
SENTIMENT_INGEST_LATENCY_MS = Histogram(
    "sentiment_ingest_latency_ms",
    "Latency of sentiment tick ingestion (ms)",
    buckets=[50, 100, 200, 500, 1000, 2000, 5000, 10000],
)
SENTIMENT_HISTORY_QUERIES_TOTAL = Counter("sentiment_history_queries_total", "Total number of sentiment history queries")
SENTIMENT_HISTORY_LATENCY_MS = Histogram(
    "sentiment_history_query_latency_ms",
    "Latency of sentiment history queries (ms)",
    buckets=[10, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
)
SENTIMENT_SSE_CLIENTS = Gauge("sentiment_sse_clients", "Number of connected sentiment SSE clients")


class SentimentTickIn(BaseModel):
    symbol: str
    ts: Union[int, str]  # epoch ms or ISO8601 (e.g., 2025-10-09T10:21:00Z)
    provider: Optional[str] = None
    count: Optional[int] = None
    score_raw: Optional[float] = None
    score_norm: Optional[float] = None  # expected in [-1, 1]
    meta: Optional[dict[str, Any]] = None


def _parse_ts_ms(ts: Union[int, str]) -> int:
    if isinstance(ts, int):
        return ts
    s = str(ts).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # fromisoformat supports "+00:00" timezone format
    dt = datetime.fromisoformat(s)
    return int(dt.timestamp() * 1000)


@app.post("/api/sentiment/tick", dependencies=[Depends(require_api_key)])
async def api_sentiment_tick(body: SentimentTickIn):
    """Ingest a single sentiment tick.
    - ts: epoch ms or ISO8601; must not be in the future.
    - score_norm clamped to [-1, 1].
    - Upsert on (symbol, ts, provider) best-effort.
    """
    try:
        ts_ms = _parse_ts_ms(body.ts)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid ts: {e}")

    now_ms = int(time.time() * 1000)
    if ts_ms > now_ms + 5000:  # allow small clock skew
        raise HTTPException(status_code=400, detail="ts in the future")

    score_norm = body.score_norm
    if score_norm is not None:
        # clamp to [-1, 1]
        if score_norm > 1:
            score_norm = 1.0
        elif score_norm < -1:
            score_norm = -1.0

    meta = body.meta or {}

    _t0 = time.perf_counter()
    try:
        row_id = await sentiment_insert_tick(
            symbol=body.symbol,
            ts_ms=ts_ms,
            provider=body.provider,
            count=body.count,
            score_raw=body.score_raw,
            score_norm=score_norm,
            meta=meta,
            upsert=True,
        )
        SENTIMENT_INGEST_TOTAL.inc()
    except Exception as e:  # noqa: BLE001
        SENTIMENT_INGEST_ERRORS_TOTAL.inc()
        raise e
    finally:
        _elapsed_ms = (time.perf_counter() - _t0) * 1000.0
        try:
            SENTIMENT_INGEST_LATENCY_MS.observe(_elapsed_ms)
        except Exception:
            pass
    return {
        "id": row_id,
        "symbol": body.symbol,
        "ts": ts_ms,
        "provider": body.provider,
        "count": body.count,
        "score_raw": body.score_raw,
        "score_norm": score_norm,
        "meta": meta,
        "status": "ok",
    }


@app.get("/api/sentiment/latest", dependencies=[Depends(require_api_key)])
async def api_sentiment_latest(symbol: Optional[str] = None, windows: Optional[str] = None, include_meta: bool = False):
    """Return the latest sentiment snapshot for a symbol with EMA windows.
    - windows: comma list of integers (bucket counts) interpreted in minutes (e.g., "5,15,60").
    """
    sym = symbol or cfg.symbol
    now_ms = int(time.time() * 1000)
    # look back last 6 hours to compute 60m EMA with buffer
    lookback_ms = 6 * 60 * 60 * 1000
    start_ms = now_ms - lookback_ms
    rows = await sentiment_fetch_range(sym, start_ms, now_ms, limit=cfg.ohlcv_delta_max_limit * 20)
    if not rows:
        return {"symbol": sym, "status": "nodata"}

    # parse scores (prefer score_norm; fallback to score_raw normalized heuristically if needed)
    points: list[tuple[int, float]] = []
    for r in rows:
        ts_ms = int(r["ts"])  # migration uses epoch ms
        v = r.get("score_norm")
        if v is None:
            v = r.get("score_raw")
            if v is None:
                continue
        points.append((ts_ms, float(v)))
    if not points:
        return {"symbol": sym, "status": "nodata"}

    # step from env: 1m default
    _v = os.getenv("SENTIMENT_STEP_DEFAULT", "1")
    if isinstance(_v, str):
        _v = _v.rstrip("m")
        if '#' in _v:
            _v = _v.split('#', 1)[0].strip()
    step_min = int(float(_v))
    step_ms = step_min * 60 * 1000

    # EMA windows in minutes
    if windows:
        ema_windows = [int(w.strip()) for w in windows.split(",") if w.strip()]
    else:
        ema_windows = [int(x.strip().rstrip("m")) for x in os.getenv("SENTIMENT_EMA_WINDOWS", "5m,15m,60m").split(",")]

    _v = os.getenv("SENTIMENT_POS_THRESHOLD", "0.0")
    if isinstance(_v, str) and '#' in _v:
        _v = _v.split('#', 1)[0].strip()
    pos_threshold = float(_v)
    agg = aggregate_with_windows(sorted(points), step_ms=step_ms, ema_windows=ema_windows, pos_threshold=pos_threshold)
    # latest by ts among score
    last_ts = agg["score"][-1][0]
    out = {
        "symbol": sym,
        "t": last_ts,
        "score": agg["score"][-1][1],
        "count": agg["count"][-1][1],
        "pos_ratio": agg["pos_ratio"][-1][1],
        "d1": agg["d1"][-1][1],
        "d5": agg["d5"][-1][1],
        "vol_30": agg["vol_30"][-1][1],
        "ema": {f"{n}m": agg[f"ema_{n}"][-1][1] for n in ema_windows if agg.get(f"ema_{n}")},
        "status": "ok",
    }
    if include_meta:
        out["windows"] = ema_windows
        out["step_ms"] = step_ms
        out["points_used"] = len(points)
    return out


@app.get("/api/sentiment/history", dependencies=[Depends(require_api_key)])
async def api_sentiment_history(
    symbol: Optional[str] = None,
    start: Optional[Union[int, str]] = None,
    end: Optional[Union[int, str]] = None,
    step: Optional[str] = None,
    windows: Optional[str] = None,
    fields: Optional[str] = None,
    agg: str = "mean",  # reserved for future (mean|last)
):
    sym = symbol or cfg.symbol
    now_ms = int(time.time() * 1000)

    def _parse_ts(val: Optional[Union[int, str]], default: int) -> int:
        if val is None:
            return default
        if isinstance(val, int):
            return val
        s = str(val).strip()
        if s.lstrip("-").isdigit():
            return int(s)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        from datetime import datetime
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp() * 1000)

    end_ms = _parse_ts(end, now_ms)
    # default lookback 24h
    start_ms = _parse_ts(start, end_ms - 24 * 60 * 60 * 1000)
    if start_ms >= end_ms:
        raise HTTPException(status_code=400, detail="invalid time range")

    _t0 = time.perf_counter()
    _v = os.getenv("SENTIMENT_HISTORY_MAX_POINTS", "10000")
    if isinstance(_v, str) and '#' in _v:
        _v = _v.split('#', 1)[0].strip()
    rows = await sentiment_fetch_range(sym, start_ms, end_ms, limit=int(float(_v)))
    try:
        SENTIMENT_HISTORY_QUERIES_TOTAL.inc()
    finally:
        _elapsed_ms = (time.perf_counter() - _t0) * 1000.0
        try:
            SENTIMENT_HISTORY_LATENCY_MS.observe(_elapsed_ms)
        except Exception:
            pass
    if not rows:
        return {"symbol": sym, "items": [], "status": "nodata"}

    points: list[tuple[int, float]] = []
    for r in rows:
        ts_ms = int(r["ts"])  # epoch ms
        v = r.get("score_norm")
        if v is None:
            v = r.get("score_raw")
            if v is None:
                continue
        points.append((ts_ms, float(v)))
    if not points:
        return {"symbol": sym, "items": [], "status": "nodata"}

    # step
    if step:
        if step.endswith("m"):
            step_min = int(step[:-1])
        else:
            step_min = int(step)  # minutes
    else:
        _v = os.getenv("SENTIMENT_STEP_DEFAULT", "1")
        if isinstance(_v, str):
            _v = _v.rstrip("m")
            if '#' in _v:
                _v = _v.split('#', 1)[0].strip()
        step_min = int(float(_v))
    step_ms = step_min * 60 * 1000

    # windows
    if windows:
        ema_windows = [int(w.strip()) for w in windows.split(",") if w.strip()]
    else:
        ema_windows = [int(x.strip().rstrip("m")) for x in os.getenv("SENTIMENT_EMA_WINDOWS", "5m,15m,60m").split(",")]

    _v = os.getenv("SENTIMENT_POS_THRESHOLD", "0.0")
    if isinstance(_v, str) and '#' in _v:
        _v = _v.split('#', 1)[0].strip()
    pos_threshold = float(_v)
    agg_res = aggregate_with_windows(sorted(points), step_ms=step_ms, ema_windows=ema_windows, pos_threshold=pos_threshold)

    # select fields
    default_fields = ["t", "score", "count", "pos_ratio"] + [f"ema_{n}" for n in ema_windows]
    if fields:
        wanted = [f.strip() for f in fields.split(",") if f.strip()]
    else:
        wanted = default_fields

    # build time-aligned output using score timestamps as base
    tses = [t for t, _ in agg_res["score"]]
    series: dict[str, dict[int, float]] = {}
    for k, arr in agg_res.items():
        series[k] = {t: v for t, v in arr}

    items = []
    for t in tses:
        row = {"t": t}
        for key in wanted:
            if key == "t":
                continue
            if key in ("score", "count", "pos_ratio", "d1", "d5", "vol_30"):
                row[key] = series.get(key, {}).get(t)
            elif key.startswith("ema_"):
                row[key] = series.get(key, {}).get(t)
        items.append(row)

    return {
        "symbol": sym,
        "step_ms": step_ms,
        "windows": ema_windows,
        "items": items,
        "status": "ok",
    }


# ---------------------------------------------------------------------------
# SSE: Sentiment stream (optional)
# ---------------------------------------------------------------------------
@app.get("/stream/sentiment", dependencies=[Depends(require_api_key)])
async def stream_sentiment(request: StarletteRequest, symbol: Optional[str] = None, windows: Optional[str] = None, step: Optional[str] = None):
    """SSE stream of latest sentiment snapshot for a symbol every ~3s."""
    sym = symbol or cfg.symbol

    async def event_gen():
        SENTIMENT_SSE_CLIENTS.inc()
        try:
            last_hash = None
            while True:
                try:
                    if await request.is_disconnected():
                        break
                except Exception:
                    pass
                try:
                    data = await api_sentiment_latest(symbol=sym, windows=windows, include_meta=False)
                except Exception as e:  # noqa: BLE001
                    data = {"status": "error", "error": str(e), "symbol": sym}
                import json, hashlib, time as _t
                blob = json.dumps(data, default=str)
                h = hashlib.md5(blob.encode()).hexdigest()
                if h != last_hash:
                    last_hash = h
                    yield f"id: {int(_t.time()*1000)}\n"
                    yield "event: message\n"
                    yield f"data: {blob}\n\n"
                yield "event: ping\n"
                yield f"data: {int(_t.time())}\n\n"
                await asyncio.sleep(3)
        except asyncio.CancelledError:
            pass
        finally:
            try:
                SENTIMENT_SSE_CLIENTS.dec()
            except Exception:
                pass

    headers = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "Connection": "keep-alive"}
    return StreamingResponse(event_gen(), headers=headers)
