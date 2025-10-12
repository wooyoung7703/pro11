from __future__ import annotations
import json
from typing import Any, Iterable, Optional
import asyncpg

from backend.common.db.connection import init_pool
from prometheus_client import Counter

# Metrics
SENTIMENT_DEDUP_UPSERT_TOTAL = Counter(
    "sentiment_dedup_upsert_total",
    "Number of sentiment ticks that were deduplicated via upsert (existing row updated)",
)


SENTIMENT_INSERT_SQL = """
INSERT INTO sentiment_ticks
    (symbol, ts, provider, count, score_raw, score_norm, meta)
VALUES ($1, $2, $3, $4, $5, $6, $7)
RETURNING id
"""

SENTIMENT_SELECT_EXISTING_SQL = """
SELECT id FROM sentiment_ticks
WHERE symbol=$1 AND ts=$2 AND COALESCE(provider,'')=COALESCE($3,'')
"""

SENTIMENT_UPDATE_SQL = """
UPDATE sentiment_ticks
SET count=$1, score_raw=$2, score_norm=$3, meta=$4
WHERE id=$5
RETURNING id
"""


async def get_pool() -> asyncpg.Pool:
    pool = await init_pool()
    if pool is None:
        raise RuntimeError("Database pool is not initialized")
    return pool


async def insert_tick(
    symbol: str,
    ts_ms: int,
    provider: Optional[str],
    count: Optional[int],
    score_raw: Optional[float],
    score_norm: Optional[float],
    meta: Optional[dict[str, Any]] = None,
    upsert: bool = True,
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if upsert:
                existing = await conn.fetchrow(SENTIMENT_SELECT_EXISTING_SQL, symbol, ts_ms, provider)
                if existing:
                    row = await conn.fetchrow(
                        SENTIMENT_UPDATE_SQL,
                        count, score_raw, score_norm, json.dumps(meta or {}), int(existing[0])
                    )
                    try:
                        SENTIMENT_DEDUP_UPSERT_TOTAL.inc()
                    except Exception:
                        pass
                    return int(row[0])
            row = await conn.fetchrow(
                SENTIMENT_INSERT_SQL,
                symbol, ts_ms, provider, count, score_raw, score_norm, json.dumps(meta or {})
            )
            return int(row[0])


async def fetch_latest(symbol: str, limit: int = 1) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, symbol, ts, provider, count, score_raw, score_norm, meta
            FROM sentiment_ticks
            WHERE symbol=$1
            ORDER BY ts DESC
            LIMIT $2
            """,
            symbol, max(1, min(1000, int(limit))),
        )
    return [dict(r) for r in rows]


async def fetch_range(
    symbol: str,
    start_ts_ms: int,
    end_ts_ms: int,
    limit: int = 10000,
) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, symbol, ts, provider, count, score_raw, score_norm, meta
            FROM sentiment_ticks
            WHERE symbol=$1 AND ts BETWEEN $2 AND $3
            ORDER BY ts ASC
            LIMIT $4
            """,
            symbol, start_ts_ms, end_ts_ms, max(1, min(20000, int(limit))),
        )
    return [dict(r) for r in rows]
