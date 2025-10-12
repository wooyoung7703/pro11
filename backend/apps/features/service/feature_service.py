from __future__ import annotations
import asyncpg
import asyncio
import time
from typing import Dict, Any
from backend.common.db.connection import init_pool
from prometheus_client import Counter, Histogram, Gauge
from .feature_calculators import compute_all
from backend.apps.sentiment.repository import fetch_range as sentiment_fetch_range
from backend.apps.sentiment.service import aggregate_with_windows
from prometheus_client import Counter

# Metrics: Sentiment join quality
FEATURE_SENT_JOIN_ATTEMPTS_TOTAL = Counter(
    "feature_sentiment_join_attempts_total",
    "Number of feature snapshot computations where we attempted to join sentiment",
)
FEATURE_SENT_JOIN_MISSING_TOTAL = Counter(
    "feature_sentiment_join_missing_total",
    "Number of feature snapshot computations where sentiment was missing or produced no usable value",
)
import os

# Module-level schema init guard
_FEATURE_SCHEMA_READY: bool = False
_FEATURE_SCHEMA_LOCK = asyncio.Lock()

# Source for real-time compute now aligns with canonical ohlcv_candles
FETCH_SQL = """
SELECT open_time, close_time, close::float
FROM ohlcv_candles
WHERE symbol = $1 AND interval = $2 AND is_closed = true
ORDER BY open_time DESC
LIMIT $3
"""

# New long-format schema SQLs (Option A)
META_UPSERT_SQL = """
INSERT INTO feature_snapshot_meta (symbol, interval, open_time, close_time)
VALUES ($1, $2, $3, $4)
ON CONFLICT (symbol, interval, open_time)
DO UPDATE SET close_time = EXCLUDED.close_time
RETURNING id
"""

VALUE_UPSERT_SQL = """
INSERT INTO feature_snapshot_value (snapshot_id, feature_name, feature_value)
VALUES ($1, $2, $3)
ON CONFLICT (snapshot_id, feature_name)
DO UPDATE SET feature_value = EXCLUDED.feature_value
"""

# Backfill source (canonical ohlcv_candles)
CLOSES_FETCH_BACKFILL_SQL = """
SELECT open_time, close_time, close::float
FROM ohlcv_candles
WHERE symbol = $1 AND interval = $2 AND is_closed = true
ORDER BY open_time ASC
LIMIT $3
"""

RECENT_META_SQL = """
SELECT id, symbol, interval, open_time, close_time
FROM feature_snapshot_meta
WHERE symbol = $1 AND interval = $2
ORDER BY open_time DESC
LIMIT $3
"""

RECENT_VALUES_BY_IDS_SQL = """
SELECT snapshot_id, feature_name, feature_value
FROM feature_snapshot_value
WHERE snapshot_id = ANY($1::bigint[])
"""

FEATURE_COLUMNS = [
    "symbol","interval","open_time","close_time",
    "ret_1","ret_5","ret_10","ret_15","rsi_14","rolling_vol_20","ma_20","ma_50"
]

