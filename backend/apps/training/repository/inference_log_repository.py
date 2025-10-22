from __future__ import annotations

import contextlib
import inspect
import json
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import asyncpg
from asyncpg import Record  # type: ignore

from backend.common.config.base_config import load_config
from backend.common.db.connection import init_pool


@asynccontextmanager
async def _pooled_conn(pool):
    acquire_ctx = pool.acquire()
    if hasattr(acquire_ctx, "__aenter__"):
        async with acquire_ctx as conn:
            yield conn
        return
    conn = await acquire_ctx
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            release = getattr(pool, "release", None)
            if release is not None:
                res = release(conn)
                if inspect.isawaitable(res):
                    await res

INSERT_SQL = """
INSERT INTO model_inference_log (symbol, interval, model_name, model_version, probability, decision, threshold, production, extra)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
RETURNING id, created_at;
"""

INSERT_SQL_NO_RETURNING = """
INSERT INTO model_inference_log (symbol, interval, model_name, model_version, probability, decision, threshold, production, extra)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
"""

FETCH_RECENT_SQL = """
SELECT id, created_at, symbol, interval, model_name, model_version, probability, decision, threshold, production, realized, resolved_at
FROM model_inference_log
ORDER BY created_at DESC
LIMIT $1 OFFSET $2;
"""

HISTOGRAM_SQL = """
WITH buckets AS (
  SELECT generate_series(0,10) AS b
), stats AS (
  SELECT width_bucket(probability, 0, 1, 10) AS bkt
  FROM model_inference_log
  WHERE created_at >= NOW() - ($1 || ' seconds')::interval
)
SELECT b as bucket, COALESCE(count,0) as count, (b/10.0) as le
FROM buckets
LEFT JOIN (
  SELECT bkt, COUNT(*) AS count FROM stats GROUP BY bkt
) agg ON agg.bkt = buckets.b
ORDER BY b;
"""

