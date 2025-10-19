"""Central schema manager to ensure all application tables exist.

This aggregates the DDL for each logical component so an operator can
provision or repair a blank database with a single call.
"""
from __future__ import annotations
import asyncpg
from backend.common.db.connection import init_pool
import os

# --- DDL DEFINITIONS -------------------------------------------------------

DDL_NEWS = """
CREATE TABLE IF NOT EXISTS news_articles (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    symbol TEXT NULL,
    title TEXT NOT NULL,
    body TEXT NULL,
    url TEXT NULL,
    published_ts BIGINT NOT NULL,
    ingested_ts BIGINT NOT NULL,
    sentiment REAL NULL,
    lang VARCHAR(8) NULL,
    hash TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_news_published_ts ON news_articles(published_ts DESC);
CREATE INDEX IF NOT EXISTS idx_news_symbol_published_ts ON news_articles(symbol, published_ts DESC);
"""

DDL_SENTIMENT_TICKS = """
CREATE TABLE IF NOT EXISTS sentiment_ticks (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    ts BIGINT NOT NULL,
    provider TEXT NULL,
    count INT NULL,
    score_raw DOUBLE PRECISION NULL,
    score_norm DOUBLE PRECISION NULL,
    score_ema_5m DOUBLE PRECISION NULL,
    score_ema_15m DOUBLE PRECISION NULL,
    meta JSONB NULL
);
CREATE INDEX IF NOT EXISTS ix_sentiment_symbol_ts ON sentiment_ticks(symbol, ts);
CREATE INDEX IF NOT EXISTS ix_sentiment_ts ON sentiment_ticks(ts);
"""

# DEPRECATED (2025-10-04): kline_raw is no longer used by the application.
# The canonical table is ohlcv_candles. Creation has been disabled here to
# avoid perpetuating unused schema. A separate Alembic migration drops the
# legacy table if it exists. Keep this as an empty string so ensure_all skips it.
DDL_KLINE_RAW = """
"""

DDL_FEATURE_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS feature_snapshot (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_time BIGINT NOT NULL,
    close_time BIGINT NOT NULL,
    ret_1 DOUBLE PRECISION NULL,
    ret_5 DOUBLE PRECISION NULL,
    ret_10 DOUBLE PRECISION NULL,
    rsi_14 DOUBLE PRECISION NULL,
    rolling_vol_20 DOUBLE PRECISION NULL,
    ma_20 DOUBLE PRECISION NULL,
    ma_50 DOUBLE PRECISION NULL,
    PRIMARY KEY (symbol, interval, open_time)
);
CREATE INDEX IF NOT EXISTS idx_feature_symbol_interval_time ON feature_snapshot(symbol, interval, open_time DESC);
"""

DDL_INFERENCE_LOG = """
CREATE TABLE IF NOT EXISTS model_inference_log (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_type TEXT NULL,
    probability DOUBLE PRECISION NOT NULL,
    decision INT NOT NULL,
    threshold DOUBLE PRECISION NOT NULL,
    production BOOLEAN NOT NULL DEFAULT false,
    realized INT NULL,
    resolved_at TIMESTAMPTZ NULL,
    extra JSONB NULL
);
CREATE INDEX IF NOT EXISTS idx_infer_created ON model_inference_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_infer_symbol_interval_created ON model_inference_log(symbol, interval, created_at DESC);
"""

DDL_TRAINING_JOBS = """
CREATE TABLE IF NOT EXISTS training_jobs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ NULL,
    status TEXT NOT NULL,
    trigger TEXT NOT NULL,
    drift_feature TEXT NULL,
    drift_z DOUBLE PRECISION NULL,
    artifact_path TEXT NULL,
    model_id INT NULL,
    version TEXT NULL,
    metrics JSONB NULL,
    error TEXT NULL,
    duration_seconds DOUBLE PRECISION NULL
);
CREATE INDEX IF NOT EXISTS idx_training_jobs_created ON training_jobs(created_at DESC);
"""

DDL_MODEL_REGISTRY = """
CREATE TABLE IF NOT EXISTS model_registry (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    promoted_at TIMESTAMPTZ NULL,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    model_type TEXT NOT NULL,
    status TEXT NOT NULL,
    artifact_path TEXT NULL,
    metrics JSONB NULL,
    UNIQUE(name, version, model_type)
);
CREATE INDEX IF NOT EXISTS idx_model_registry_name_type_created ON model_registry(name, model_type, created_at DESC);
"""

DDL_MODEL_METRICS_HISTORY = """
CREATE TABLE IF NOT EXISTS model_metrics_history (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model_id BIGINT NOT NULL,
    metrics JSONB NULL
);
CREATE INDEX IF NOT EXISTS idx_model_metrics_history_model ON model_metrics_history(model_id, created_at DESC);
"""

DDL_MODEL_LINEAGE = """
CREATE TABLE IF NOT EXISTS model_lineage (
    parent_id BIGINT NOT NULL,
    child_id BIGINT NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);
