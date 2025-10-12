-- One-time migration: copy wide feature_snapshot data into long schema
-- Safe to run multiple times due to ON CONFLICT DO NOTHING

-- 1) Ensure target tables exist (mirrors ensure_schema)
CREATE TABLE IF NOT EXISTS feature_snapshot_meta (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_time BIGINT NOT NULL,
    close_time BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(symbol, interval, open_time)
);
CREATE INDEX IF NOT EXISTS idx_feature_meta_sio ON feature_snapshot_meta(symbol, interval, open_time);

CREATE TABLE IF NOT EXISTS feature_snapshot_value (
    snapshot_id BIGINT NOT NULL REFERENCES feature_snapshot_meta(id) ON DELETE CASCADE,
    feature_name TEXT NOT NULL,
    feature_value DOUBLE PRECISION,
    PRIMARY KEY(snapshot_id, feature_name)
);
CREATE INDEX IF NOT EXISTS idx_feature_value_name ON feature_snapshot_value(feature_name);

-- 2) Seed meta from existing wide table
INSERT INTO feature_snapshot_meta(symbol, interval, open_time, close_time)
SELECT s.symbol, s.interval, s.open_time, s.close_time
FROM feature_snapshot s
ON CONFLICT (symbol, interval, open_time) DO NOTHING;

-- 3) Seed values by unpivoting wide columns
INSERT INTO feature_snapshot_value(snapshot_id, feature_name, feature_value)
SELECT m.id, v.feature_name, v.feature_value
FROM feature_snapshot s
JOIN feature_snapshot_meta m
  ON m.symbol = s.symbol AND m.interval = s.interval AND m.open_time = s.open_time
CROSS JOIN LATERAL (VALUES
  ('ret_1', s.ret_1::double precision),
  ('ret_5', s.ret_5::double precision),
  ('ret_10', s.ret_10::double precision),
  ('rsi_14', s.rsi_14::double precision),
  ('rolling_vol_20', s.rolling_vol_20::double precision),
  ('ma_20', s.ma_20::double precision),
  ('ma_50', s.ma_50::double precision)
) AS v(feature_name, feature_value)
ON CONFLICT (snapshot_id, feature_name) DO NOTHING;
