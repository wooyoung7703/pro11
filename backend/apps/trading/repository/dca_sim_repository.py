from __future__ import annotations
from typing import Any, Dict, List, Optional
from backend.common.db.connection import init_pool

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS dca_simulations (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    params JSONB NOT NULL,
    trades JSONB NOT NULL,
    summary JSONB NULL,
    first_open_time BIGINT NOT NULL,
    last_open_time BIGINT NOT NULL,
    label TEXT NULL
);
"""

INSERT_SQL = (
    "INSERT INTO dca_simulations(symbol, interval, params, trades, summary, first_open_time, last_open_time, label) "
    "VALUES ($1,$2,$3::jsonb,$4::jsonb,$5::jsonb,$6,$7,$8) RETURNING id"
)

FETCH_ONE_SQL = (
    "SELECT id, extract(epoch from created_at) as created_ts, symbol, interval, params, trades, summary, first_open_time, last_open_time, label "
    "FROM dca_simulations WHERE id=$1"
)

LIST_SQL = (
    "SELECT id, extract(epoch from created_at) as created_ts, symbol, interval, params, trades, summary, first_open_time, last_open_time, label "
    "FROM dca_simulations WHERE ($1::text IS NULL OR symbol=$1) AND ($2::text IS NULL OR interval=$2) "
    "ORDER BY id DESC LIMIT $3"
)

class DcaSimRepository:
    async def _ensure(self, conn) -> None:
        await conn.execute(CREATE_SQL)

    async def insert(self, *, symbol: str, interval: str, params: Dict[str, Any], trades: List[Dict[str, Any]], summary: Optional[Dict[str, Any]], first_open_time: int, last_open_time: int, label: Optional[str] = None) -> Optional[int]:
        pool = await init_pool()
        if pool is None:
            import logging
            logging.getLogger(__name__).warning("dca_sim_repo_pool_unavailable action=insert")
            return None
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            row = await conn.fetchrow(
                INSERT_SQL,
                symbol,
                interval,
                params,
                trades,
                summary,
                int(first_open_time),
                int(last_open_time),
                label,
            )
            return int(row[0]) if row else None

    async def get(self, sim_id: int) -> Optional[Dict[str, Any]]:
        pool = await init_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            row = await conn.fetchrow(FETCH_ONE_SQL, int(sim_id))
            return dict(row) if row else None

    async def list(self, *, symbol: Optional[str] = None, interval: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        pool = await init_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            rows = await conn.fetch(LIST_SQL, symbol, interval, int(max(1, min(limit, 500))))
            return [dict(r) for r in rows]

__all__ = ["DcaSimRepository"]
