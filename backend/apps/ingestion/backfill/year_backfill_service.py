from __future__ import annotations
"""One-shot 1-year historical backfill service (Binance Futures REST klines).

Exposes async start_year_backfill() which launches a background task to
fetch closed klines from (now - 365d) .. now for the configured symbol/interval.

Progress is tracked in-memory (year_backfill_state) and persisted via
BackfillRunRepository so existing audit tables show the run.

NOTE: This is intentionally simple (single task, sequential REST calls).
      Future improvements: rate-limit adaptation, resume partial, multi-symbol.
"""
import asyncio
import time
from dataclasses import dataclass, asdict
import logging, os
from typing import Optional, Dict, Any, List

import aiohttp
import asyncpg

from backend.common.config.base_config import load_config
from backend.apps.ingestion.repository.ohlcv_repository import bulk_upsert, delete_range
from backend.apps.ingestion.repository.backfill_run_repository import BackfillRunRepository
from backend.common.db.schema_manager import ensure_all as ensure_all_schema

BINANCE_FUTURES_REST = "https://fapi.binance.com"

cfg = load_config()
logger = logging.getLogger(__name__)
_LOG_FULL_BINANCE_YEAR = os.getenv("YEAR_BACKFILL_LOG_BINANCE", "0").lower() in {"1","true","yes","on"}

@dataclass
class YearBackfillState:
    run_id: int
    symbol: str
    interval: str
    started_at: float
    from_open_time: int
    to_open_time: int
    expected_bars: int
    loaded_bars: int = 0
    status: str = "pending"  # pending|running|success|error|partial
    last_error: Optional[str] = None
    finished_at: Optional[float] = None
    last_progress_ts: Optional[float] = None
    request_count: int = 0  # number of REST requests performed
    last_batch_size: int = 0  # size of most recent payload

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        now = time.time()
        d["elapsed_seconds"] = (self.finished_at or now) - self.started_at
        if self.expected_bars > 0:
            d["percent"] = min(100.0, (self.loaded_bars / self.expected_bars) * 100.0)
        else:
            d["percent"] = 0.0
        if self.loaded_bars and self.status == "running":
            rate = self.loaded_bars / max(1.0, (now - self.started_at))  # bars/sec
            remaining = max(0, self.expected_bars - self.loaded_bars)
            d["eta_seconds"] = remaining / rate if rate > 0 else None
        else:
            d["eta_seconds"] = None
        return d

_year_backfill_states: dict[str, YearBackfillState] = {}
_year_backfill_tasks: dict[str, asyncio.Task] = {}
_year_backfill_locks: dict[str, asyncio.Lock] = {}

def _interval_to_ms(interval: str) -> int:
    try:
        unit = interval[-1]
        val = int(interval[:-1])
        if unit == 'm':
            return val * 60_000
        if unit == 'h':
            return val * 60 * 60_000
        if unit == 'd':
            return val * 24 * 60 * 60_000
    except Exception:  # pragma: no cover
        pass
    return 60_000

