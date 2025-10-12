#!/usr/bin/env python
"""1년 백필 검증 스크립트

검증 항목:
1. DB 에 365일 범위 (now-365d .. now) 의 1m 캔들 존재 여부
2. 기대 바 수 대비 실제 비율 (partial 제외)
3. 임의 샘플 10개 timestamp 에 대해 Binance REST /fapi/v1/klines 재검증 (정합성: OHLC 일치)
4. 가장 오래된 캔들 open_time 이 (now-365d) 이상인지

Usage:
  python scripts/verify_year_backfill.py [SYMBOL] [INTERVAL]
환경변수:
  DB_DSN=postgresql://user:pass@host:port/dbname  (없으면 기존 init_pool 설정 시도)

Binance REST 제한: 과도한 호출 방지를 위해 샘플 10개만 검증.
"""
import os, sys, asyncio, time, random, math
from typing import List, Tuple, Dict, Any
import aiohttp
import asyncpg

SYMBOL = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("SYMBOL", "XRPUSDT")).upper()
INTERVAL = sys.argv[2] if len(sys.argv) > 2 else os.getenv("INTERVAL", "1m")
BINANCE = "https://fapi.binance.com"

# Interval to ms

def interval_to_ms(it: str) -> int:
    try:
        unit = it[-1]
        val = int(it[:-1])
        if unit == 'm':
            return val * 60_000
        if unit == 'h':
            return val * 60 * 60_000
        if unit == 'd':
            return val * 24 * 60 * 60_000
    except Exception:
        pass
    return 60_000

INTERVAL_MS = interval_to_ms(INTERVAL)

async def get_pool():
    # 재사용: 프로젝트 내 init_pool 로직 활용 시도
    try:
        from backend.common.db.connection import init_pool  # type: ignore
        pool = await init_pool()
        if pool:
            return pool
    except Exception:
        pass
    dsn = os.getenv("DB_DSN")
    if not dsn:
        raise RuntimeError("DB_DSN env not set and init_pool unavailable")
    return await asyncpg.create_pool(dsn)

async def fetch_range_meta(conn) -> Dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT MIN(open_time) AS earliest, MAX(open_time) AS latest, COUNT(*) AS cnt
        FROM ohlcv_candles
        WHERE symbol=$1 AND interval=$2 AND is_closed=true
        """,
        SYMBOL, INTERVAL,
    )
    return {k: (int(row[k]) if row and row[k] is not None else None) for k in ["earliest","latest","cnt"]}

async def fetch_sample_candles(conn, start_ms: int, end_ms: int, sample: int = 10) -> List[int]:
    # uniform random sampling by index inside the range by ordering
    row = await conn.fetchrow(
        """SELECT COUNT(*) AS c FROM ohlcv_candles WHERE symbol=$1 AND interval=$2 AND open_time BETWEEN $3 AND $4 AND is_closed=true""",
        SYMBOL, INTERVAL, start_ms, end_ms,
    )
    total = int(row["c"]) if row and row["c"] is not None else 0
    if total == 0:
        return []
    picks = sorted(random.sample(range(total), min(sample, total)))
    out: List[int] = []
    for pk in picks:
        r = await conn.fetchrow(
            """SELECT open_time FROM ohlcv_candles WHERE symbol=$1 AND interval=$2 AND open_time BETWEEN $3 AND $4 AND is_closed=true ORDER BY open_time ASC OFFSET $5 LIMIT 1""",
            SYMBOL, INTERVAL, start_ms, end_ms, pk,
        )
        if r and r["open_time"] is not None:
            out.append(int(r["open_time"]))
    return out

async def fetch_candle_by_open(conn, open_time: int) -> Dict[str, Any] | None:
    r = await conn.fetchrow(
        """SELECT open_time, close_time, open, high, low, close, volume FROM ohlcv_candles WHERE symbol=$1 AND interval=$2 AND open_time=$3""",
        SYMBOL, INTERVAL, open_time,
    )
    return dict(r) if r else None

async def fetch_binance_klines(session: aiohttp.ClientSession, start: int, end: int) -> List[List[Any]]:
    params = {"symbol": SYMBOL, "interval": INTERVAL, "startTime": start, "endTime": end, "limit": 1500}
    async with session.get(f"{BINANCE}/fapi/v1/klines", params=params, timeout=10) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Binance HTTP {resp.status}")
        return await resp.json()

async def verify_samples(samples: List[int], session, conn) -> List[Dict[str, Any]]:
    results = []
    for ot in samples:
        # Fetch single kline window [ot, ot+INTERVAL_MS)
        data = await fetch_binance_klines(session, ot, ot + INTERVAL_MS)
        # find matching open_time
        rec = None
        for item in data:
            if int(item[0]) == ot:
                rec = item
                break
        db_row = await fetch_candle_by_open(conn, ot)
        ok = False
        diff_detail = None
        if rec and db_row:
            # Compare OHLC as floats
            o, h, l, c = map(float, rec[1:5])
            tol = 1e-9
            comp_ok = (abs(o - float(db_row["open"])) < tol and
                       abs(h - float(db_row["high"])) < tol and
                       abs(l - float(db_row["low"])) < tol and
                       abs(c - float(db_row["close"])) < tol)
            ok = comp_ok
            if not comp_ok:
                diff_detail = {
                    "db": {"o": db_row["open"], "h": db_row["high"], "l": db_row["low"], "c": db_row["close"]},
                    "binance": {"o": o, "h": h, "l": l, "c": c},
                }
        results.append({"open_time": ot, "ok": ok, "diff": diff_detail})
    return results

async def main():
    pool = await get_pool()
    async with pool.acquire() as conn:  # type: ignore
        meta = await fetch_range_meta(conn)
        earliest = meta["earliest"]
        latest = meta["latest"]
        count = meta["cnt"] or 0
        if earliest is None or latest is None:
            print("NO_DATA")
            return
        now_ms = int(time.time()*1000)
        start_1y = now_ms - 365*24*60*60*1000
        window_start = max(start_1y, earliest)
        window_end = latest
        expected = ((window_end - window_start) // INTERVAL_MS) + 1 if window_end >= window_start else 0
        # Only closed bars assumption (partial 제외)
        span_count_row = await conn.fetchrow(
            """SELECT COUNT(*) AS c FROM ohlcv_candles WHERE symbol=$1 AND interval=$2 AND open_time BETWEEN $3 AND $4 AND is_closed=true""",
            SYMBOL, INTERVAL, window_start, window_end,
        )
        span_count = int(span_count_row["c"]) if span_count_row and span_count_row["c"] is not None else 0
        completeness = (span_count/expected) if expected>0 else None
        print(f"RANGE earliest={earliest} latest={latest} window_start={window_start} expected={expected} span_count={span_count} completeness={completeness}")
        # Sample verify
        samples = await fetch_sample_candles(conn, window_start, window_end, sample=10)
        async with aiohttp.ClientSession() as session:
            sample_results = await verify_samples(samples, session, conn)
        ok_samples = sum(1 for r in sample_results if r["ok"]) if sample_results else 0
        print(f"SAMPLES total={len(sample_results)} ok={ok_samples}")
        for r in sample_results:
            status = "OK" if r["ok"] else "MISMATCH"
            print(f"SAMPLE {status} open_time={r['open_time']} diff={r['diff']}")

if __name__ == "__main__":
    asyncio.run(main())