class InferenceLogRepository:
    async def _direct_connect(self) -> Optional[asyncpg.Connection]:
        """Fallback direct connection when pool is unavailable.

        This mirrors the fallback used by schema manager to avoid dropping inference logs
        early in startup or during transient pool failures.
        """
        cfg = load_config()
        try:
            return await asyncpg.connect(dsn=cfg.dsn)  # type: ignore
        except Exception:
            try:
                return await asyncpg.connect(  # type: ignore
                    host=cfg.postgres_host,
                    port=cfg.postgres_port,
                    user=cfg.postgres_user,
                    password=cfg.postgres_password,
                    database=cfg.postgres_db,
                )
            except Exception:
                return None
    async def insert(self, symbol: str, interval: str, model_name: str, model_version: str, probability: float, decision: int, threshold: float, production: bool, extra: Optional[Dict[str, Any]] = None) -> Optional[int]:
        pool = await init_pool()
        if pool is None:
            # Try direct connection fallback once
            conn = await self._direct_connect()
            if conn is None:
                import logging; logging.getLogger(__name__).warning("inference_log_repo_pool_unavailable action=insert (fallback_failed)")
                return None
            try:
                payload = json.dumps(extra) if extra is not None else None
                row = await conn.fetchrow(INSERT_SQL, symbol, interval, model_name, model_version, probability, decision, threshold, production, payload)
                return row[0] if row else None
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
        async with _pooled_conn(pool) as conn:  # type: ignore[arg-type]
            payload = json.dumps(extra) if extra is not None else None
            row = await conn.fetchrow(INSERT_SQL, symbol, interval, model_name, model_version, probability, decision, threshold, production, payload)
            return row[0] if row else None

    async def fetch_recent(self, limit: int = 100, offset: int = 0) -> List[asyncpg.Record]:
        pool = await init_pool()
        if pool is None:
            # Fallback direct connection
            conn = await self._direct_connect()
            if conn is None:
                import logging; logging.getLogger(__name__).warning("inference_log_repo_pool_unavailable action=fetch_recent (fallback_failed)")
                return []
            try:
                rows = await conn.fetch(FETCH_RECENT_SQL, limit, offset)
                return list(rows)
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
        async with _pooled_conn(pool) as conn:  # type: ignore[arg-type]
            rows = await conn.fetch(FETCH_RECENT_SQL, limit, offset)
            return list(rows)

    async def probability_histogram(self, window_seconds: int = 3600) -> List[dict]:
        pool = await init_pool()
        ws = str(int(window_seconds))
        if pool is None:
            conn = await self._direct_connect()
            if conn is None:
                import logging; logging.getLogger(__name__).warning("inference_log_repo_pool_unavailable action=probability_histogram (fallback_failed)")
                return []
            try:
                rows = await conn.fetch(HISTOGRAM_SQL, ws)
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
            out = []
            for r in rows:
                out.append({"bucket": r["bucket"], "count": r["count"], "le": r["le"]})
            return out
        async with _pooled_conn(pool) as conn:  # type: ignore[arg-type]
            rows = await conn.fetch(HISTOGRAM_SQL, ws)
        out = []
        for r in rows:
            out.append({"bucket": r["bucket"], "count": r["count"], "le": r["le"]})
        return out
    async def fetch_latest_for(self, symbol: str, interval: str) -> Optional[asyncpg.Record]:
        """Fetch the most recent inference log row for a given symbol and interval.

        Returns asyncpg.Record with at least: id, created_at, probability, decision, threshold, production.
        """
        pool = await init_pool()
        if pool is None:
            conn = await self._direct_connect()
            if conn is None:
                import logging; logging.getLogger(__name__).warning("inference_log_repo_pool_unavailable action=fetch_latest_for symbol=%s interval=%s (fallback_failed)", symbol, interval)
                return None
            try:
                row = await conn.fetchrow(
                    """
                    SELECT id, created_at, symbol, interval, model_name, model_version, probability, decision, threshold, production
                    FROM model_inference_log
                    WHERE UPPER(symbol)=UPPER($1) AND interval=$2
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    symbol,
                    interval,
                )
                return row
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
        async with _pooled_conn(pool) as conn:  # type: ignore[arg-type]
            row = await conn.fetchrow(
                """
                SELECT id, created_at, symbol, interval, model_name, model_version, probability, decision, threshold, production
                FROM model_inference_log
                WHERE UPPER(symbol)=UPPER($1) AND interval=$2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                symbol,
                interval,
            )
            return row
    async def bulk_insert(self, items: List[Dict[str, Any]]):
        if not items:
            return
        # Prepare list of tuples
        records = [
            (
                it["symbol"],
                it["interval"],
                it["model_name"],
                it["model_version"],
                it["probability"],
                it["decision"],
                it["threshold"],
                it["production"],
                json.dumps(it.get("extra")) if it.get("extra") is not None else None,
            )
            for it in items
        ]
        pool = await init_pool()
        if pool is None:
            # Fallback direct connection
            conn = await self._direct_connect()
            if conn is None:
                import logging; logging.getLogger(__name__).warning("inference_log_repo_pool_unavailable action=bulk_insert size=%d (fallback_failed)", len(records))
                return
            try:
                await conn.executemany(INSERT_SQL_NO_RETURNING, records)
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
            return
        async with _pooled_conn(pool) as conn:  # type: ignore[arg-type]
            await conn.executemany(INSERT_SQL_NO_RETURNING, records)

    async def update_realized_batch(self, updates: List[Dict[str, Any]]):
        if not updates:
            return 0
        pool = await init_pool()
        if pool is None:
            conn = await self._direct_connect()
            if conn is None:
                import logging; logging.getLogger(__name__).warning("inference_log_repo_pool_unavailable action=update_realized_batch size=%d (fallback_failed)", len(updates))
                return 0
            try:
                q = "UPDATE model_inference_log SET realized = $1, resolved_at = NOW() WHERE id = $2"
                await conn.executemany(q, [(u["realized"], u["id"]) for u in updates])
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
            return len(updates)
        async with _pooled_conn(pool) as conn:  # type: ignore[arg-type]
            q = "UPDATE model_inference_log SET realized = $1, resolved_at = NOW() WHERE id = $2"
            await conn.executemany(q, [(u["realized"], u["id"]) for u in updates])
        return len(updates)

    async def fetch_window_for_live_calibration(self, window_seconds: int = 3600, symbol: Optional[str] = None, interval: Optional[str] = None, target: Optional[str] = None) -> List[asyncpg.Record]:
        pool = await init_pool()
        if pool is None:
            import logging; logging.getLogger(__name__).warning("inference_log_repo_pool_unavailable action=fetch_window_for_live_calibration")
            return []
        async with _pooled_conn(pool) as conn:  # type: ignore[arg-type]
            ws = str(int(window_seconds))
            # optional target filter helper
            def _extra_target_clause():
                return " AND (extra ->> 'target') = $X" if target else ""
            # We cannot place $X dynamically; build SQL/params per branch
            if symbol and interval and target:
                rows = await conn.fetch(
                    """SELECT id, created_at, probability, decision, realized FROM model_inference_log
                        WHERE created_at >= NOW() - ($1 || ' seconds')::interval
                          AND UPPER(symbol) = UPPER($2) AND interval = $3 AND realized IS NOT NULL
                          AND COALESCE((extra ->> 'target'), 'bottom') = $4""",
                    ws, symbol, interval, target,
                )
            elif symbol and interval and not target:
                rows = await conn.fetch(
                    """SELECT id, created_at, probability, decision, realized FROM model_inference_log
                        WHERE created_at >= NOW() - ($1 || ' seconds')::interval
                          AND UPPER(symbol) = UPPER($2) AND interval = $3 AND realized IS NOT NULL""",
                    ws, symbol, interval,
                )
            elif symbol and not interval and target:
                rows = await conn.fetch(
                    """SELECT id, created_at, probability, decision, realized FROM model_inference_log
                        WHERE created_at >= NOW() - ($1 || ' seconds')::interval
                          AND UPPER(symbol) = UPPER($2) AND realized IS NOT NULL
                          AND COALESCE((extra ->> 'target'), 'bottom') = $3""",
                    ws, symbol, target,
                )
            elif symbol and not interval and not target:
                rows = await conn.fetch(
                    """SELECT id, created_at, probability, decision, realized FROM model_inference_log
                        WHERE created_at >= NOW() - ($1 || ' seconds')::interval
                          AND UPPER(symbol) = UPPER($2) AND realized IS NOT NULL""",
                    ws, symbol,
                )
            elif interval and not symbol and target:
                rows = await conn.fetch(
                    """SELECT id, created_at, probability, decision, realized FROM model_inference_log
                        WHERE created_at >= NOW() - ($1 || ' seconds')::interval
                          AND interval = $2 AND realized IS NOT NULL
                          AND COALESCE((extra ->> 'target'), 'bottom') = $3""",
                    ws, interval, target,
                )
            elif interval and not symbol and not target:
                rows = await conn.fetch(
                    """SELECT id, created_at, probability, decision, realized FROM model_inference_log
                        WHERE created_at >= NOW() - ($1 || ' seconds')::interval
                          AND interval = $2 AND realized IS NOT NULL""",
                    ws, interval,
                )
            elif target and not symbol and not interval:
                rows = await conn.fetch(
                    """SELECT id, created_at, probability, decision, realized FROM model_inference_log
                        WHERE created_at >= NOW() - ($1 || ' seconds')::interval
                          AND realized IS NOT NULL AND COALESCE((extra ->> 'target'), 'bottom') = $2""",
                    ws, target,
                )
            else:
                rows = await conn.fetch(
                    """SELECT id, created_at, probability, decision, realized FROM model_inference_log
                        WHERE created_at >= NOW() - ($1 || ' seconds')::interval AND realized IS NOT NULL""",
                    ws,
                )
            return list(rows)

    async def fetch_unlabeled_candidates(self, min_age_seconds: int = 60, limit: int = 500) -> List[asyncpg.Record]:
        """Return unlabeled inference logs older than min_age_seconds ordered oldest first.

        Columns returned include: id, created_at, probability, decision, symbol, interval, extra.
        We exclude already realized rows; caller will compute realized labels and then call update_realized_batch.
        """
        pool = await init_pool()
        s = str(int(min_age_seconds))
        if pool is None:
            conn = await self._direct_connect()
            if conn is None:
                import logging; logging.getLogger(__name__).warning("inference_log_repo_pool_unavailable action=fetch_unlabeled_candidates (fallback_failed)")
                return []
            try:
                rows = await conn.fetch(
                    """SELECT id, created_at, probability, decision, symbol, interval, extra
                        FROM model_inference_log
                        WHERE realized IS NULL AND created_at <= NOW() - ($1 || ' seconds')::interval
                        ORDER BY created_at ASC
                        LIMIT $2""",
                    s,
                    limit,
                )
                return list(rows)
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
        async with _pooled_conn(pool) as conn:  # type: ignore[arg-type]
            rows = await conn.fetch(
                """SELECT id, created_at, probability, decision, symbol, interval, extra
                    FROM model_inference_log
                    WHERE realized IS NULL AND created_at <= NOW() - ($1 || ' seconds')::interval
                    ORDER BY created_at ASC
                    LIMIT $2""",
                s,
                limit,
            )
            return list(rows)

__all__ = ["InferenceLogRepository"]