"""

DDL_RETRAIN_EVENTS = """
CREATE TABLE IF NOT EXISTS retrain_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    job_id BIGINT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    drift_feature TEXT NULL,
    drift_z DOUBLE PRECISION NULL,
    model_id BIGINT NULL,
    version TEXT NULL,
    artifact_path TEXT NULL,
    samples INT NULL,
    error TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_retrain_events_created ON retrain_events(created_at DESC);
"""

DDL_PROMOTION_EVENTS = """
CREATE TABLE IF NOT EXISTS promotion_events (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model_id BIGINT NULL,
    previous_production_model_id BIGINT NULL,
    decision TEXT NOT NULL,
    reason TEXT NULL,
    samples_old INT NULL,
    samples_new INT NULL
);
CREATE INDEX IF NOT EXISTS idx_promotion_events_created ON promotion_events(created_at DESC);
"""

DDL_RISK_STATE = """
CREATE TABLE IF NOT EXISTS risk_state (
    session_key TEXT PRIMARY KEY,
    starting_equity DOUBLE PRECISION NOT NULL,
    peak_equity DOUBLE PRECISION NOT NULL,
    current_equity DOUBLE PRECISION NOT NULL,
    cumulative_pnl DOUBLE PRECISION NOT NULL,
    last_reset_ts BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

DDL_RISK_POSITIONS = """
CREATE TABLE IF NOT EXISTS risk_positions (
    session_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    size DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_key, symbol)
);
"""

DDL_GAP_SEGMENTS = """
CREATE TABLE IF NOT EXISTS gap_segments (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    from_open_time BIGINT NOT NULL,
    to_open_time BIGINT NOT NULL,
    missing_bars INT NOT NULL,
    remaining_bars INT NOT NULL,
    recovered_bars INT NOT NULL DEFAULT 0,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recovered_at TIMESTAMPTZ NULL,
    status TEXT NOT NULL,
    UNIQUE(symbol, interval, from_open_time)
);
CREATE INDEX IF NOT EXISTS idx_gap_segments_symbol_interval ON gap_segments(symbol, interval, detected_at DESC);
"""

DDL_BACKFILL_RUNS = """
CREATE TABLE IF NOT EXISTS ohlcv_backfill_runs (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ NULL,
    finished_at TIMESTAMPTZ NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    from_open_time BIGINT NOT NULL,
    to_open_time BIGINT NOT NULL,
    expected_bars BIGINT NOT NULL,
    loaded_bars BIGINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_backfill_runs_symbol_interval_created ON ohlcv_backfill_runs(symbol, interval, created_at DESC);
"""

# Fallback canonical OHLCV table (if alembic migration not yet applied). This is a safety net only;
# the real definition (with updated_at, ingestion_source, hypertable conversion) lives in Alembic.
DDL_OHLCV_CANDLES_FALLBACK = """
CREATE TABLE IF NOT EXISTS ohlcv_candles (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_time BIGINT NOT NULL,
    close_time BIGINT NOT NULL,
    open NUMERIC(18,8) NOT NULL,
    high NUMERIC(18,8) NOT NULL,
    low NUMERIC(18,8) NOT NULL,
    close NUMERIC(18,8) NOT NULL,
    volume NUMERIC(28,10) NULL,
    trade_count BIGINT NULL,
    taker_buy_volume NUMERIC(28,10) NULL,
    taker_buy_quote_volume NUMERIC(28,10) NULL,
    is_closed BOOLEAN NOT NULL DEFAULT false,
    ingestion_source TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, interval, open_time)
);
CREATE INDEX IF NOT EXISTS ix_ohlcv_symbol_interval_time_desc ON ohlcv_candles(symbol, interval, open_time);
CREATE INDEX IF NOT EXISTS ix_ohlcv_symbol_interval_closed_time_desc ON ohlcv_candles(symbol, interval, is_closed, open_time);
"""

DDL_TRADING_SIGNALS = """
CREATE TABLE IF NOT EXISTS trading_signals (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal_type TEXT NOT NULL,
    status TEXT NOT NULL,
    params JSONB NOT NULL,
    price DOUBLE PRECISION NULL,
    extra JSONB NULL,
    executed_at TIMESTAMPTZ NULL,
    order_side TEXT NULL,
    order_size DOUBLE PRECISION NULL,
    order_price DOUBLE PRECISION NULL,
    error TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_trading_signals_created ON trading_signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trading_signals_type_created ON trading_signals(signal_type, created_at DESC);
"""

ALL_DDLS = [
    DDL_NEWS,
    DDL_SENTIMENT_TICKS,
    DDL_KLINE_RAW,
    DDL_FEATURE_SNAPSHOT,
    DDL_INFERENCE_LOG,
    DDL_TRAINING_JOBS,
    DDL_MODEL_REGISTRY,
    DDL_MODEL_METRICS_HISTORY,
    DDL_MODEL_LINEAGE,
    DDL_RETRAIN_EVENTS,
    DDL_PROMOTION_EVENTS,
    DDL_RISK_STATE,
    DDL_RISK_POSITIONS,
    DDL_GAP_SEGMENTS,
    DDL_BACKFILL_RUNS,
    DDL_OHLCV_CANDLES_FALLBACK,
    DDL_TRADING_SIGNALS,
]

REQUIRED_COLUMN_DEFS: dict[str, dict[str, str]] = {
    # Tables that our queries expect to have created_at even if an older manual table lacked it.
    "model_inference_log": {"created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()"},
    "training_jobs": {"created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()"},
    "model_registry": {"created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()"},
    "model_metrics_history": {"created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()"},
    "retrain_events": {"created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()"},
    "promotion_events": {"created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()"},
}

async def _ensure_columns(conn: asyncpg.Connection, table: str, columns: dict[str,str]):  # type: ignore
    for col, ddl in columns.items():
        exists = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name=$1 AND column_name=$2", table, col
        )
        if not exists:
            await conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}')
            # Backfill NULLs if NOT NULL default just added automatically done by PG for new rows; existing rows get default.

