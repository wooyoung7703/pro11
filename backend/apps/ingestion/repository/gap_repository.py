from __future__ import annotations
from typing import Any, Sequence
import asyncpg
from backend.common.db.connection import init_pool

INSERT_SQL = """
INSERT INTO gap_segments (symbol, interval, from_open_time, to_open_time, missing_bars, remaining_bars, status)
VALUES ($1,$2,$3,$4,$5,$6,'open')
ON CONFLICT (symbol, interval, from_open_time) DO NOTHING;  -- simple; future: composite span uniqueness
"""
# Note: if overlapping gaps could exist we may need a composite uniqueness constraint.
# For simplicity we rely on (symbol, interval, from_open_time) uniqueness semantic.

UPDATE_PARTIAL_SQL = """
UPDATE gap_segments
SET remaining_bars=$4, recovered_bars=recovered_bars + $5, status=CASE WHEN $4=0 THEN 'recovered' ELSE 'partial' END,
    recovered_at = CASE WHEN $4=0 THEN now() ELSE recovered_at END
WHERE id=$1
RETURNING status, remaining_bars;
"""

MARK_RECOVERED_SQL = """
UPDATE gap_segments
SET remaining_bars=0, recovered_bars=missing_bars, status='recovered', recovered_at=now()
WHERE id=$1 AND status <> 'recovered';
"""

SELECT_OPEN_SQL = """
SELECT id, symbol, interval, from_open_time, to_open_time, missing_bars, remaining_bars, detected_at, recovered_at, recovered_bars, status
FROM gap_segments
WHERE status <> 'recovered' AND symbol=$1 AND interval=$2
ORDER BY detected_at ASC
LIMIT $3;
"""

SELECT_RECENT_SQL = """
SELECT id, symbol, interval, from_open_time, to_open_time, missing_bars, remaining_bars, detected_at, recovered_at, recovered_bars, status
FROM gap_segments
WHERE symbol=$1 AND interval=$2
ORDER BY detected_at DESC
LIMIT $3;
"""

class GapRepository:
    @staticmethod
    async def insert_gap(symbol: str, interval: str, from_open: int, to_open: int, missing_bars: int) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await conn.execute(INSERT_SQL, symbol, interval, from_open, to_open, missing_bars, missing_bars)

    @staticmethod
    async def merge_or_insert_gap(symbol: str, interval: str, from_open: int, to_open: int, missing_bars: int) -> dict[str, Any]:
        """Merge overlapping gap segments with precise missing bar recomputation.

        Steps:
          1. Lock overlapping gap segments (status != recovered)
          2. If none overlap -> insert directly
          3. Else compute merged span [min_from, max_to]
          4. Recalculate expected bars = ((max_to - min_from) / interval_ms) + 1
          5. Count existing candles in canonical table inside span
          6. missing_bars = expected - present (>=0)
          7. Mark old segments recovered (merged) & insert new merged segment
        """
        pool = await init_pool()
        async with pool.acquire() as conn:  # type: ignore
            tr = conn.transaction()
            await tr.start()
            try:
                rows = await conn.fetch(
                    """
                    SELECT id, from_open_time, to_open_time, missing_bars, remaining_bars
                    FROM gap_segments
                    WHERE symbol=$1 AND interval=$2 AND status <> 'recovered'
                      AND NOT ($4 < from_open_time OR $3 > to_open_time)  -- overlap condition
                    FOR UPDATE
                    """,
                    symbol,
                    interval,
                    from_open,
                    to_open,
                )
                if not rows:
                    await conn.execute(INSERT_SQL, symbol, interval, from_open, to_open, missing_bars, missing_bars)
                    await tr.commit()
                    return {"symbol": symbol, "interval": interval, "from_open_time": from_open, "to_open_time": to_open, "missing_bars": missing_bars, "merged": False}
                # merged span
                min_from = min([r["from_open_time"] for r in rows] + [from_open])
                max_to = max([r["to_open_time"] for r in rows] + [to_open])
                # Compute interval ms by inferring from incoming gap or existing segments.
                # We assume uniform fixed interval across dataset; derive from smallest existing span delta.
                # Safer: query one next candle to compute diff if needed. Here we use heuristic from first row span / missing+1 when possible.
                # Instead of heuristic, attempt: find nearest two candle open_times to compute interval.
                interval_ms: int | None = None
                # Try using one existing row if it had missing_bars info (approx) – fallback to direct diff of provided bounds / (missing_bars+1).
                if missing_bars > 0 and (to_open - from_open) > 0:
                    approx = (to_open - from_open) // (missing_bars + 1)
                    if approx > 0:
                        interval_ms = approx
                if interval_ms is None:
                    # Fallback query: fetch two consecutive candles inside or adjacent to span
                    cand = await conn.fetch(
                        """
                        SELECT open_time FROM ohlcv_candles
                        WHERE symbol=$1 AND interval=$2 AND open_time BETWEEN $3-($5*1000) AND $4+($5*1000)
                        ORDER BY open_time ASC LIMIT 3
                        """,
                        symbol,
                        interval,
                        min_from,
                        max_to,
                        60,
                    )
                    if len(cand) >= 2:
                        interval_ms = cand[1]["open_time"] - cand[0]["open_time"]
                if interval_ms is None or interval_ms <= 0:
                    # Conservative default 60s (1m) to avoid division by zero
                    interval_ms = 60_000
                expected_bars = ((max_to - min_from) // interval_ms) + 1
                present_row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) AS c FROM ohlcv_candles
                    WHERE symbol=$1 AND interval=$2 AND open_time BETWEEN $3 AND $4
                    """,
                    symbol,
                    interval,
                    min_from,
                    max_to,
                )
                present = int(present_row["c"]) if present_row else 0
                precise_missing = max(0, expected_bars - present)
                # mark existing as recovered (merged)
                existing_ids = [r["id"] for r in rows]
                await conn.execute(
                    "UPDATE gap_segments SET status='recovered', recovered_at=now(), recovered_bars=missing_bars, remaining_bars=0 WHERE id = ANY($1::bigint[])",
                    existing_ids,
                )
                await conn.execute(INSERT_SQL, symbol, interval, min_from, max_to, precise_missing, precise_missing)
                await conn.execute(
                    "UPDATE gap_segments SET merged=true WHERE symbol=$1 AND interval=$2 AND from_open_time=$3",
                    symbol,
                    interval,
                    min_from,
                )
                await tr.commit()
                return {"symbol": symbol, "interval": interval, "from_open_time": min_from, "to_open_time": max_to, "missing_bars": precise_missing, "merged": True, "expected_bars": expected_bars, "present_bars": present}
            except Exception:
                await tr.rollback()
                raise

    @staticmethod
    async def load_open(symbol: str, interval: str, limit: int = 500) -> list[dict[str, Any]]:
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(SELECT_OPEN_SQL, symbol, interval, limit)
            return [dict(r) for r in rows]

    @staticmethod
    async def partial_recover(gap_id: int, remaining_bars: int, recovered_now: int) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await conn.fetchrow(UPDATE_PARTIAL_SQL, gap_id, None, None, remaining_bars, recovered_now)

    @staticmethod
    async def mark_recovered(gap_id: int) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await conn.execute(MARK_RECOVERED_SQL, gap_id)

    @staticmethod
    async def recent(symbol: str, interval: str, limit: int = 50) -> list[dict[str, Any]]:
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(SELECT_RECENT_SQL, symbol, interval, limit)
            return [dict(r) for r in rows]

__all__ = ["GapRepository"]