class FeatureService:
    def __init__(self, symbol: str, interval: str, window: int = 300):
        self.symbol = symbol
        self.interval = interval
        self.window = window
        self._last_open_time: int | None = None
        self._lock = asyncio.Lock()
        self.last_success_ts: float | None = None
        self.last_error_ts: float | None = None

    async def _ensure_schema(self, conn: asyncpg.Connection) -> None:
        global _FEATURE_SCHEMA_READY
        if _FEATURE_SCHEMA_READY:
            return
        async with _FEATURE_SCHEMA_LOCK:
            if _FEATURE_SCHEMA_READY:
                return
            # Create tables if not exist (idempotent)
            await conn.execute(
                """
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
                CREATE INDEX IF NOT EXISTS idx_feature_backfill_runs_si ON feature_backfill_runs(symbol, interval, started_at DESC);
                """
            )
            _FEATURE_SCHEMA_READY = True

    async def compute_and_store(self) -> Dict[str, Any]:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await self._ensure_schema(conn)
            rows = await conn.fetch(FETCH_SQL, self.symbol, self.interval, self.window)
            if not rows:
                return {"status": "no_data"}
            ordered = list(reversed(rows))  # rows are DESC; reverse for chronological
            prices = [r[2] for r in ordered]
            feats = compute_all(prices)
            last = ordered[-1]
            if self._last_open_time == last[0]:  # Dedup
                return {"status": "unchanged"}
            # Upsert meta row and get snapshot id
            meta_row = await conn.fetchrow(
                META_UPSERT_SQL,
                self.symbol,
                self.interval,
                last[0],
                last[1],
            )
            snapshot_id = meta_row["id"] if meta_row else None
            if snapshot_id is None:
                return {"status": "error", "message": "failed_to_upsert_meta"}

            # --- Sentiment join (no-leak): use sentiment ticks up to close_time ---
            try:
                sent_step_min = int(os.getenv("SENTIMENT_STEP_DEFAULT", "1").rstrip("m"))
                sent_step_ms = sent_step_min * 60 * 1000
                # Lookback window: 60m default for stable EMA_60 computation
                ema_windows = [int(x.strip().rstrip("m")) for x in os.getenv("SENTIMENT_EMA_WINDOWS", "5m,15m,60m").split(",")]
                lookback_min = max(ema_windows + [60])
                end_ms = int(last[1])  # close_time is BIGINT epoch ms in ohlcv_candles
                start_ms = end_ms - lookback_min * 60 * 1000
                sent_rows = await sentiment_fetch_range(self.symbol, start_ms, end_ms, limit=10000)
                sent_points: list[tuple[int, float]] = []
                for r in sent_rows:
                    val = r.get("score_norm")
                    if val is None:
                        val = r.get("score_raw")
                    if val is None:
                        continue
                    sent_points.append((int(r["ts"]), float(val)))
                # Record attempt
                try:
                    FEATURE_SENT_JOIN_ATTEMPTS_TOTAL.inc()
                except Exception:
                    pass
                if sent_points:
                    pos_threshold = float(os.getenv("SENTIMENT_POS_THRESHOLD", "0.0"))
                    agg = aggregate_with_windows(sorted(sent_points), sent_step_ms, ema_windows, pos_threshold)
                    # Extract last aligned to final bucket <= end_ms
                    def last_value(key: str) -> float | None:
                        arr = agg.get(key)
                        if not arr:
                            return None
                        # find last item with ts <= end_ms
                        for t, v in reversed(arr):
                            if t <= end_ms:
                                return float(v) if v is not None else None
                        return None
                    sent_out: dict[str, float | None] = {
                        "sent_score": last_value("score"),
                        "sent_cnt": last_value("count"),
                        "sent_pos_ratio": last_value("pos_ratio"),
                        "sent_d1": last_value("d1"),
                        "sent_d5": last_value("d5"),
                        "sent_vol_30": last_value("vol_30"),
                    }
                    for n in ema_windows:
                        sent_out[f"sent_ema_{n}m"] = last_value(f"ema_{n}")
                    # Determine if missing (all None)
                    all_none = all(v is None for v in sent_out.values())
                    if all_none:
                        try:
                            FEATURE_SENT_JOIN_MISSING_TOTAL.inc()
                        except Exception:
                            pass
                    # Upsert joined sentiment features
                    for name, value in sent_out.items():
                        await conn.execute(
                            VALUE_UPSERT_SQL,
                            snapshot_id,
                            name,
                            None if value is None else float(value),
                        )
                else:
                    # No sentiment points in window
                    try:
                        FEATURE_SENT_JOIN_MISSING_TOTAL.inc()
                    except Exception:
                        pass
            except Exception:
                # Sentiment join is optional; failures should not break OHLCV features
                pass
            # Upsert each feature value
            for name, value in feats.items():
                await conn.execute(
                    VALUE_UPSERT_SQL,
                    snapshot_id,
                    name,
                    None if value is None else float(value),
                )
            feats_out = {k: (None if v is None else float(v)) for k, v in feats.items()}
            feats_out.update({"open_time": last[0], "close_time": last[1]})
            self._last_open_time = last[0]
            return feats_out

    async def fetch_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await self._ensure_schema(conn)
            meta_rows = await conn.fetch(RECENT_META_SQL, self.symbol, self.interval, limit)
            if not meta_rows:
                return []
            ids = [r["id"] for r in meta_rows]
            val_rows = await conn.fetch(RECENT_VALUES_BY_IDS_SQL, ids)
            # Group values by snapshot_id
            by_id: dict[int, dict[str, Any]] = {}
            for vr in val_rows:
                sid = vr["snapshot_id"]
                by_id.setdefault(sid, {})[vr["feature_name"]] = vr["feature_value"]
            # Build wide dicts for compatibility
            result: list[dict[str, Any]] = []
            for mr in meta_rows:
                base = {
                    "symbol": mr["symbol"],
                    "interval": mr["interval"],
                    "open_time": mr["open_time"],
                    "close_time": mr["close_time"],
                }
                vals = by_id.get(mr["id"], {})
                # Cast numeric features to float
                for k, v in vals.items():
                    base[k] = None if v is None else float(v)
                result.append(base)
            return result

    async def recent_as_csv(self, limit: int = 200) -> str:
        rows = await self.fetch_recent(limit)
        import io, csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(FEATURE_COLUMNS)
        for r in rows:
            writer.writerow([r.get(col, "") for col in FEATURE_COLUMNS])
        return output.getvalue()

    async def backfill_snapshots(self, target: int = 600) -> Dict[str, Any]:
        """Compute and insert feature_snapshot rows in bulk for bootstrap.

        Strategy:
          - Fetch ASC ordered closed candles sufficient to compute `target` snapshots,
            considering the internal window size.
          - For each index i >= window-1, compute features on prices[:i+1] and insert a row
            aligned to open_time/close_time of index i.

        Returns summary with inserted count and covered open/close time range
        plus source fetch details (useful for UI/ops visibility).
        """
        if target <= 0:
            return {"status": "noop", "inserted": 0}
        pool = await init_pool()
        async with pool.acquire() as conn:
            await self._ensure_schema(conn)
            # Need at least window + target samples
            need = int(self.window) + int(target)
            rows = await conn.fetch(CLOSES_FETCH_BACKFILL_SQL, self.symbol, self.interval, need)
            if not rows or len(rows) < self.window + 5:  # require some buffer
                return {"status": "insufficient_data", "needed": need, "have": len(rows) if rows else 0}
            # compute chronologically
            prices = [float(r[2]) for r in rows]
            inserted = 0
            first_ot = None
            last_ot = None
            first_ct = None
            last_ct = None
            start_idx = max(0, self.window - 1)
            end_idx = min(len(rows) - 1, start_idx + target - 1)
            # Start run history row
            run_row = await conn.fetchrow(
                """
                INSERT INTO feature_backfill_runs (symbol, interval, requested_target, used_window, status)
                VALUES ($1, $2, $3, $4, 'running') RETURNING id
                """,
                self.symbol,
                self.interval,
                int(target),
                int(self.window),
            )
            run_id = run_row["id"] if run_row else None
            try:
                for i in range(start_idx, end_idx + 1):
                    segment = prices[: i + 1]
                    feats = compute_all(segment)
                    ot = rows[i][0]
                    ct = rows[i][1]
                    meta_row = await conn.fetchrow(
                        META_UPSERT_SQL,
                        self.symbol,
                        self.interval,
                        ot,
                        ct,
                    )
                    snapshot_id = meta_row["id"] if meta_row else None
                    if snapshot_id is None:
                        # If meta upsert fails mid-run, record error and abort
                        if run_id is not None:
                            await conn.execute(
                                "UPDATE feature_backfill_runs SET status='error', error=$2, finished_at=now() WHERE id=$1",
                                run_id,
                                "failed_to_upsert_meta",
                            )
                        return {"status": "error", "message": "failed_to_upsert_meta"}
                    for name, value in feats.items():
                        await conn.execute(
                            VALUE_UPSERT_SQL,
                            snapshot_id,
                            name,
                            None if value is None else float(value),
                        )
                    if first_ot is None:
                        first_ot = ot
                        first_ct = ct
                    last_ot = ot
                    last_ct = ct
                    inserted += 1
            except Exception as e:
                if run_id is not None:
                    await conn.execute(
                        "UPDATE feature_backfill_runs SET status='error', error=$2, finished_at=now() WHERE id=$1",
                        run_id,
                        str(e),
                    )
                raise
            # Finish run history row
            if run_id is not None:
                await conn.execute(
                    """
                    UPDATE feature_backfill_runs SET
                        inserted=$2,
                        from_open_time=$3,
                        to_open_time=$4,
                        from_close_time=$5,
                        to_close_time=$6,
                        source_fetched=$7,
                        start_index=$8,
                        end_index=$9,
                        status='success',
                        finished_at=now()
                    WHERE id=$1
                    """,
                    run_id,
                    inserted,
                    first_ot,
                    last_ot,
                    first_ct,
                    last_ct,
                    len(rows),
                    start_idx,
                    end_idx,
                )
            return {
                "status": "ok",
                "inserted": inserted,
                "covered_bars": inserted,
                "target": target,
                "used_window": self.window,
                "range": {
                    "from_open_time": first_ot,
                    "to_open_time": last_ot,
                    "from_close_time": first_ct,
                    "to_close_time": last_ct,
                },
                "source": {
                    "fetched": len(rows),
                    "from_open_time": rows[0][0] if rows else None,
                    "to_open_time": rows[-1][0] if rows else None,
                    "from_close_time": rows[0][1] if rows else None,
                    "to_close_time": rows[-1][1] if rows else None,
                    "start_index": start_idx,
                    "end_index": end_idx,
                },
            }

    async def compute_drift(self, feature: str = "ret_1", window: int = 200) -> Dict[str, Any]:
        """Compute simple population vs recent segment mean/var z-score drift.
        Fetch 2*window rows; first window = baseline, second window = recent.
        Returns z score and basic stats. If insufficient data or feature missing returns status.
        """
        rows = await self.fetch_recent(limit=2*window)
        if len(rows) < 2*window:
            return {"status": "insufficient_data", "needed": 2*window, "have": len(rows)}
        # rows are DESC currently; reverse to chronological
        chronological = list(reversed(rows))
        baseline = chronological[:window]
        recent = chronological[window:2*window]
        import math
        def extract(vals):
            arr = []
            for v in vals:
                x = v.get(feature)
                if isinstance(x, (int, float)) and x is not None and math.isfinite(x):
                    arr.append(float(x))
            return arr
        base_vals = extract(baseline)
        recent_vals = extract(recent)
        if len(base_vals) < window * 0.8 or len(recent_vals) < window * 0.8:
            return {"status": "insufficient_valid_points"}
        def stats(arr):
            m = sum(arr)/len(arr)
            var = sum((x-m)**2 for x in arr)/len(arr)
            return m, var
        base_mean, base_var = stats(base_vals)
        recent_mean, recent_var = stats(recent_vals)
        # Pooled std (Cohen's d style) across baseline/recent (population-style denominator)
        pooled_var = (base_var + recent_var) / 2.0
        pooled_std = math.sqrt(pooled_var) if pooled_var > 0 else 0.0
        z = (recent_mean - base_mean)/pooled_std if pooled_std > 0 else 0.0
        return {
            "status": "ok",
            "feature": feature,
            "window": window,
            "baseline_mean": base_mean,
            "recent_mean": recent_mean,
            "baseline_var": base_var,
            "recent_var": recent_var,
            "pooled_std": pooled_std,
            "method": "cohens_d",
            "z_score": z,
            "n_baseline": len(base_vals),
            "n_recent": len(recent_vals),
        }

    async def compute_drift_scan(self, features: list[str], window: int = 200) -> Dict[str, Any]:
        """Scan multiple features and return each drift stat plus the max |z| feature summary.
        Reuses underlying compute_drift logic with shared row fetch for efficiency.
        """
        # Single fetch for all features
        rows = await self.fetch_recent(limit=2*window)
        if len(rows) < 2*window:
            return {"status": "insufficient_data", "needed": 2*window, "have": len(rows)}
        chronological = list(reversed(rows))
        baseline = chronological[:window]
        recent = chronological[window:2*window]
        import math
        results: dict[str, Dict[str, Any]] = {}
        best: dict[str, Any] | None = None
        for feature in features:
            def extract(vals):
                out = []
                for v in vals:
                    x = v.get(feature)
                    if isinstance(x, (int, float)) and x is not None and math.isfinite(x):
                        out.append(float(x))
                return out
            base_vals = extract(baseline)
            recent_vals = extract(recent)
            if len(base_vals) < window * 0.8 or len(recent_vals) < window * 0.8:
                results[feature] = {"status": "insufficient_valid_points"}
                continue
            m_base = sum(base_vals)/len(base_vals)
            var_base = sum((x-m_base)**2 for x in base_vals)/len(base_vals)
            m_recent = sum(recent_vals)/len(recent_vals)
            var_recent = sum((x-m_recent)**2 for x in recent_vals)/len(recent_vals)
            pooled_var = (var_base + var_recent) / 2.0
            pooled_std = math.sqrt(pooled_var) if pooled_var > 0 else 0.0
            z = (m_recent - m_base)/pooled_std if pooled_std > 0 else 0.0
            stat = {
                "status": "ok",
                "feature": feature,
                "window": window,
                "baseline_mean": m_base,
                "recent_mean": m_recent,
                "baseline_var": var_base,
                "recent_var": var_recent,
                "pooled_std": pooled_std,
                "method": "cohens_d",
                "z_score": z,
                "n_baseline": len(base_vals),
                "n_recent": len(recent_vals),
            }
            results[feature] = stat
            if best is None or abs(z) > abs(best.get("z_score", 0.0)):
                best = stat
        out: Dict[str, Any] = {"status": "ok", "results": results}
        if best:
            out["max_abs"] = {
                "feature": best["feature"],
                "z_score": best["z_score"],
            }
        return out

