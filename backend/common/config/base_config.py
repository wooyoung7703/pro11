from __future__ import annotations

import logging
import os
from typing import Optional

_log = logging.getLogger("config")

def _repo_root() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    # backend/common/config -> repo root three levels up
    return os.path.abspath(os.path.join(here, '../../..'))

def _sanitize_inline_commented_env(keys: list[str]) -> None:
    """Strip inline comments (text after '#') for specific env keys known to be numeric."""
    for k in keys:
        try:
            v = os.getenv(k)
            if v and "#" in v:
                os.environ[k] = v.split("#", 1)[0].strip()
        except Exception:  # pragma: no cover - best-effort
            continue

# Common numeric envs that admins may annotate with inline comments
SANITIZE_KEYS = [
    # Core scheduler/loop timings
    "FEATURE_SCHED_INTERVAL",
    "INFERENCE_AUTO_LOOP_INTERVAL",
    "INFERENCE_LOG_FLUSH_INTERVAL",
    "INFERENCE_LOG_FLUSH_MIN_INTERVAL",
    "INFERENCE_LOG_FLUSH_HIGH_WATERMARK",
    "INFERENCE_LOG_FLUSH_LOW_WATERMARK",
    "RISK_METRICS_INTERVAL",
    "PRODUCTION_METRICS_INTERVAL",
    "AUTO_LABELER_INTERVAL",
    "CALIBRATION_MONITOR_INTERVAL",
    # Bottom params
    "BOTTOM_LOOKAHEAD",
    "BOTTOM_DRAWDOWN",
    "BOTTOM_REBOUND",
    # Autopilot / ML
    "LOW_BUY_AUTO_LOOP_INTERVAL",
    "LOW_BUY_EXIT_LOOP_INTERVAL",
    "ML_AUTO_LOOP_INTERVAL",
    "ML_SIGNAL_THRESHOLD",
    "ML_SIGNAL_COOLDOWN_SEC",
    "ML_DEFAULT_SIZE",
    # News and sentiment
    "NEWS_POLL_INTERVAL",
    "NEWS_STARTUP_BOOTSTRAP_INTERVAL",
    "NEWS_RSS_TIMEOUT",
    "SENTIMENT_POS_THRESHOLD",
    "NEWS_SRC_BACKOFF_MIN",
    "NEWS_SRC_BACKOFF_MAX",
    "NEWS_HEALTH_THRESHOLD",
    "NEWS_SOURCE_STALL_THRESHOLD_SEC",
    "NEWS_DEDUP_MIN_RATIO",
    "NEWS_DEDUP_VOLATILITY_THRESHOLD",
    # Trading live params
    "LIVE_TRADE_BASE_SIZE",
    "SCALE_IN_SIZE_RATIO",
    "MIN_ADD_DISTANCE_BPS",
    "SI_PROB_DELTA_MIN",
    "TRAILING_TP_PCT",
    # Promotion thresholds
    "PROMOTION_MAX_BRIER_DEGRADATION",
    "PROMOTION_MAX_ECE_DEGRADATION",
    "PROMOTION_MIN_ABS_AUC_DELTA",
    # Risk engine, low-buy service
    "RISK_STARTING_EQUITY",
    "LOW_BUY_DEFAULT_SIZE",
    "LOW_BUY_DISTANCE_PCT",
    "LOW_BUY_MIN_DROP_PCT",
    "LOW_BUY_RELAX_DISTANCE_MULT",
    "LOW_BUY_RELAX_DROP_MULT",
    "LOW_BUY_RELAX_RSI_DELTA",
    "LOW_BUY_RELAX_MOMENTUM_DELTA",
]

