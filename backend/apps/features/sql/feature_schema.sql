-- Feature snapshot schema (Option A: meta/value long) and backfill run history

-- Meta table: one row per symbol/interval/open_time
CREATE TABLE IF NOT EXISTS feature_snapshot_meta (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_time BIGINT NOT NULL,
    close_time BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(symbol, interval, open_time)
);

CREATE INDEX IF NOT EXISTS idx_feature_meta_sio
    ON feature_snapshot_meta(symbol, interval, open_time);

-- Value table: one row per feature per snapshot
CREATE TABLE IF NOT EXISTS feature_snapshot_value (
    snapshot_id BIGINT NOT NULL REFERENCES feature_snapshot_meta(id) ON DELETE CASCADE,
    feature_name TEXT NOT NULL,
    feature_value DOUBLE PRECISION,
    PRIMARY KEY(snapshot_id, feature_name)
);

CREATE INDEX IF NOT EXISTS idx_feature_value_name
    ON feature_snapshot_value(feature_name);

-- Backfill runs history
CREATE TABLE IF NOT EXISTS feature_backfill_runs (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    requested_target INT NOT NULL,
    used_window INT NOT NULL,
    inserted INT NOT NULL DEFAULT 0,
    from_open_time BIGINT,
    to_open_time BIGINT,
    from_close_time BIGINT,
    to_close_time BIGINT,
    source_fetched INT,
    start_index INT,
    end_index INT,
    status TEXT NOT NULL,
    error TEXT,
    started_at TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_feature_backfill_runs_si
    ON feature_backfill_runs(symbol, interval, started_at DESC);
