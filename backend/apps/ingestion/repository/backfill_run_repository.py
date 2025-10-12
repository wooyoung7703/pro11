from __future__ import annotations
from typing import Any
from backend.common.db.connection import init_pool

INSERT_RUN = """
INSERT INTO ohlcv_backfill_runs (symbol, interval, from_open_time, to_open_time, expected_bars, loaded_bars, status)
VALUES ($1,$2,$3,$4,$5,0,'pending') RETURNING id;
"""
START_RUN = """
UPDATE ohlcv_backfill_runs SET status='running', attempts=attempts+1, started_at=now() WHERE id=$1 AND status IN ('pending','error','partial') RETURNING id;
"""
UPDATE_PROGRESS = """
UPDATE ohlcv_backfill_runs SET loaded_bars=$2, last_error=NULL WHERE id=$1;
"""
FINISH_SUCCESS = """
UPDATE ohlcv_backfill_runs SET status='success', finished_at=now() WHERE id=$1;
"""
FINISH_PARTIAL = """
UPDATE ohlcv_backfill_runs SET status='partial', finished_at=now(), last_error=$2 WHERE id=$1;
"""
FINISH_ERROR = """
UPDATE ohlcv_backfill_runs SET status='error', finished_at=now(), last_error=$2 WHERE id=$1;
"""

SELECT_RECENT = """
SELECT id, symbol, interval, from_open_time, to_open_time, expected_bars, loaded_bars, status, attempts, last_error, started_at, finished_at, created_at
FROM ohlcv_backfill_runs
WHERE symbol=$1 AND interval=$2
ORDER BY created_at DESC
LIMIT $3;
"""

class BackfillRunRepository:
    @staticmethod
    async def create(symbol: str, interval: str, from_open: int, to_open: int, expected: int) -> int:
        pool = await init_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(INSERT_RUN, symbol, interval, from_open, to_open, expected)
            return int(row["id"])  # type: ignore

    @staticmethod
    async def start(run_id: int) -> bool:
        pool = await init_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(START_RUN, run_id)
            return bool(row)

    @staticmethod
    async def update_progress(run_id: int, loaded_bars: int) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await conn.execute(UPDATE_PROGRESS, run_id, loaded_bars)

    @staticmethod
    async def finish_success(run_id: int) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await conn.execute(FINISH_SUCCESS, run_id)

    @staticmethod
    async def finish_partial(run_id: int, err: str) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await conn.execute(FINISH_PARTIAL, run_id, err)

    @staticmethod
    async def finish_error(run_id: int, err: str) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await conn.execute(FINISH_ERROR, run_id, err)

    @staticmethod
    async def recent(symbol: str, interval: str, limit: int = 20) -> list[dict[str, Any]]:
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(SELECT_RECENT, symbol, interval, limit)
            return [dict(r) for r in rows]

__all__ = ["BackfillRunRepository"]