FEATURE_SCHED_COUNTER = Counter("feature_scheduler_runs_total", "Feature scheduler run count", ["symbol", "interval"])
FEATURE_SCHED_ERRORS = Counter("feature_scheduler_errors_total", "Feature scheduler errors", ["symbol", "interval"])
FEATURE_SCHED_LATENCY = Histogram("feature_scheduler_latency_seconds", "Feature computation latency", ["symbol", "interval"])
FEATURE_SCHED_SKIPPED = Counter("feature_scheduler_skipped_total", "Scheduler skipped due to overlapping run", ["symbol", "interval"])
FEATURE_SCHED_LAST_SUCCESS = Gauge("feature_scheduler_last_success_timestamp", "Unix ts of last successful feature computation", ["symbol", "interval"])
FEATURE_SCHED_LAST_ERROR = Gauge("feature_scheduler_last_error_timestamp", "Unix ts of last error in feature computation", ["symbol", "interval"])
FEATURE_SCHED_LAG = Gauge("feature_scheduler_lag_seconds", "Seconds since last successful feature computation", ["symbol", "interval"])
 
async def feature_scheduler(service: FeatureService, interval_seconds: int = 60):
    while True:
        start = time.perf_counter()
        if service._lock.locked():
            FEATURE_SCHED_SKIPPED.labels(service.symbol, service.interval).inc()
            await asyncio.sleep(interval_seconds)
            continue
        async with service._lock:
            try:
                FEATURE_SCHED_COUNTER.labels(service.symbol, service.interval).inc()
                await service.compute_and_store()
                FEATURE_SCHED_LATENCY.labels(service.symbol, service.interval).observe(time.perf_counter() - start)
                ts = time.time()
                service.last_success_ts = ts
                FEATURE_SCHED_LAST_SUCCESS.labels(service.symbol, service.interval).set(ts)
                FEATURE_SCHED_LAG.labels(service.symbol, service.interval).set(0.0)
            except Exception:
                FEATURE_SCHED_ERRORS.labels(service.symbol, service.interval).inc()
                ts = time.time()
                service.last_error_ts = ts
                FEATURE_SCHED_LAST_ERROR.labels(service.symbol, service.interval).set(ts)
        # Update lag gauge if last_success exists
        if service.last_success_ts:
            FEATURE_SCHED_LAG.labels(service.symbol, service.interval).set(max(0.0, time.time() - service.last_success_ts))
        await asyncio.sleep(interval_seconds)