def reload_env_from_files() -> None:
    """Reload .env and .env.private from CWD and repo root into os.environ.
    Private overrides win. Safe to call at runtime.
    """
    try:
        from dotenv import load_dotenv  # type: ignore
        # Base .env (CWD then repo-root; no override)
        try:
            load_dotenv(override=False)
        except Exception:
            pass
        alt_env = os.path.join(_repo_root(), '.env')
        if os.path.exists(alt_env):
            try:
                load_dotenv(alt_env, override=False)
            except Exception:
                pass
        # Private overrides (CWD then repo-root; override=True)
        if os.path.exists('.env.private'):
            try:
                load_dotenv('.env.private', override=True)
            except Exception:
                pass
        alt_private = os.path.join(_repo_root(), '.env.private')
        if os.path.exists(alt_private):
            try:
                load_dotenv(alt_private, override=True)
            except Exception:
                pass
    except Exception:
        # Fallback manual parser
        def _load_env_file(path: str, override: bool = False) -> None:
            try:
                if not os.path.exists(path):
                    return
                with open(path, 'r', encoding='utf-8') as f:
                    for raw in f:
                        line = raw.strip()
                        if not line or line.startswith('#') or '=' not in line:
                            continue
                        key, val = line.split('=', 1)
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if not key:
                            continue
                        if override or (key not in os.environ):
                            os.environ[key] = val
            except Exception:
                pass
        _load_env_file('.env', override=False)
        _load_env_file(os.path.join(_repo_root(), '.env'), override=False)
        _load_env_file('.env.private', override=True)
        _load_env_file(os.path.join(_repo_root(), '.env.private'), override=True)
try:  # pragma: no cover
    from dotenv import load_dotenv  # type: ignore
    # 0) Determine candidate roots
    # use helper defined above

    # 1) Load base .env (if present). Try CWD first, then repo root.
    loaded_env = load_dotenv()
    if not loaded_env:
        alt_env = os.path.join(_repo_root(), '.env')
        if os.path.exists(alt_env):
            loaded_env = load_dotenv(alt_env)
    if loaded_env:
        _log.info("[config] .env 파일 로드 완료")
    else:
        _log.info("[config] .env 파일 로드되지 않았거나 존재하지 않음")
    # 2) Merge local-private overrides (if present). Try CWD then repo root. override=True so private takes precedence.
    if os.path.exists('.env.private'):
        load_dotenv('.env.private', override=True)
        _log.info("[config] .env.private 병합 로드 완료 (override)")
    else:
        alt_private = os.path.join(_repo_root(), '.env.private')
        if os.path.exists(alt_private):
            load_dotenv(alt_private, override=True)
            _log.info("[config] .env.private 병합 로드 완료 (override, repo-root)")
    # Additionally, ensure repo-root .env is loaded (non-override) even if CWD .env existed
    alt_env2 = os.path.join(_repo_root(), '.env')
    try:
        if os.path.exists(alt_env2):
            load_dotenv(alt_env2, override=False)
            _log.info("[config] .env (repo-root) 보조 로드 완료 (no-override)")
    except Exception:
        pass
    # Sanitize inline comments in numeric envs
    try:
        _sanitize_inline_commented_env(SANITIZE_KEYS)
    except Exception:
        pass
    # And ensure repo-root .env.private applies overrides last
    alt_private2 = os.path.join(_repo_root(), '.env.private')
    try:
        if os.path.exists(alt_private2):
            load_dotenv(alt_private2, override=True)
            _log.info("[config] .env.private (repo-root) 보조 로드 완료 (override)")
    except Exception:
        pass
