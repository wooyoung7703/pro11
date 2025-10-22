from __future__ import annotations
import json
from typing import Any, Dict, Optional
from backend.common.db.connection import init_pool

INSERT_SIGNAL_SQL = """
INSERT INTO trading_signals(signal_type, status, params, price, extra)
VALUES ($1, $2, $3::jsonb, $4, $5::jsonb)
RETURNING id, created_at;
"""

UPDATE_SIGNAL_SQL = """
UPDATE trading_signals
SET status=$2,
    executed_at=NOW(),
    order_side=$3,
    order_size=$4,
    order_price=$5,
    error=$6,
    extra=COALESCE(extra, '{}'::jsonb) || COALESCE($7::jsonb, '{}'::jsonb)
WHERE id=$1;
"""

FETCH_RECENT_SQL = """
SELECT id, created_at, signal_type, status, params, price, extra, executed_at,
       order_side, order_size, order_price, error
FROM trading_signals
WHERE ($2::text IS NULL OR signal_type = $2)
ORDER BY created_at DESC
LIMIT $1;
"""

DELETE_SIGNALS_SQL = """
DELETE FROM trading_signals
WHERE ($1::text IS NULL OR signal_type = $1);
"""

DELETE_OLDER_THAN_SQL = """
DELETE FROM trading_signals
WHERE created_at < to_timestamp($1)
    AND ($2::text IS NULL OR signal_type = $2);
"""


class TradingSignalRepository:
    async def insert_signal(
        self,
        *,
        signal_type: str,
        status: str,
        params: Dict[str, Any],
        price: Optional[float],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        pool = await init_pool()
        if pool is None:
            raise RuntimeError("db_pool_unavailable")
        payload = json.dumps(params, allow_nan=False)
        extra_payload = json.dumps(extra, allow_nan=False) if extra is not None else None
        async with pool.acquire() as conn:  # type: ignore
            row = await conn.fetchrow(INSERT_SIGNAL_SQL, signal_type, status, payload, price, extra_payload)
            return {"id": int(row[0]), "created_at": row[1]} if row else {"id": None, "created_at": None}

    async def mark_result(
        self,
        *,
        signal_id: int,
        status: str,
        order_side: Optional[str],
        order_size: Optional[float],
        order_price: Optional[float],
        error: Optional[str],
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        pool = await init_pool()
        if pool is None:
            raise RuntimeError("db_pool_unavailable")
        extra_payload = json.dumps(extra, allow_nan=False) if extra is not None else None
        async with pool.acquire() as conn:  # type: ignore
            await conn.execute(
                UPDATE_SIGNAL_SQL,
                int(signal_id),
                status,
                order_side,
                order_size,
                order_price,
                error,
                extra_payload,
            )

    async def fetch_recent(self, limit: int = 50, signal_type: Optional[str] = None) -> list[Dict[str, Any]]:
        pool = await init_pool()
        if pool is None:
            raise RuntimeError("db_pool_unavailable")
        async with pool.acquire() as conn:  # type: ignore
            rows = await conn.fetch(FETCH_RECENT_SQL, int(limit), signal_type)
            out: list[Dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                for key in ("params", "extra"):
                    val = d.get(key)
                    if isinstance(val, str):
                        try:
                            d[key] = json.loads(val)
                        except Exception:
                            pass
                out.append(d)
            return out

    async def delete_all(self, signal_type: Optional[str] = None) -> int:
        pool = await init_pool()
        if pool is None:
            raise RuntimeError("db_pool_unavailable")
        async with pool.acquire() as conn:  # type: ignore
            result = await conn.execute(DELETE_SIGNALS_SQL, signal_type)
        try:
            # asyncpg returns strings like "DELETE 5"
            return int(str(result).split(" ")[-1])
        except Exception:
            return 0

    async def delete_older_than(self, *, before_ts: float, signal_type: Optional[str] = None, max_rows: Optional[int] = None) -> int:
        pool = await init_pool()
        if pool is None:
            raise RuntimeError("db_pool_unavailable")
        async with pool.acquire() as conn:  # type: ignore
            if max_rows and max_rows > 0:
                # Limit via ctid trick to avoid full table lock; portable enough for dev
                sql = (
                    "WITH del AS (SELECT ctid FROM trading_signals WHERE created_at < to_timestamp($1) "
                    "AND ($2::text IS NULL OR signal_type = $2) ORDER BY created_at ASC LIMIT $3) "
                    "DELETE FROM trading_signals t USING del WHERE t.ctid = del.ctid"
                )
                result = await conn.execute(sql, float(before_ts), signal_type, int(max_rows))
            else:
                result = await conn.execute(DELETE_OLDER_THAN_SQL, float(before_ts), signal_type)
        try:
            return int(str(result).split(" ")[-1])
        except Exception:
            return 0

__all__ = ["TradingSignalRepository"]