def _extract_table_name(create_stmt: str) -> str:
    tokens = create_stmt.replace('\n', ' ').split()
    name = "unknown"
    if "TABLE" in [t.upper() for t in tokens]:
        # find index of TABLE (case-insensitive)
        for i,t in enumerate(tokens):
            if t.upper() == "TABLE":
                j = i + 1
                # skip IF NOT EXISTS tokens
                while j < len(tokens) and tokens[j].upper() in {"IF","NOT","EXISTS"}:
                    j += 1
                if j < len(tokens):
                    name = tokens[j].strip('"')
                break
    # strip trailing '(' if stuck
    if name.endswith('('):
        name = name[:-1]
    return name

async def ensure_all() -> list[str]:
    pool = await init_pool()
    executed: list[str] = []
    if pool is not None:
        async with pool.acquire() as conn:  # type: ignore
            for ddl in ALL_DDLS:
                stmts = [s.strip() for s in ddl.strip().split(';') if s.strip()]
                if not stmts:
                    continue
                create_stmt = stmts[0]
                await conn.execute(create_stmt)
                table = _extract_table_name(create_stmt)
                # Ensure ALL columns from DDL exist (handles previously stubbed/empty tables)
                await _ensure_all_columns_from_create(conn, table, create_stmt)
                if table in REQUIRED_COLUMN_DEFS:
                    await _ensure_columns(conn, table, REQUIRED_COLUMN_DEFS[table])
                for s in stmts[1:]:
                    try:
                        await conn.execute(s)
                    except Exception as e:  # noqa: BLE001
                        msg = str(e).lower()
                        if table in REQUIRED_COLUMN_DEFS and 'created_at' in msg and 'does not exist' in msg:
                            await _ensure_columns(conn, table, REQUIRED_COLUMN_DEFS[table])
                            await conn.execute(s)
                        else:
                            raise
                executed.append(table)
        return executed
    # Fallback: direct one-off connection (used by reset scripts before pool ready)
    host = os.getenv('POSTGRES_HOST', '127.0.0.1')
    _v = os.getenv('POSTGRES_PORT', '5432')
    if isinstance(_v, str) and '#' in _v:
        _v = _v.split('#', 1)[0].strip()
    port = int(float(_v))
    user = os.getenv('POSTGRES_USER', 'postgres')
    password = os.getenv('POSTGRES_PASSWORD', 'traderpass')
    database = os.getenv('POSTGRES_DB', 'mydata')
    conn = await asyncpg.connect(host=host, port=port, user=user, password=password, database=database)  # type: ignore
    try:
        for ddl in ALL_DDLS:
            stmts = [s.strip() for s in ddl.strip().split(';') if s.strip()]
            if not stmts:
                continue
            create_stmt = stmts[0]
            await conn.execute(create_stmt)
            table = _extract_table_name(create_stmt)
            await _ensure_all_columns_from_create(conn, table, create_stmt)
            if table in REQUIRED_COLUMN_DEFS:
                await _ensure_columns(conn, table, REQUIRED_COLUMN_DEFS[table])
            for s in stmts[1:]:
                if not s:
                    continue
                try:
                    await conn.execute(s)
                except Exception as e:  # noqa: BLE001
                    msg = str(e).lower()
                    if table in REQUIRED_COLUMN_DEFS and 'created_at' in msg and 'does not exist' in msg:
                        await _ensure_columns(conn, table, REQUIRED_COLUMN_DEFS[table])
                        await conn.execute(s)
                    else:
                        raise
            executed.append(table)
    finally:
        await conn.close()
    return executed