except Exception:  # pragma: no cover
    # Fallback: parse .env and .env.private manually to populate os.environ
    # Also search from repository root (relative to this file) to be robust to CWD differences.
    _log.debug("[config] python-dotenv 미사용 (선택사항) - 수동 로더 사용 + repo-root 탐색")

    def _find_repo_root() -> str:
        """Best-effort repository root based on this file location.
        base_config.py is at backend/common/config/base_config.py => repo root is three levels up.
        """
        here = os.path.abspath(os.path.dirname(__file__))
        repo = os.path.abspath(os.path.join(here, '../../..'))
        return repo

    def _candidate_paths(filename: str) -> list[str]:
        paths = []
        # 1) Current working directory
        try:
            paths.append(os.path.abspath(os.path.join(os.getcwd(), filename)))
        except Exception:
            pass
        # 2) Repository root (derived)
        try:
            paths.append(os.path.join(_find_repo_root(), filename))
        except Exception:
            pass
        # 3) Location relative to this file (walk upwards a few levels)
        try:
            here = os.path.abspath(os.path.dirname(__file__))
            for up in ('.', '..', '../..', '../../..', '../../../..'):
                paths.append(os.path.abspath(os.path.join(here, up, filename)))
        except Exception:
            pass
        # Deduplicate while preserving order
        seen = set()
        uniq = []
        for p in paths:
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        return uniq

    def _load_env_file(path: str, override: bool = False) -> bool:
        try:
            if not os.path.exists(path):
                return False
            with open(path, 'r', encoding='utf-8') as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' not in line:
                        continue
                    key, val = line.split('=', 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if not key:
                        continue
                    if override or (key not in os.environ):
                        os.environ[key] = val
            return True
        except Exception:
            return False

    def _try_load(filename: str, override: bool) -> str | None:
        for p in _candidate_paths(filename):
            if _load_env_file(p, override=override):
                return p
        return None

    # Load base .env without override first (try multiple candidate locations)
    loaded_env_path = _try_load('.env', override=False)
    if loaded_env_path:
        _log.info(f"[config] .env 파일 로드 완료 (path={loaded_env_path})")
    else:
        _log.info("[config] .env 파일 로드되지 않았거나 존재하지 않음")
    # Then apply private overrides
    loaded_private_path = _try_load('.env.private', override=True)
    if loaded_private_path:
        _log.info(f"[config] .env.private 병합 로드 완료 (override, path={loaded_private_path})")
    # Sanitize inline comments in numeric envs
    try:
        _sanitize_inline_commented_env(SANITIZE_KEYS)
    except Exception:
        pass

def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    v = val.strip().lower()
    return v in ("1", "true", "t", "yes", "y", "on")
def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    s = str(val).strip()
    # Strip inline comments like "60  # comment"
    if "#" in s:
        s = s.split("#", 1)[0].strip()
    try:
        return int(s)
    except Exception:
        try:
            return int(float(s))
        except Exception:
            return default
def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None:
        return default
    s = str(val).strip()
    if "#" in s:
        s = s.split("#", 1)[0].strip()
    try:
        return float(s)
    except Exception:
        return default
from pydantic import BaseModel, Field

# (중복 로드 방지) 위에서 .env/.env.private를 처리했으므로 추가 로드는 생략

class AppConfig(BaseModel):
    environment: str = Field(default=os.getenv("APP_ENV", "dev"))
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)

    # Database settings (support both POSTGRES_* and legacy DB_* variable names)
    postgres_host: str = Field(default=os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")))
    postgres_port: int = Field(default=_env_int("POSTGRES_PORT", int(os.getenv("DB_PORT", 5432))))
    postgres_db: str = Field(default=os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "mydata")))
    postgres_user: str = Field(default=os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")))
    postgres_password: str = Field(default=os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "traderpass")))

    symbol: str = Field(default=os.getenv("SYMBOL", "XRPUSDT"))
    kline_interval: str = Field(default=os.getenv("INTERVAL", "1m"))
    feature_sched_interval: int = Field(default=_env_int("FEATURE_SCHED_INTERVAL", 60))
    ingestion_enabled: bool = Field(default=os.getenv("INGESTION_ENABLED", "false").lower() == "true")
    health_max_feature_lag_sec: int = Field(default=_env_int("HEALTH_MAX_FEATURE_LAG_SEC", 180))
    health_max_ingestion_lag_sec: int = Field(default=_env_int("HEALTH_MAX_INGESTION_LAG_SEC", 120))
    drift_z_threshold: float = Field(default=float(os.getenv("DRIFT_Z_THRESHOLD", 3.0)))
    drift_z_threshold: float = Field(default=_env_float("DRIFT_Z_THRESHOLD", 3.0))
    model_artifact_dir: str = Field(default=os.getenv("MODEL_ARTIFACT_DIR", "artifacts/models"))
    auto_retrain_enabled: bool = Field(default=os.getenv("AUTO_RETRAIN_ENABLED", "false").lower() == "true")
    auto_retrain_check_interval: int = Field(default=_env_int("AUTO_RETRAIN_CHECK_INTERVAL", 300))  # seconds
    auto_retrain_min_interval: int = Field(default=_env_int("AUTO_RETRAIN_MIN_INTERVAL", 3600))  # seconds between successful runs
    auto_retrain_required_consecutive_drifts: int = Field(default=_env_int("AUTO_RETRAIN_REQUIRED_CONSECUTIVE_DRIFTS", 2))
    auto_retrain_min_samples: int = Field(default=_env_int("AUTO_RETRAIN_MIN_SAMPLES", 500))
    auto_promote_enabled: bool = Field(default=os.getenv("AUTO_PROMOTE_ENABLED", "false").lower() == "true")
    auto_promote_min_sample_growth: float = Field(default=float(os.getenv("AUTO_PROMOTE_MIN_SAMPLE_GROWTH", 1.05)))  # 5% more samples
    auto_promote_min_interval: int = Field(default=_env_int("AUTO_PROMOTE_MIN_INTERVAL", 1800))  # seconds since last promotion
    # Default model family is bottom predictor (direction removed)
    auto_promote_model_name: str = Field(default=os.getenv("AUTO_PROMOTE_MODEL_NAME", "bottom_predictor"))
    auto_retrain_lock_key: int = Field(default=int(os.getenv("AUTO_RETRAIN_LOCK_KEY", 821337)))  # arbitrary default
    # Multi-feature drift configuration
    auto_retrain_drift_features: Optional[str] = Field(default=os.getenv("AUTO_RETRAIN_DRIFT_FEATURES"))  # comma list
    auto_retrain_drift_mode: str = Field(default=os.getenv("AUTO_RETRAIN_DRIFT_MODE", "max_abs"))  # max_abs | mean_top3
    auto_retrain_drift_window: int = Field(default=_env_int("AUTO_RETRAIN_DRIFT_WINDOW", 200))
    # Promotion performance threshold (AUC relative improvement over production)
    auto_promote_min_auc_improve: float = Field(default=float(os.getenv("AUTO_PROMOTE_MIN_AUC_IMPROVE", 0.01)))  # 1% rel
    # Inference probability threshold for positive class decision
    inference_prob_threshold: float = Field(default=float(os.getenv("INFERENCE_PROB_THRESHOLD", 0.5)))
    # Auto inference loop (주기적으로 /api 없이 내부에서 predict_latest 실행)
    inference_auto_loop_enabled: bool = Field(default=os.getenv("INFERENCE_AUTO_LOOP_ENABLED", "false").lower() == "true")
    inference_auto_loop_interval: float = Field(default=float(os.getenv("INFERENCE_AUTO_LOOP_INTERVAL", 15.0)))  # seconds
    inference_auto_loop_interval: float = Field(default=_env_float("INFERENCE_AUTO_LOOP_INTERVAL", 15.0))  # seconds
    # Async inference logging queue settings
    inference_log_queue_max: int = Field(default=_env_int("INFERENCE_LOG_QUEUE_MAX", 5000))
    inference_log_flush_batch: int = Field(default=_env_int("INFERENCE_LOG_FLUSH_BATCH", 200))
    inference_log_flush_interval: float = Field(default=float(os.getenv("INFERENCE_LOG_FLUSH_INTERVAL", 2.0)))  # seconds
    inference_log_flush_interval: float = Field(default=_env_float("INFERENCE_LOG_FLUSH_INTERVAL", 2.0))  # seconds
    # Adaptive inference log queue flushing
    inference_log_flush_min_interval: float = Field(default=float(os.getenv("INFERENCE_LOG_FLUSH_MIN_INTERVAL", 0.25)))
    inference_log_flush_min_interval: float = Field(default=_env_float("INFERENCE_LOG_FLUSH_MIN_INTERVAL", 0.25))
    inference_log_flush_high_watermark: float = Field(default=_env_float("INFERENCE_LOG_FLUSH_HIGH_WATERMARK", 0.7))  # ratio of max
    inference_log_flush_low_watermark: float = Field(default=_env_float("INFERENCE_LOG_FLUSH_LOW_WATERMARK", 0.2))   # ratio of max
    # Risk metrics update interval
    risk_metrics_interval: float = Field(default=float(os.getenv("RISK_METRICS_INTERVAL", 5.0)))
    risk_metrics_interval: float = Field(default=_env_float("RISK_METRICS_INTERVAL", 5.0))
    # Production model metrics refresh interval (seconds)
    production_metrics_interval: float = Field(default=float(os.getenv("PRODUCTION_METRICS_INTERVAL", 60.0)))
    production_metrics_interval: float = Field(default=_env_float("PRODUCTION_METRICS_INTERVAL", 60.0))
    # Auto realized labeler settings
    auto_labeler_enabled: bool = Field(default=os.getenv("AUTO_LABELER_ENABLED", "false").lower() == "true")
    auto_labeler_interval: float = Field(default=float(os.getenv("AUTO_LABELER_INTERVAL", 30.0)))  # seconds between scans
    auto_labeler_interval: float = Field(default=_env_float("AUTO_LABELER_INTERVAL", 30.0))  # seconds between scans
    auto_labeler_min_age_seconds: int = Field(default=_env_int("AUTO_LABELER_MIN_AGE_SECONDS", 60))  # only label inferences older than this
    auto_labeler_batch_limit: int = Field(default=_env_int("AUTO_LABELER_BATCH_LIMIT", 500))  # max rows per scan
    # Calibration drift monitor
    calibration_monitor_enabled: bool = Field(default=os.getenv("CALIBRATION_MONITOR_ENABLED", "false").lower() == "true")
    calibration_monitor_interval: float = Field(default=float(os.getenv("CALIBRATION_MONITOR_INTERVAL", 60.0)))  # seconds
    calibration_monitor_interval: float = Field(default=_env_float("CALIBRATION_MONITOR_INTERVAL", 60.0))  # seconds
    calibration_monitor_window_seconds: int = Field(default=_env_int("CALIBRATION_MONITOR_WINDOW_SECONDS", 3600))
    calibration_monitor_ece_drift_abs: float = Field(default=float(os.getenv("CALIBRATION_MONITOR_ECE_DRIFT_ABS", 0.05)))  # absolute ECE gap
    calibration_monitor_ece_drift_abs: float = Field(default=_env_float("CALIBRATION_MONITOR_ECE_DRIFT_ABS", 0.05))  # absolute ECE gap
    calibration_monitor_ece_drift_rel: float = Field(default=_env_float("CALIBRATION_MONITOR_ECE_DRIFT_REL", 0.5))  # relative (delta/base)
    calibration_monitor_min_samples: int = Field(default=_env_int("CALIBRATION_MONITOR_MIN_SAMPLES", 200))
    # Streak-based escalation triggers (e.g., for retrain recommendation)
    calibration_monitor_abs_streak_trigger: int = Field(default=_env_int("CALIBRATION_MONITOR_ABS_STREAK_TRIGGER", 3))
    calibration_monitor_rel_streak_trigger: int = Field(default=_env_int("CALIBRATION_MONITOR_REL_STREAK_TRIGGER", 3))
    # Recommendation gating & heuristics
    # If > 0, once a recommendation is issued, suppress further recommendations for this many seconds
    calibration_recommend_cooldown_seconds: int = Field(default=_env_int("CALIBRATION_RECOMMEND_COOLDOWN_SECONDS", 0))
    # Heuristic multiplier for absolute delta: recommend when delta >= multiplier * abs_threshold (default behavior 2.0)
    calibration_abs_delta_multiplier: float = Field(default=float(os.getenv("CALIBRATION_ABS_DELTA_MULTIPLIER", 2.0)))
    # Calibration-based auto retrain (leverages recommendation metrics)
    calibration_retrain_enabled: bool = Field(default=os.getenv("CALIBRATION_RETRAIN_ENABLED", "false").lower() == "true")
    # Optional: skip DB (start API even if DB is unavailable); endpoints needing DB will likely error
    skip_db: bool = Field(default=os.getenv("SKIP_DB", "false").lower() == "true")
    calibration_retrain_min_interval: int = Field(default=_env_int("CALIBRATION_RETRAIN_MIN_INTERVAL", 7200))  # 2h default
    # New configurable training promotion thresholds (absolute deltas)
    training_promote_min_auc_delta: float = Field(default=float(os.getenv("TRAINING_PROMOTE_MIN_AUC_DELTA", 0.005)))
    training_promote_min_auc_delta: float = Field(default=_env_float("TRAINING_PROMOTE_MIN_AUC_DELTA", 0.005))
    training_promote_max_ece_delta: float = Field(default=_env_float("TRAINING_PROMOTE_MAX_ECE_DELTA", 0.002))
    training_promote_min_val_samples: int = Field(default=_env_int("TRAINING_PROMOTE_MIN_VAL_SAMPLES", 50))
    # Validation split fraction for final hold-out (e.g., 0.2 => 80/20 train/val)
    training_validation_fraction: float = Field(default=float(os.getenv("TRAINING_VALIDATION_FRACTION", 0.2)))
    training_validation_fraction: float = Field(default=_env_float("TRAINING_VALIDATION_FRACTION", 0.2))
    # Promotion alerting
    promotion_alert_enabled: bool = Field(default=os.getenv("PROMOTION_ALERT_ENABLED", "false").lower() == "true")
    promotion_alert_webhook_url: Optional[str] = Field(default=os.getenv("PROMOTION_ALERT_WEBHOOK_URL"))
    promotion_alert_min_window: int = Field(default=_env_int("PROMOTION_ALERT_MIN_WINDOW", 100))  # ensure enough samples
    promotion_alert_low_auc_ratio: float = Field(default=float(os.getenv("PROMOTION_ALERT_LOW_AUC_RATIO", 0.8)))  # avg auc_ratio below this
    promotion_alert_low_auc_ratio: float = Field(default=_env_float("PROMOTION_ALERT_LOW_AUC_RATIO", 0.8))  # avg auc_ratio below this
    promotion_alert_low_ece_margin_ratio: float = Field(default=_env_float("PROMOTION_ALERT_LOW_ECE_MARGIN_RATIO", 0.3))  # avg ece_margin below
    promotion_alert_cooldown_seconds: int = Field(default=_env_int("PROMOTION_ALERT_COOLDOWN_SECONDS", 1800))  # 30m default
    # Rate limiting (simple in-memory token bucket per (ip, api_key, bucket))
    rate_limit_enabled: bool = Field(default=os.getenv("RATE_LIMIT_ENABLED", "false").lower() == "true")
    rate_limit_training_rps: float = Field(default=float(os.getenv("RATE_LIMIT_TRAINING_RPS", 0.2)))  # 0.2 req/sec = 1 every 5s
    rate_limit_training_rps: float = Field(default=_env_float("RATE_LIMIT_TRAINING_RPS", 0.2))  # 0.2 req/sec = 1 every 5s
    rate_limit_inference_rps: float = Field(default=_env_float("RATE_LIMIT_INFERENCE_RPS", 5.0))  # allow higher inference throughput
    calibration_abs_delta_multiplier: float = Field(default=_env_float("CALIBRATION_ABS_DELTA_MULTIPLIER", 2.0))
    rate_limit_training_burst: int = Field(default=_env_int("RATE_LIMIT_TRAINING_BURST", 2))
    rate_limit_inference_burst: int = Field(default=_env_int("RATE_LIMIT_INFERENCE_BURST", 20))
    # Calibration CV degradation threshold (relative ratio: last_cv_mean_auc / production_auc)
    calibration_cv_degradation_min_ratio: float = Field(default=float(os.getenv("CALIBRATION_CV_DEGRADATION_MIN_RATIO", 0.95)))
    calibration_cv_degradation_min_ratio: float = Field(default=_env_float("CALIBRATION_CV_DEGRADATION_MIN_RATIO", 0.95))
    # Inference Playground direction forecast (removed in bottom-only mode)
    playground_direction_card: bool = Field(default=False)
    playground_direction_horizons: str = Field(default="")
    playground_direction_thresh: float = Field(default=0.5)
    playground_direction_thresh: float = Field(default=0.5)
    # Training target selection (bottom-only)
    training_target_type: str = Field(default=os.getenv("TRAINING_TARGET_TYPE", "bottom"))
    # Bottom-event label params (defaults are conservative; tune per market)
    bottom_lookahead: int = Field(default=_env_int("BOTTOM_LOOKAHEAD", 30))
    bottom_drawdown: float = Field(default=float(os.getenv("BOTTOM_DRAWDOWN", 0.005)))
    bottom_drawdown: float = Field(default=_env_float("BOTTOM_DRAWDOWN", 0.005))
    bottom_rebound: float = Field(default=_env_float("BOTTOM_REBOUND", 0.003))
    # Max candles to fetch when building bottom-event labels (increase to see more events)
    bottom_ohlcv_fetch_cap: int = Field(default=_env_int("BOTTOM_OHLCV_FETCH_CAP", 5000))
    # Minimum labeled samples required for bottom training to proceed
    bottom_min_labels: int = Field(default=_env_int("BOTTOM_MIN_LABELS", 150))
    # OHLCV partial(진행중 미종가 캔들) 전역 포함 여부 (API 기본값 & WS snapshot 정책)
    ohlcv_include_open_default: bool = Field(default=_env_bool("OHLCV_INCLUDE_OPEN_DEFAULT", True))
    ohlcv_ws_snapshot_include_open: bool = Field(default=_env_bool("OHLCV_WS_SNAPSHOT_INCLUDE_OPEN", False))
    # Kline consumer batch size (flush after N closed klines); small value (e.g. 1) to force fast append broadcasts in dev
    kline_consumer_batch_size: int = Field(default=_env_int("KLINE_CONSUMER_BATCH_SIZE", 50))
    # Partial 업데이트 최소 전송 주기(ms) (서버 측 coalesce 정책 - P1 에서는 관리자 mock 테스트 용)
    ohlcv_partial_min_period_ms: int = Field(default=_env_int("OHLCV_PARTIAL_MIN_PERIOD_MS", 500))
    # Delta 엔드포인트 최대 limit
    ohlcv_delta_max_limit: int = Field(default=_env_int("OHLCV_DELTA_MAX_LIMIT", 2000))
    # Trading fees (Binance-like). Defaults: taker 0.1%, maker 0.1%.
    trading_fee_taker: float = Field(default=float(os.getenv("TRADING_FEE_TAKER", 0.001)))
    trading_fee_taker: float = Field(default=_env_float("TRADING_FEE_TAKER", 0.001))
    trading_fee_maker: float = Field(default=_env_float("TRADING_FEE_MAKER", 0.001))
    # fee mode used for simulated fills: 'taker' or 'maker'
    trading_fee_mode: str = Field(default=os.getenv("TRADING_FEE_MODE", "taker"))

    # Exchange integration (Binance) – keys are optional unless using private endpoints
    exchange: str = Field(default=os.getenv("EXCHANGE", "binance"))  # binance | mock
    binance_api_key: Optional[str] = Field(default=os.getenv("BINANCE_API_KEY"))
    binance_api_secret: Optional[str] = Field(default=os.getenv("BINANCE_API_SECRET"))
    binance_testnet: bool = Field(default=_env_bool("BINANCE_TESTNET", False))
    binance_account_type: str = Field(default=os.getenv("BINANCE_ACCOUNT_TYPE", "futures"))  # futures | spot
    # Optional override URLs if needed
    binance_futures_rest: str = Field(default=os.getenv("BINANCE_FUTURES_REST", "https://fapi.binance.com"))
    binance_futures_ws: str = Field(default=os.getenv("BINANCE_FUTURES_WS", "wss://fstream.binance.com/ws"))
    # Low-buy autopilot loop controls
    low_buy_auto_loop_enabled: bool = Field(default=os.getenv("LOW_BUY_AUTO_LOOP_ENABLED", "true").lower() == "true")
    low_buy_auto_loop_interval: float = Field(default=float(os.getenv("LOW_BUY_AUTO_LOOP_INTERVAL", 20.0)))
    low_buy_auto_loop_interval: float = Field(default=_env_float("LOW_BUY_AUTO_LOOP_INTERVAL", 20.0))
    low_buy_signal_ttl: float = Field(default=_env_float("LOW_BUY_SIGNAL_TTL", 120.0))
    low_buy_auto_execute_enabled: bool = Field(default=os.getenv("LOW_BUY_AUTO_EXECUTE_ENABLED", "true").lower() == "true")
    low_buy_auto_execute_paper: bool = Field(default=os.getenv("LOW_BUY_AUTO_EXECUTE_PAPER", "true").lower() == "true")
    autopilot_mode: str = Field(default=os.getenv("AUTOPILOT_MODE", "paper"))
    low_buy_exit_loop_enabled: bool = Field(default=os.getenv("LOW_BUY_EXIT_LOOP_ENABLED", "true").lower() == "true")
    low_buy_exit_loop_interval: float = Field(default=float(os.getenv("LOW_BUY_EXIT_LOOP_INTERVAL", 10.0)))
    low_buy_exit_loop_interval: float = Field(default=_env_float("LOW_BUY_EXIT_LOOP_INTERVAL", 10.0))
    low_buy_take_profit_pct: float = Field(default=_env_float("LOW_BUY_TAKE_PROFIT_PCT", 0.008))
    low_buy_stop_loss_pct: float = Field(default=_env_float("LOW_BUY_STOP_LOSS_PCT", 0.005))

    # ML-driven signal controls
    # Default to 'ml' so environments without explicit env override use ML-based signals by default
    signal_source: str = Field(default=os.getenv("SIGNAL_SOURCE", "ml"))  # low_buy | ml
    ml_auto_loop_enabled: bool = Field(default=os.getenv("ML_AUTO_LOOP_ENABLED", "true").lower() == "true")
    ml_auto_loop_interval: float = Field(default=float(os.getenv("ML_AUTO_LOOP_INTERVAL", 10.0)))
    ml_auto_loop_interval: float = Field(default=_env_float("ML_AUTO_LOOP_INTERVAL", 10.0))
    ml_signal_threshold: float = Field(default=_env_float("ML_SIGNAL_THRESHOLD", _env_float("INFERENCE_PROB_THRESHOLD", 0.8)))
    ml_signal_cooldown_sec: float = Field(default=float(os.getenv("ML_SIGNAL_COOLDOWN_SEC", 120.0)))
    ml_signal_cooldown_sec: float = Field(default=_env_float("ML_SIGNAL_COOLDOWN_SEC", 120.0))
    ml_auto_execute_enabled: bool = Field(default=os.getenv("ML_AUTO_EXECUTE_ENABLED", "true").lower() == "true")
    ml_auto_execute_paper: bool = Field(default=os.getenv("ML_AUTO_EXECUTE_PAPER", "true").lower() == "true")
    ml_default_size: float = Field(default=float(os.getenv("ML_DEFAULT_SIZE", os.getenv("LOW_BUY_DEFAULT_SIZE", 1))))
    ml_default_size: float = Field(default=_env_float("ML_DEFAULT_SIZE", _env_float("LOW_BUY_DEFAULT_SIZE", 1)))
    ml_model_family: str = Field(default=os.getenv("ML_MODEL_FAMILY", os.getenv("TRAINING_TARGET_TYPE", "bottom")))  # bottom | direction

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"\
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def _get_env_float(name: str) -> float | None:
    v = os.getenv(name)
    if v is None:
        return None
    try:
        f = float(v)
        if f == float('inf') or f == float('-inf'):
            return None
        return f
    except Exception:
        return None