async def start_year_backfill(symbol: str | None = None, interval: str | None = None, *, overwrite: bool = True, purge: bool = False) -> Dict[str, Any]:
    """Start a 1-year historical backfill for a given symbol/interval.

    If a run is currently running for that symbol+interval, returns already_running.
    Otherwise creates new state and task.
    """
    sym = (symbol or cfg.symbol).upper()
    itv = interval or cfg.kline_interval
    key = f"{sym}|{itv}"
    lock = _year_backfill_locks.setdefault(key, asyncio.Lock())
    async with lock:
        st = _year_backfill_states.get(key)
        if st and st.status in {"running", "pending"}:
            return {"status": "already_running", "state": st.to_dict()}
        interval_ms = _interval_to_ms(itv)
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - 365 * 24 * 60 * 60 * 1000
        # Exclude the currently forming (partial) candle from expectation to avoid off-by-one
        current_open_boundary = (now_ms // interval_ms) * interval_ms
        latest_closed_open = current_open_boundary - interval_ms
        if latest_closed_open < start_ms:
            latest_closed_open = start_ms
        expected_bars = ((latest_closed_open - start_ms) // interval_ms) + 1
        # Create DB audit run with two attempts (schema ensure retry)
        run_id: int | None = None
        create_err: Exception | None = None
        for attempt in range(2):
            try:
                run_id = await BackfillRunRepository.create(sym, itv, start_ms, now_ms, expected_bars)
                create_err = None
                break
            except asyncpg.UndefinedTableError as e:
                create_err = e
                await ensure_all_schema()
            except Exception as e:  # other error
                create_err = e
                if attempt == 0:
                    try:
                        await ensure_all_schema()
                    except Exception:
                        pass
        if run_id is None:
            state_err = YearBackfillState(
                run_id=-1,
                symbol=sym,
                interval=itv,
                started_at=time.time(),
                from_open_time=start_ms,
                to_open_time=now_ms,
                expected_bars=expected_bars,
                status="error",
                last_error=f"create_run_failed: {create_err}",
                finished_at=time.time(),
            )
            _year_backfill_states[key] = state_err
            return {"status": "error", "state": state_err.to_dict()}
        state = YearBackfillState(
            run_id=run_id,
            symbol=sym,
            interval=itv,
            started_at=time.time(),
            from_open_time=start_ms,
            to_open_time=now_ms,
            expected_bars=expected_bars,
        )
        _year_backfill_states[key] = state
        task = asyncio.create_task(_run_year_backfill(state, overwrite=overwrite, purge=purge))
        _year_backfill_tasks[key] = task
        return {"status": "started", "state": state.to_dict()}

async def get_year_backfill_status(symbol: str | None = None, interval: str | None = None) -> Dict[str, Any] | None:
    sym = (symbol or cfg.symbol).upper()
    itv = interval or cfg.kline_interval
    key = f"{sym}|{itv}"
    st = _year_backfill_states.get(key)
    return st.to_dict() if st else None

async def _run_year_backfill(state: YearBackfillState, *, overwrite: bool = True, purge: bool = False):
    """Internal runner that sequentially fetches 1y historical klines and upserts them.

    Updates the in-memory state as it progresses so the status endpoint reflects progress.
    """
    symbol = state.symbol
    interval = state.interval
    interval_ms = _interval_to_ms(interval)
    max_batch = 1500
    state.status = "running"
    await BackfillRunRepository.start(state.run_id)
    url = f"{BINANCE_FUTURES_REST}/fapi/v1/klines"
    start = state.from_open_time
    end = state.to_open_time
    session: aiohttp.ClientSession | None = None
    loaded = 0
    try:
        session = aiohttp.ClientSession()
        # Optionally purge the range before backfilling
        if purge:
            try:
                deleted = await delete_range(symbol, interval, start, end)
                logger.info("year_backfill_purge run_id=%s symbol=%s interval=%s deleted=%s from=%s to=%s", state.run_id, symbol, interval, deleted, start, end)
            except Exception as de:
                logger.warning("year_backfill_purge_failed run_id=%s symbol=%s interval=%s err=%s", state.run_id, symbol, interval, de)
        cur_start = start
        # Loop until we covered range
        while cur_start <= end:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": cur_start,
                "endTime": end,
                "limit": max_batch,
            }
            req_started = time.perf_counter()
            async with session.get(url, params=params, timeout=15) as resp:
                latency = time.perf_counter() - req_started
                if resp.status != 200:
                    state.last_error = f"HTTP {resp.status}"
                    state.status = "error"
                    await BackfillRunRepository.finish_error(state.run_id, state.last_error)
                    logger.warning(
                        "year_backfill_http_error run_id=%s symbol=%s interval=%s status=%s startTime=%s endTime=%s loaded=%s expected=%s latency_ms=%.2f",
                        state.run_id,
                        symbol,
                        interval,
                        resp.status,
                        params.get("startTime"),
                        params.get("endTime"),
                        state.loaded_bars,
                        state.expected_bars,
                        latency*1000,
                    )
                    return
                data = await resp.json()

                if not data:
                    logger.debug(
                        "year_backfill_empty_batch run_id=%s symbol=%s interval=%s cur_start=%s end=%s loaded=%s",
                        state.run_id,
                        symbol,
                        interval,
                        cur_start,
                        end,
                        state.loaded_bars,
                    )
                    break
                batch_len = len(data)
                sample_first = int(data[0][0]) if batch_len else None
                sample_last = int(data[-1][0]) if batch_len else None
                logger.debug(
                    "year_backfill_fetch run_id=%s symbol=%s interval=%s batch=%s loaded=%s/%s first_ot=%s last_ot=%s cur_start=%s end=%s latency_ms=%.2f",
                    state.run_id,
                    symbol,
                    interval,
                    batch_len,
                    state.loaded_bars,
                    state.expected_bars,
                    sample_first,
                    sample_last,
                    cur_start,
                    end,
                    latency*1000,
                )
                if _LOG_FULL_BINANCE_YEAR:
                    logger.info(
                        "year_backfill_payload_dump run_id=%s symbol=%s interval=%s data=%s",
                        state.run_id,
                        symbol,
                        interval,
                        data,
                    )
                payload: List[dict] = []
                max_open = cur_start
                for item in data:
                    open_time = int(item[0])
                    close_time = int(item[6])
                    if open_time > end:
                        break
                    max_open = max(max_open, open_time)
                    # closed bar only (Binance sends only closed in historical REST anyway)
                    k = {
                        "t": open_time,
                        "T": close_time,
                        "s": symbol,
                        "i": interval,
                        "o": item[1],
                        "h": item[2],
                        "l": item[3],
                        "c": item[4],
                        "v": item[5],
                        "n": int(item[8]),
                        "V": item[9],
                        "Q": item[10],
                        "x": True,
                    }

                    payload.append(k)
                if payload:
                    await bulk_upsert(payload, ingestion_source="year_backfill", overwrite=overwrite)
                    loaded += len(payload)
                    state.loaded_bars = loaded
                    state.last_batch_size = len(payload)
                    state.request_count += 1
                    state.last_progress_ts = time.time()
                    await BackfillRunRepository.update_progress(state.run_id, loaded)
                # advance start (next open_time after last batch)
                cur_start = max_open + interval_ms
                if len(data) < max_batch:
                    break  # no more data
        state.status = "success"
        state.finished_at = time.time()
        await BackfillRunRepository.finish_success(state.run_id)
    except asyncio.CancelledError:
        state.status = "partial"
        state.last_error = "cancelled"
        state.finished_at = time.time()
        await BackfillRunRepository.finish_partial(state.run_id, state.last_error or "cancelled")
        raise
    except Exception as e:  # pragma: no cover - best effort error path
        state.status = "error"
        state.last_error = str(e)
        state.finished_at = time.time()
        try:
            await BackfillRunRepository.finish_error(state.run_id, state.last_error)
        except Exception:
            pass
        # no re-raise; keep state available to status endpoint
    finally:
        if session:
            await session.close()

async def cancel_year_backfill(symbol: str | None = None, interval: str | None = None) -> Dict[str, Any]:
    """Cancel an in-progress 1y backfill for the given symbol/interval (or defaults).

    Returns a dict with current/updated state or idle if nothing was running.
    """
    sym = (symbol or cfg.symbol).upper()
    itv = interval or cfg.kline_interval
    key = f"{sym}|{itv}"
    task = _year_backfill_tasks.get(key)
    st = _year_backfill_states.get(key)
    if not task or not st or st.status not in {"running", "pending"}:
        return {"status": "idle", "state": st.to_dict() if st else None}
    try:
        task.cancel()
        # Give the task a brief moment to handle CancelledError and update state
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except asyncio.TimeoutError:
            # Force-mark partial if it didn't finish in time
            st.status = "partial"
            st.last_error = "cancelled"
            st.finished_at = time.time()
            try:
                await BackfillRunRepository.finish_partial(st.run_id, st.last_error or "cancelled")
            except Exception:
                pass
    finally:
        # cleanup task reference
        _year_backfill_tasks.pop(key, None)
    return {"status": "cancelled", "state": st.to_dict()}

__all__ = ["start_year_backfill", "get_year_backfill_status", "cancel_year_backfill"]
