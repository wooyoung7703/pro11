from __future__ import annotations
from typing import Any, Dict, Sequence
import asyncpg
from backend.common.db.connection import init_pool

# Unified to canonical ohlcv_candles table
INSERT_SQL = """
INSERT INTO ohlcv_candles (
    symbol, interval, open_time, close_time,
    open, high, low, close, volume, trade_count,
    taker_buy_volume, taker_buy_quote_volume, is_closed, ingestion_source
) VALUES (
    $1, $2, $3, $4,
    $5, $6, $7, $8, $9, $10,
    $11, $12, $13, $14
) ON CONFLICT (symbol, interval, open_time) DO UPDATE SET
    close_time = EXCLUDED.close_time,
    high = GREATEST(ohlcv_candles.high, EXCLUDED.high),
    low = LEAST(ohlcv_candles.low, EXCLUDED.low),
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    trade_count = EXCLUDED.trade_count,
    taker_buy_volume = EXCLUDED.taker_buy_volume,
    taker_buy_quote_volume = EXCLUDED.taker_buy_quote_volume,
    is_closed = EXCLUDED.is_closed,
    ingestion_source = COALESCE(EXCLUDED.ingestion_source, ohlcv_candles.ingestion_source),
    updated_at = NOW();
"""

async def upsert_kline(k: Dict[str, Any], ingestion_source: str | None = None) -> None:
    pool = await init_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            INSERT_SQL,
            k["s"],              # symbol
            k["i"],              # interval
            k["t"],              # open_time (ms)
            k["T"],              # close_time (ms)
            k["o"],              # open
            k["h"],              # high
            k["l"],              # low
            k["c"],              # close
            k["v"],              # volume
            k.get("n", 0),       # trade_count
            k.get("V"),          # taker_buy_volume
            k.get("Q"),          # taker_buy_quote_volume
            k["x"],              # is_closed
            ingestion_source,
        )

async def bulk_upsert(klines: Sequence[Dict[str, Any]], ingestion_source: str | None = None) -> None:
    if not klines:
        return
    pool = await init_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for k in klines:
                await conn.execute(
                    INSERT_SQL,
                    k["s"], k["i"], k["t"], k["T"],
                    k["o"], k["h"], k["l"], k["c"], k["v"], k.get("n", 0),
                    k.get("V"), k.get("Q"), k["x"], ingestion_source
                )

async def fetch_recent(symbol: str, interval: str, limit: int = 100) -> list[dict[str, Any]]:
    """Fetch recent OHLCV rows (closed or in-progress) ordered by open_time descending.

    Returns list of dicts with numeric conversion for convenience.
    """
    limit = max(1, min(1000, limit))
    pool = await init_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT open_time, close_time, open, high, low, close, volume, trade_count,
                   taker_buy_volume, taker_buy_quote_volume, is_closed
            FROM ohlcv_candles
            WHERE symbol=$1 AND interval=$2
            ORDER BY open_time DESC
            LIMIT $3
            """,
            symbol, interval, limit
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            # Convert Decimal -> float where appropriate
            for num_key in ["open","high","low","close","volume","taker_buy_volume","taker_buy_quote_volume"]:
                v = d.get(num_key)
                if v is not None:
                    try:
                        d[num_key] = float(v)
                    except Exception:
                        pass
            out.append(d)
        return out
