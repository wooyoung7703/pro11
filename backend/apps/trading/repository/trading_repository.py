from __future__ import annotations
from typing import Any, Dict, List, Optional
from backend.common.db.connection import init_pool

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS trading_orders (
  id SERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  size DOUBLE PRECISION NOT NULL,
  price DOUBLE PRECISION NOT NULL,
  status TEXT NOT NULL,
  created_ts TIMESTAMPTZ NOT NULL,
  filled_ts TIMESTAMPTZ NULL,
  reason TEXT NULL
);
"""

INSERT_SQL = (
    "INSERT INTO trading_orders (symbol, side, size, price, status, created_ts, filled_ts, reason)"
    " VALUES ($1,$2,$3,$4,$5, to_timestamp($6), to_timestamp($7), $8) RETURNING id"
)

FETCH_RECENT_SQL = (
    "SELECT id, symbol, side, size, price, status, extract(epoch from created_ts) as created_ts,"
    " extract(epoch from filled_ts) as filled_ts, reason"
    " FROM trading_orders ORDER BY id DESC LIMIT $1"
)

MAX_ID_SQL = "SELECT COALESCE(MAX(id), 0) as max_id FROM trading_orders"

DELETE_SQL = "DELETE FROM trading_orders WHERE id = $1 RETURNING id"

DELETE_ALL_SQL = "DELETE FROM trading_orders WHERE ($1::text IS NULL OR symbol = $1)"


class TradingRepository:
    async def _ensure(self, conn) -> None:
        await conn.execute(CREATE_SQL)

    async def insert_order(
        self,
        *,
        symbol: str,
        side: str,
        size: float,
        price: float,
        status: str,
        created_ts: float,
        filled_ts: Optional[float],
        reason: Optional[str],
    ) -> Optional[int]:
        pool = await init_pool()
        if pool is None:
            import logging
            logging.getLogger(__name__).warning("trading_repo_pool_unavailable action=insert_order")
            return None
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            row = await conn.fetchrow(
                INSERT_SQL,
                symbol,
                side,
                float(size),
                float(price),
                status,
                float(created_ts),
                float(filled_ts) if filled_ts is not None else None,
                reason,
            )
            return int(row[0]) if row else None

    async def fetch_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        pool = await init_pool()
        if pool is None:
            import logging
            logging.getLogger(__name__).warning("trading_repo_pool_unavailable action=fetch_recent")
            return []
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            rows = await conn.fetch(FETCH_RECENT_SQL, int(max(1, min(limit, 1000))))
            return [dict(r) for r in rows]

    async def fetch_range(
        self,
        symbol: str,
        *,
        from_ts: Optional[float] = None,
        to_ts: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        pool = await init_pool()
        if pool is None:
            import logging
            logging.getLogger(__name__).warning("trading_repo_pool_unavailable action=fetch_range")
            return []
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            where_clauses = ["symbol = $1", "status = 'filled'"]
            params: List[Any] = [symbol.upper()]
            if from_ts is not None:
                where_clauses.append("filled_ts >= to_timestamp($%d)" % (len(params) + 1))
                params.append(float(from_ts))
            if to_ts is not None:
                where_clauses.append("filled_ts <= to_timestamp($%d)" % (len(params) + 1))
                params.append(float(to_ts))
            sql = (
                "SELECT id, symbol, side, size::float AS size, price::float AS price, status, "
                "extract(epoch from created_ts) AS created_ts, "
                "extract(epoch from filled_ts) AS filled_ts, reason "
                "FROM trading_orders WHERE " + " AND ".join(where_clauses) + " ORDER BY id ASC"
            )
            if limit is not None and int(limit) > 0:
                sql += f" LIMIT {int(limit)}"
            rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]

    async def get_max_id(self) -> int:
        pool = await init_pool()
        if pool is None:
            return 0
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            row = await conn.fetchrow(MAX_ID_SQL)
            try:
                return int(row[0]) if row and row[0] is not None else 0
            except Exception:
                return 0

    async def delete_order(self, order_id: int) -> bool:
        """Hard delete an order by id. Returns True if a row was deleted."""
        pool = await init_pool()
        if pool is None:
            import logging
            logging.getLogger(__name__).warning("trading_repo_pool_unavailable action=delete_order")
            return False
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            row = await conn.fetchrow(DELETE_SQL, int(order_id))
            return bool(row)

    async def delete_all(self, symbol: Optional[str] = None) -> int:
        """Delete all orders, optionally filtered by symbol. Returns number of rows affected (best effort)."""
        pool = await init_pool()
        if pool is None:
            import logging
            logging.getLogger(__name__).warning("trading_repo_pool_unavailable action=delete_all")
            return 0
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            # asyncpg execute returns status like 'DELETE <n>'
            status = await conn.execute(DELETE_ALL_SQL, symbol)
            try:
                return int(status.split()[-1])
            except Exception:
                return 0
