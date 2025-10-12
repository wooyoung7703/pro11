from __future__ import annotations
from typing import Dict, Any, List, Optional
import asyncpg
from backend.common.db.connection import init_pool

UPSERT_STATE_SQL = """
INSERT INTO risk_state (session_key, starting_equity, peak_equity, current_equity, cumulative_pnl, last_reset_ts)
VALUES ($1,$2,$3,$4,$5,$6)
ON CONFLICT (session_key) DO UPDATE SET
    peak_equity = EXCLUDED.peak_equity,
    current_equity = EXCLUDED.current_equity,
    cumulative_pnl = EXCLUDED.cumulative_pnl,
    last_reset_ts = EXCLUDED.last_reset_ts,
    updated_at = NOW();
"""

SELECT_STATE_SQL = "SELECT * FROM risk_state WHERE session_key = $1";

UPSERT_POSITION_SQL = """
INSERT INTO risk_positions (session_key, symbol, size, entry_price)
VALUES ($1,$2,$3,$4)
ON CONFLICT (session_key, symbol) DO UPDATE SET
    size = EXCLUDED.size,
    entry_price = EXCLUDED.entry_price,
    updated_at = NOW();
"""

SELECT_POSITIONS_SQL = "SELECT symbol, size::float, entry_price::float FROM risk_positions WHERE session_key = $1";

DELETE_POSITION_SQL = "DELETE FROM risk_positions WHERE session_key = $1 AND symbol = $2";
DELETE_ALL_POSITIONS_SQL = "DELETE FROM risk_positions WHERE session_key = $1";

class RiskRepository:
    def __init__(self, session_key: str):
        self.session_key = session_key

    async def load_state(self) -> Optional[asyncpg.Record]:
        pool = await init_pool()
        async with pool.acquire() as conn:
            return await conn.fetchrow(SELECT_STATE_SQL, self.session_key)

    async def save_state(self, starting_equity: float, peak_equity: float, current_equity: float, cumulative_pnl: float, last_reset_ts: int) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await conn.execute(UPSERT_STATE_SQL, self.session_key, starting_equity, peak_equity, current_equity, cumulative_pnl, last_reset_ts)

    async def save_position(self, symbol: str, size: float, entry_price: float) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            if size == 0:
                await conn.execute(DELETE_POSITION_SQL, self.session_key, symbol)
            else:
                await conn.execute(UPSERT_POSITION_SQL, self.session_key, symbol, size, entry_price)

    async def load_positions(self) -> List[asyncpg.Record]:
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(SELECT_POSITIONS_SQL, self.session_key)
            return list(rows)

    async def clear_positions(self) -> int:
        """Delete all positions for the session. Returns number of rows deleted (best effort)."""
        pool = await init_pool()
        async with pool.acquire() as conn:
            status = await conn.execute(DELETE_ALL_POSITIONS_SQL, self.session_key)
            try:
                return int(status.split()[-1])
            except Exception:
                return 0
