from __future__ import annotations
import os
import logging

_log = logging.getLogger("config")
try:  # pragma: no cover
    from dotenv import load_dotenv  # type: ignore
    # 1) Load base .env (if present)
    loaded_env = load_dotenv()
    if loaded_env:
        _log.info("[config] .env 파일 로드 완료")
    else:
        _log.info("[config] .env 파일 로드되지 않았거나 존재하지 않음")
    # 2) Merge local-private overrides (if present). override=True so private takes precedence.
    if os.path.exists('.env.private'):
        load_dotenv('.env.private', override=True)
        _log.info("[config] .env.private 병합 로드 완료 (override)")
except Exception:  # pragma: no cover
    _log.debug("[config] python-dotenv 미사용 (선택사항)")

def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    v = val.strip().lower()
    return v in ("1", "true", "t", "yes", "y", "on")
from pydantic import BaseModel, Field

# (중복 로드 방지) 위에서 .env/.env.private를 처리했으므로 추가 로드는 생략

class AppConfig(BaseModel):
    environment: str = Field(default=os.getenv("APP_ENV", "dev"))
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)

    # Database settings (support both POSTGRES_* and legacy DB_* variable names)
    postgres_host: str = Field(default=os.getenv("POSTGRES_HOST", os.getenv("DB_HOST", "localhost")))
    postgres_port: int = Field(default=int(os.getenv("POSTGRES_PORT", os.getenv("DB_PORT", 5432))))
    postgres_db: str = Field(default=os.getenv("POSTGRES_DB", os.getenv("DB_NAME", "mydata")))
    postgres_user: str = Field(default=os.getenv("POSTGRES_USER", os.getenv("DB_USER", "postgres")))
    postgres_password: str = Field(default=os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD", "traderpass")))

    symbol: str = Field(default=os.getenv("SYMBOL", "XRPUSDT"))
    kline_interval: str = Field(default=os.getenv("INTERVAL", "1m"))
    feature_sched_interval: int = Field(default=int(os.getenv("FEATURE_SCHED_INTERVAL", 60)))
    ingestion_enabled: bool = Field(default=os.getenv("INGESTION_ENABLED", "false").lower() == "true")
    health_max_feature_lag_sec: int = Field(default=int(os.getenv("HEALTH_MAX_FEATURE_LAG_SEC", 180)))
    health_max_ingestion_lag_sec: int = Field(default=int(os.getenv("HEALTH_MAX_INGESTION_LAG_SEC", 120)))
    drift_z_threshold: float = Field(default=float(os.getenv("DRIFT_Z_THRESHOLD", 3.0)))
    model_artifact_dir: str = Field(default=os.getenv("MODEL_ARTIFACT_DIR", "artifacts/models"))
    auto_retrain_enabled: bool = Field(default=os.getenv("AUTO_RETRAIN_ENABLED", "false").lower() == "true")
    auto_retrain_check_interval: int = Field(default=int(os.getenv("AUTO_RETRAIN_CHECK_INTERVAL", 300)))  # seconds
    auto_retrain_min_interval: int = Field(default=int(os.getenv("AUTO_RETRAIN_MIN_INTERVAL", 3600)))  # seconds between successful runs
    auto_retrain_required_consecutive_drifts: int = Field(default=int(os.getenv("AUTO_RETRAIN_REQUIRED_CONSECUTIVE_DRIFTS", 2)))
    auto_retrain_min_samples: int = Field(default=int(os.getenv("AUTO_RETRAIN_MIN_SAMPLES", 500)))
    auto_promote_enabled: bool = Field(default=os.getenv("AUTO_PROMOTE_ENABLED", "false").lower() == "true")
    auto_promote_min_sample_growth: float = Field(default=float(os.getenv("AUTO_PROMOTE_MIN_SAMPLE_GROWTH", 1.05)))  # 5% more samples
    auto_promote_min_interval: int = Field(default=int(os.getenv("AUTO_PROMOTE_MIN_INTERVAL", 1800)))  # seconds since last promotion
    auto_promote_model_name: str = Field(default=os.getenv("AUTO_PROMOTE_MODEL_NAME", "baseline_predictor"))
    auto_retrain_lock_key: int = Field(default=int(os.getenv("AUTO_RETRAIN_LOCK_KEY", 821337)))  # arbitrary default
    # Multi-feature drift configuration
    auto_retrain_drift_features: str | None = Field(default=os.getenv("AUTO_RETRAIN_DRIFT_FEATURES"))  # comma list
    auto_retrain_drift_mode: str = Field(default=os.getenv("AUTO_RETRAIN_DRIFT_MODE", "max_abs"))  # max_abs | mean_top3
    auto_retrain_drift_window: int = Field(default=int(os.getenv("AUTO_RETRAIN_DRIFT_WINDOW", 200)))
    # Promotion performance threshold (AUC relative improvement over production)
    auto_promote_min_auc_improve: float = Field(default=float(os.getenv("AUTO_PROMOTE_MIN_AUC_IMPROVE", 0.01)))  # 1% rel
    # Inference probability threshold for positive class decision
    inference_prob_threshold: float = Field(default=float(os.getenv("INFERENCE_PROB_THRESHOLD", 0.5)))
    # Auto inference loop (주기적으로 /api 없이 내부에서 predict_latest 실행)
    inference_auto_loop_enabled: bool = Field(default=os.getenv("INFERENCE_AUTO_LOOP_ENABLED", "false").lower() == "true")
    inference_auto_loop_interval: float = Field(default=float(os.getenv("INFERENCE_AUTO_LOOP_INTERVAL", 15.0)))  # seconds
    # Async inference logging queue settings
    inference_log_queue_max: int = Field(default=int(os.getenv("INFERENCE_LOG_QUEUE_MAX", 5000)))
    inference_log_flush_batch: int = Field(default=int(os.getenv("INFERENCE_LOG_FLUSH_BATCH", 200)))
    inference_log_flush_interval: float = Field(default=float(os.getenv("INFERENCE_LOG_FLUSH_INTERVAL", 2.0)))  # seconds
    # Adaptive inference log queue flushing
    inference_log_flush_min_interval: float = Field(default=float(os.getenv("INFERENCE_LOG_FLUSH_MIN_INTERVAL", 0.25)))
    inference_log_flush_high_watermark: float = Field(default=float(os.getenv("INFERENCE_LOG_FLUSH_HIGH_WATERMARK", 0.7)))  # ratio of max
    inference_log_flush_low_watermark: float = Field(default=float(os.getenv("INFERENCE_LOG_FLUSH_LOW_WATERMARK", 0.2)))   # ratio of max
    # Risk metrics update interval
    risk_metrics_interval: float = Field(default=float(os.getenv("RISK_METRICS_INTERVAL", 5.0)))
    # Production model metrics refresh interval (seconds)
    production_metrics_interval: float = Field(default=float(os.getenv("PRODUCTION_METRICS_INTERVAL", 60.0)))
    # Auto realized labeler settings
    auto_labeler_enabled: bool = Field(default=os.getenv("AUTO_LABELER_ENABLED", "false").lower() == "true")
    auto_labeler_interval: float = Field(default=float(os.getenv("AUTO_LABELER_INTERVAL", 30.0)))  # seconds between scans
    auto_labeler_min_age_seconds: int = Field(default=int(os.getenv("AUTO_LABELER_MIN_AGE_SECONDS", 60)))  # only label inferences older than this
    auto_labeler_batch_limit: int = Field(default=int(os.getenv("AUTO_LABELER_BATCH_LIMIT", 500)))  # max rows per scan
    # Calibration drift monitor
    calibration_monitor_enabled: bool = Field(default=os.getenv("CALIBRATION_MONITOR_ENABLED", "false").lower() == "true")
    calibration_monitor_interval: float = Field(default=float(os.getenv("CALIBRATION_MONITOR_INTERVAL", 60.0)))  # seconds
    calibration_monitor_window_seconds: int = Field(default=int(os.getenv("CALIBRATION_MONITOR_WINDOW_SECONDS", 3600)))
    calibration_monitor_ece_drift_abs: float = Field(default=float(os.getenv("CALIBRATION_MONITOR_ECE_DRIFT_ABS", 0.05)))  # absolute ECE gap
    calibration_monitor_ece_drift_rel: float = Field(default=float(os.getenv("CALIBRATION_MONITOR_ECE_DRIFT_REL", 0.5)))  # relative (delta/base)
    calibration_monitor_min_samples: int = Field(default=int(os.getenv("CALIBRATION_MONITOR_MIN_SAMPLES", 200)))
    # Streak-based escalation triggers (e.g., for retrain recommendation)
    calibration_monitor_abs_streak_trigger: int = Field(default=int(os.getenv("CALIBRATION_MONITOR_ABS_STREAK_TRIGGER", 3)))
    calibration_monitor_rel_streak_trigger: int = Field(default=int(os.getenv("CALIBRATION_MONITOR_REL_STREAK_TRIGGER", 3)))
    # Recommendation gating & heuristics
    # If > 0, once a recommendation is issued, suppress further recommendations for this many seconds
    calibration_recommend_cooldown_seconds: int = Field(default=int(os.getenv("CALIBRATION_RECOMMEND_COOLDOWN_SECONDS", 0)))
    # Heuristic multiplier for absolute delta: recommend when delta >= multiplier * abs_threshold (default behavior 2.0)
    calibration_abs_delta_multiplier: float = Field(default=float(os.getenv("CALIBRATION_ABS_DELTA_MULTIPLIER", 2.0)))
    # Calibration-based auto retrain (leverages recommendation metrics)
    calibration_retrain_enabled: bool = Field(default=os.getenv("CALIBRATION_RETRAIN_ENABLED", "false").lower() == "true")
    # Optional: skip DB (start API even if DB is unavailable); endpoints needing DB will likely error
    skip_db: bool = Field(default=os.getenv("SKIP_DB", "false").lower() == "true")
    calibration_retrain_min_interval: int = Field(default=int(os.getenv("CALIBRATION_RETRAIN_MIN_INTERVAL", 7200)))  # 2h default
    # New configurable training promotion thresholds (absolute deltas)
    training_promote_min_auc_delta: float = Field(default=float(os.getenv("TRAINING_PROMOTE_MIN_AUC_DELTA", 0.005)))
    training_promote_max_ece_delta: float = Field(default=float(os.getenv("TRAINING_PROMOTE_MAX_ECE_DELTA", 0.002)))
    training_promote_min_val_samples: int = Field(default=int(os.getenv("TRAINING_PROMOTE_MIN_VAL_SAMPLES", 50)))
    # Validation split fraction for final hold-out (e.g., 0.2 => 80/20 train/val)
    training_validation_fraction: float = Field(default=float(os.getenv("TRAINING_VALIDATION_FRACTION", 0.2)))
    # Promotion alerting
    promotion_alert_enabled: bool = Field(default=os.getenv("PROMOTION_ALERT_ENABLED", "false").lower() == "true")
    promotion_alert_webhook_url: str | None = Field(default=os.getenv("PROMOTION_ALERT_WEBHOOK_URL"))
    promotion_alert_min_window: int = Field(default=int(os.getenv("PROMOTION_ALERT_MIN_WINDOW", 100)))  # ensure enough samples
    promotion_alert_low_auc_ratio: float = Field(default=float(os.getenv("PROMOTION_ALERT_LOW_AUC_RATIO", 0.8)))  # avg auc_ratio below this
    promotion_alert_low_ece_margin_ratio: float = Field(default=float(os.getenv("PROMOTION_ALERT_LOW_ECE_MARGIN_RATIO", 0.3)))  # avg ece_margin below
    promotion_alert_cooldown_seconds: int = Field(default=int(os.getenv("PROMOTION_ALERT_COOLDOWN_SECONDS", 1800)))  # 30m default
    # Rate limiting (simple in-memory token bucket per (ip, api_key, bucket))
    rate_limit_enabled: bool = Field(default=os.getenv("RATE_LIMIT_ENABLED", "false").lower() == "true")
    rate_limit_training_rps: float = Field(default=float(os.getenv("RATE_LIMIT_TRAINING_RPS", 0.2)))  # 0.2 req/sec = 1 every 5s
    rate_limit_inference_rps: float = Field(default=float(os.getenv("RATE_LIMIT_INFERENCE_RPS", 5.0)))  # allow higher inference throughput
    rate_limit_training_burst: int = Field(default=int(os.getenv("RATE_LIMIT_TRAINING_BURST", 2)))
    rate_limit_inference_burst: int = Field(default=int(os.getenv("RATE_LIMIT_INFERENCE_BURST", 20)))
    # Calibration CV degradation threshold (relative ratio: last_cv_mean_auc / production_auc)
    calibration_cv_degradation_min_ratio: float = Field(default=float(os.getenv("CALIBRATION_CV_DEGRADATION_MIN_RATIO", 0.95)))
    # Inference Playground direction forecast (UI helper)
    playground_direction_card: bool = Field(default=_env_bool("PLAYGROUND_DIRECTION_CARD", False))
    playground_direction_horizons: str = Field(default=os.getenv("PLAYGROUND_DIRECTION_HORIZONS", "1m,5m,15m"))
    playground_direction_thresh: float = Field(default=float(os.getenv("PLAYGROUND_DIRECTION_THRESH", 0.45)))
    # Training target selection and bottom-event parameters (for upcoming bottom-detection support)
    # training_target_type: 'direction' | 'bottom'
    training_target_type: str = Field(default=os.getenv("TRAINING_TARGET_TYPE", "direction"))
    # Bottom-event label params (defaults are conservative; tune per market)
    bottom_lookahead: int = Field(default=int(os.getenv("BOTTOM_LOOKAHEAD", 30)))
    bottom_drawdown: float = Field(default=float(os.getenv("BOTTOM_DRAWDOWN", 0.005)))
    bottom_rebound: float = Field(default=float(os.getenv("BOTTOM_REBOUND", 0.003)))
    # OHLCV partial(진행중 미종가 캔들) 전역 포함 여부 (API 기본값 & WS snapshot 정책)
    ohlcv_include_open_default: bool = Field(default=_env_bool("OHLCV_INCLUDE_OPEN_DEFAULT", True))
    ohlcv_ws_snapshot_include_open: bool = Field(default=_env_bool("OHLCV_WS_SNAPSHOT_INCLUDE_OPEN", False))
    # Kline consumer batch size (flush after N closed klines); small value (e.g. 1) to force fast append broadcasts in dev
    kline_consumer_batch_size: int = Field(default=int(os.getenv("KLINE_CONSUMER_BATCH_SIZE", 50)))
    # Partial 업데이트 최소 전송 주기(ms) (서버 측 coalesce 정책 - P1 에서는 관리자 mock 테스트 용)
    ohlcv_partial_min_period_ms: int = Field(default=int(os.getenv("OHLCV_PARTIAL_MIN_PERIOD_MS", 500)))
    # Delta 엔드포인트 최대 limit
    ohlcv_delta_max_limit: int = Field(default=int(os.getenv("OHLCV_DELTA_MAX_LIMIT", 2000)))
    # Trading fees (Binance-like). Defaults: taker 0.1%, maker 0.1%.
    trading_fee_taker: float = Field(default=float(os.getenv("TRADING_FEE_TAKER", 0.001)))
    trading_fee_maker: float = Field(default=float(os.getenv("TRADING_FEE_MAKER", 0.001)))
    # fee mode used for simulated fills: 'taker' or 'maker'
    trading_fee_mode: str = Field(default=os.getenv("TRADING_FEE_MODE", "taker"))

    # Exchange integration (Binance) – keys are optional unless using private endpoints
    exchange: str = Field(default=os.getenv("EXCHANGE", "binance"))  # binance | mock
    binance_api_key: str | None = Field(default=os.getenv("BINANCE_API_KEY"))
    binance_api_secret: str | None = Field(default=os.getenv("BINANCE_API_SECRET"))
    binance_testnet: bool = Field(default=_env_bool("BINANCE_TESTNET", False))
    binance_account_type: str = Field(default=os.getenv("BINANCE_ACCOUNT_TYPE", "futures"))  # futures | spot
    # Optional override URLs if needed
    binance_futures_rest: str = Field(default=os.getenv("BINANCE_FUTURES_REST", "https://fapi.binance.com"))
    binance_futures_ws: str = Field(default=os.getenv("BINANCE_FUTURES_WS", "wss://fstream.binance.com/ws"))

    @property
    def dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"\
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def load_config() -> AppConfig:
    return AppConfig()