def _get_env_str(name: str) -> str | None:
    v = os.getenv(name)
    return v if (v is not None and str(v).strip() != '') else None

def load_config() -> AppConfig:
    # Ensure latest .env/.env.private are considered before reading
    try:
        reload_env_from_files()
    except Exception:
        pass
    cfg = AppConfig()
    # Dynamically override a small set of frequently tuned fields to reflect runtime env without process restart
    try:
        v = _get_env_float('INFERENCE_PROB_THRESHOLD')
        if v is not None and 0 < v < 1:
            cfg.inference_prob_threshold = v
    except Exception:
        pass
    try:
        v = _get_env_float('ML_SIGNAL_THRESHOLD')
        if v is not None and 0 < v < 1:
            cfg.ml_signal_threshold = v
    except Exception:
        pass
    try:
        v = _get_env_float('ML_SIGNAL_COOLDOWN_SEC')
        if v is not None and v >= 0:
            cfg.ml_signal_cooldown_sec = v
    except Exception:
        pass
    try:
        v = _get_env_float('ML_AUTO_LOOP_INTERVAL')
        if v is not None and v > 0:
            cfg.ml_auto_loop_interval = v
    except Exception:
        pass
    try:
        s = _get_env_str('SIGNAL_SOURCE')
        if s:
            cfg.signal_source = s
    except Exception:
        pass
    try:
        s = _get_env_str('ML_MODEL_FAMILY')
        if s:
            cfg.ml_model_family = s
    except Exception:
        pass
    return cfg
