#!/usr/bin/env python
"""Continuity scanner: detect OHLCV gaps over a historical range (default 365d).

Usage:
  python scripts/jobs/continuity_scan.py \
      --symbol XRPUSDT --interval 1m --days 365 \
      [--json out.json] [--auto-insert-missing] [--dsn postgres://...]

Features:
  - Streams candles ordered by open_time from canonical table `ohlcv_candles`
  - Detects gaps where delta(open_time) > interval_ms
  - Outputs summary + optional JSON structure
  - Optional automatic insertion of gap segments not already recorded (merge logic relies on existing repository features)

Exit codes:
  0: success (no gaps OR gaps reported)
  2: execution error (DB/connect/logic)

TODO (Phase2):
  - Push completeness & largest gap metrics directly (or set gauge via admin endpoint)
  - Support partial day range (--from-ts / --to-ts overrides)
"""
from __future__ import annotations
import argparse, asyncio, json, math, os, sys, time
from typing import Any, List, Tuple

import asyncpg

# Local project imports (lazy) -------------------------------------------------
try:
    from backend.common.config.base_config import load_config as _load_cfg
    from backend.apps.ingestion.repository.gap_repository import GapRepository
except Exception:
    _load_cfg = None  # type: ignore
    GapRepository = None  # type: ignore

INTERVAL_UNITS = {"m": 60_000, "h": 60*60_000, "d": 24*60*60_000}

def interval_to_ms(interval: str) -> int:
    try:
        unit = interval[-1]
        val = int(interval[:-1])
        return val * INTERVAL_UNITS[unit]
    except Exception:
        return 60_000

async def fetch_candles(pool: asyncpg.Pool, symbol: str, interval: str, start_ms: int, end_ms: int) -> List[Tuple[int]]:
    sql = """
    SELECT open_time FROM ohlcv_candles
    WHERE symbol=$1 AND interval=$2 AND open_time BETWEEN $3 AND $4
    ORDER BY open_time ASC
    """
    rows = await pool.fetch(sql, symbol.upper(), interval, start_ms, end_ms)
    return [(r["open_time"],) for r in rows]

async def load_existing_gap_keys(pool: asyncpg.Pool, symbol: str, interval: str) -> set[int]:
    sql = "SELECT from_open_time FROM gap_segments WHERE symbol=$1 AND interval=$2 AND status <> 'recovered'"
    rows = await pool.fetch(sql, symbol.upper(), interval)
    return {r["from_open_time"] for r in rows}

async def insert_gap(pool: asyncpg.Pool, symbol: str, interval: str, from_ot: int, to_ot: int, missing: int) -> None:
    sql = (
        "INSERT INTO gap_segments (symbol, interval, from_open_time, to_open_time, missing_bars, remaining_bars, status)"
        " VALUES ($1,$2,$3,$4,$5,$5,'open') ON CONFLICT (symbol, interval, from_open_time) DO NOTHING"
    )
    await pool.execute(sql, symbol.upper(), interval, from_ot, to_ot, missing)

async def scan(args) -> int:
    # Config / defaults
    if _load_cfg:
        cfg = _load_cfg()
        dsn = args.dsn or os.getenv("DATABASE_URL") or os.getenv("DB_DSN")
        if not dsn and getattr(cfg, "database_dsn", None):
            dsn = cfg.database_dsn
    else:
        cfg = None
        dsn = args.dsn or os.getenv("DATABASE_URL") or os.getenv("DB_DSN")
    if not dsn:
        print("[continuity] ERROR: database DSN not provided (use --dsn or env)", file=sys.stderr)
        return 2

    symbol = args.symbol or (cfg.symbol if cfg else None)
    interval = args.interval or (cfg.kline_interval if cfg else None)
    if not symbol or not interval:
        print("[continuity] ERROR: symbol/interval required", file=sys.stderr)
        return 2

    now_ms = int(time.time()*1000)
    interval_ms = interval_to_ms(interval)
    total_span_ms = args.days * 24 * 60 * 60 * 1000
    start_ms = now_ms - total_span_ms
    end_ms = now_ms

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    async with pool.acquire() as conn:  # type: ignore
        candles = await fetch_candles(pool, symbol, interval, start_ms, end_ms)
    if not candles:
        print(json.dumps({"symbol": symbol, "interval": interval, "gaps": [], "message": "no data"}))
        return 0

    gaps = []
    prev_ot = candles[0][0]
    for (ot,) in candles[1:]:
        delta = ot - prev_ot
        if delta > interval_ms:
            missing_bars = (delta // interval_ms) - 1
            from_ot = prev_ot + interval_ms
            to_ot = ot - interval_ms
            gaps.append({
                "from_open_time": from_ot,
                "to_open_time": to_ot,
                "missing_bars": missing_bars,
                "span_ms": delta - interval_ms,
            })
        prev_ot = ot

    result = {
        "symbol": symbol,
        "interval": interval,
        "scanned_days": args.days,
        "candle_start": candles[0][0],
        "candle_end": candles[-1][0],
        "gap_count": len(gaps),
        "gaps": gaps,
        "interval_ms": interval_ms,
    }

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    # Optional auto-insert
    if args.auto_insert_missing and gaps:
        async with pool.acquire() as conn:  # type: ignore
            existing_keys = await load_existing_gap_keys(pool, symbol, interval)
            inserted = 0
            for g in gaps:
                if g["from_open_time"] in existing_keys:
                    continue
                await insert_gap(pool, symbol, interval, g["from_open_time"], g["to_open_time"], g["missing_bars"])
                inserted += 1
            result["auto_inserted"] = inserted

    print(json.dumps(result, ensure_ascii=False))
    await pool.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description="Continuity (gap) scanner")
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--interval", type=str, default=None)
    parser.add_argument("--days", type=int, default=365, help="Days back to scan (default 365)")
    parser.add_argument("--json", type=str, default=None, help="Write full result to file")
    parser.add_argument("--auto-insert-missing", action="store_true", dest="auto_insert_missing")
    parser.add_argument("--dsn", type=str, default=None, help="Override database DSN")
    args = parser.parse_args()
    try:
        code = asyncio.run(scan(args))
    except KeyboardInterrupt:
        code = 130
    except Exception as e:  # noqa: BLE001
        print(f"[continuity] ERROR: {e}", file=sys.stderr)
        code = 2
    sys.exit(code)

if __name__ == "__main__":  # pragma: no cover
    main()