__all__ = ["ensure_all"]

# ------------------ Column Backfill Helpers ---------------------------------

async def _ensure_all_columns_from_create(conn: asyncpg.Connection, table: str, create_stmt: str):  # type: ignore
    """Parse the CREATE TABLE statement and add any missing columns.

    This is a safety net for cases where an operator may have created an empty placeholder
    table earlier (e.g., CREATE TABLE xyz ();) or a partial manual schema. We ignore
    constraint-only lines and only attempt simple column additions. If a column already
    exists nothing happens. If a column has a NOT NULL constraint and the table has rows,
    the ALTER may fail; that's acceptable (explicit migration then required). Empty tables
    will succeed.
    """
    # Quick extraction of column block between first '(' after TABLE name and its matching ')'
    try:
        open_paren = create_stmt.index('(')
        close_paren = create_stmt.rindex(')')
    except ValueError:
        return
    block = create_stmt[open_paren + 1:close_paren]
    lines = [l.strip() for l in block.split('\n') if l.strip()]
    ignore_starts = ("PRIMARY ", "UNIQUE", "CONSTRAINT", "FOREIGN", "CHECK", "REFERENCES")
    for line in lines:
        # Remove trailing commas
        if line.endswith(','):
            line = line[:-1].strip()
        if not line or any(line.upper().startswith(k) for k in ignore_starts):
            continue
        # Split into name + rest
        parts = line.split()
        if not parts:
            continue
        col = parts[0].strip('"')
        # Very naive type/constraint reconstruction: remainder of line as-is
        col_def = ' '.join(parts[1:])
        if not col_def:
            continue
        exists = await conn.fetchval(
            "SELECT 1 FROM information_schema.columns WHERE table_name=$1 AND column_name=$2",
            table, col
        )
        if exists:
            continue
        # Attempt to add column
        try:
            await conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {col_def}')
        except Exception:
            # Silently ignore failures; explicit migrations should handle complex cases
            pass
