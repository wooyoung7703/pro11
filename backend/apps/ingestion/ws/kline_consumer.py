from __future__ import annotations
import asyncio
import json
import aiohttp
import contextlib
import time
import random
from typing import Any, Dict, Callable, Awaitable, List
from collections import deque
from prometheus_client import Counter, Histogram, Gauge
from backend.apps.ingestion.repository.ohlcv_repository import upsert_kline, bulk_upsert  # canonical table
from backend.apps.ingestion.repository.gap_repository import GapRepository
from backend.common.config.base_config import load_config

cfg = load_config()
BINANCE_FUTURES_WS = "wss://fstream.binance.com/ws"

MSG_COUNTER = Counter("kline_messages_total", "Total kline websocket messages", ["symbol"])
CLOSED_COUNTER = Counter("kline_closed_total", "Closed klines processed", ["symbol"])
FLUSH_COUNTER = Counter("kline_flush_total", "Number of buffer flush operations", ["symbol"])
FLUSH_LATENCY = Histogram("kline_flush_latency_seconds", "Flush DB latency", ["symbol"])
PROCESS_LATENCY = Histogram("kline_process_latency_seconds", "Time from close_time to persistence", ["symbol"])
BUFFER_GAUGE = Gauge("kline_buffer_size", "Current kline buffer size", ["symbol"])
INGESTION_LAG_GAUGE = Gauge("ingestion_lag_seconds", "Seconds since last kline websocket message", ["symbol"])
GAP_DETECTED = Counter("kline_gap_detected_total", "Detected missing OHLCV gap segments", ["symbol"])
GAP_OPEN_SEGMENTS = Gauge("kline_gap_open_segments", "Current open gap segments (unrecovered)", ["symbol"])
GAP_REMAINING_BARS = Gauge("kline_gap_remaining_bars", "Total remaining missing bars across open gap segments", ["symbol"])
GAP_OLDEST_AGE_SECONDS = Gauge("kline_gap_oldest_age_seconds", "Age in seconds of the oldest open gap segment", ["symbol"])
LATE_FILL_TOTAL = Counter("kline_late_fill_total", "Out-of-order (late) closed klines inserted", ["symbol"])

def _interval_to_ms(interval: str) -> int:
    # Basic parser for Binance style intervals (m,h,d)
    try:
        unit = interval[-1]
        val = int(interval[:-1])
        if unit == 'm':
            return val * 60_000
        if unit == 'h':
            return val * 60 * 60_000
        if unit == 'd':
            return val * 24 * 60 * 60_000
    except Exception:  # pragma: no cover - fallback
        pass
    # default assume 1m
    return 60_000

class KlineConsumer:
    def __init__(self, symbol: str, interval: str = "1m", on_kline: Callable[[Dict[str, Any]], Awaitable[None]] | None = None, batch_size: int = 50, flush_interval: float = 1.0, max_retries: int = 0):
        self.symbol = symbol.lower()
        self.interval = interval
        self.on_kline = on_kline or self._default_handler
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._buffer: deque[Dict[str, Any]] = deque()
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._flusher_task: asyncio.Task | None = None
        self._max_retries = max_retries
        # State tracking
        self.last_message_ts: float | None = None
        self.last_closed_kline_close_time: int | None = None
        self.total_messages: int = 0
        self.total_closed: int = 0
        self._reconnect_attempts: int = 0
        self.last_flush_ts = None
        # Gap tracking
        self._last_closed_open_time = None
        self._gaps = []
        self._interval_ms = _interval_to_ms(self.interval)
        self._loaded_persisted = False  # ensure we only hydrate once
        # post-flush listeners: called with list[Dict] of closed klines just flushed
        self._flush_listeners: list[Callable[[List[Dict[str, Any]]], Awaitable[None]]] = []
    
    def _refresh_gap_metrics(self):
        """Recalculate aggregate gap gauges (remaining bars & oldest age)."""
        GAP_OPEN_SEGMENTS.labels(self.symbol).set(len(self._gaps))
        if not self._gaps:
            GAP_REMAINING_BARS.labels(self.symbol).set(0)
            GAP_OLDEST_AGE_SECONDS.labels(self.symbol).set(0)
            return
        now = time.time()
        remaining = 0
        oldest_age = 0.0
        for g in self._gaps:
            remaining += g.get("remaining_bars", g.get("missing_bars", 0))
            detected_ts = g.get("detected_ts") or now
            age = max(0.0, now - detected_ts)
            if age > oldest_age:
                oldest_age = age
        GAP_REMAINING_BARS.labels(self.symbol).set(remaining)
        GAP_OLDEST_AGE_SECONDS.labels(self.symbol).set(oldest_age)

    async def _default_handler(self, k: Dict[str, Any]) -> None:
        # Buffer only closed klines to reduce churn
        if k.get("x") is True:
            self._buffer.append(k)
            if len(self._buffer) >= self._batch_size:
                await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return
        start = time.perf_counter()
        batch: List[Dict[str, Any]] = []
        while self._buffer and len(batch) < self._batch_size:
            batch.append(self._buffer.popleft())
        BUFFER_GAUGE.labels(self.symbol).set(len(self._buffer))
        # Tag origin of ingestion for provenance
        await bulk_upsert(batch, ingestion_source="binance_ws")
        FLUSH_COUNTER.labels(self.symbol).inc()
        FLUSH_LATENCY.labels(self.symbol).observe(time.perf_counter() - start)
        self.last_flush_ts = time.time()
        # fire listeners (best-effort)
        if self._flush_listeners:
            for cb in list(self._flush_listeners):
                try:
                    await cb(batch)
                except Exception:
                    pass

    async def _periodic_flusher(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._flush_interval)
                await self._flush()
        except asyncio.CancelledError:
            # final flush
            await self._flush()
            raise

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._session = aiohttp.ClientSession()
        stream = f"{self.symbol}@kline_{self.interval}"
        url = f"{BINANCE_FUTURES_WS}/{stream}"
        attempt = 0
        while self._running:
            try:
                self._flusher_task = asyncio.create_task(self._periodic_flusher())
                async with self._session.ws_connect(url, heartbeat=30) as ws:  # type: ignore[arg-type]
                    attempt = 0  # reset on success
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            kline = data.get("k")
                            if kline:
                                MSG_COUNTER.labels(self.symbol).inc()
                                self.total_messages += 1
                                if kline.get("x"):
                                    CLOSED_COUNTER.labels(self.symbol).inc()
                                    self.total_closed += 1
                                # measure process latency when closed (close_time to now)
                                if kline.get("x"):
                                    now_ms = int(time.time() * 1000)
                                    close_time = kline.get("T", now_ms)
                                    PROCESS_LATENCY.labels(self.symbol).observe(max(0, (now_ms - close_time) / 1000.0))
                                    self.last_closed_kline_close_time = close_time
                                    # gap detection uses open_time of closed bar
                                    try:
                                        open_time = kline.get("t") or kline.get("t")  # Binance field 't'
                                        if open_time is not None and isinstance(open_time, int):
                                            # Late fill detection: closed bar whose open_time < last_closed_open_time
                                            if self._last_closed_open_time is not None and open_time < self._last_closed_open_time:
                                                try:
                                                    await upsert_kline(kline, ingestion_source="binance_ws_late")  # ensure persisted with source
                                                    LATE_FILL_TOTAL.labels(self.symbol).inc()
                                                except Exception:
                                                    pass
                                                # Adjust any in-memory gap segment that spans this open_time
                                                try:
                                                    interval_ms = self._interval_ms
                                                    updated = False
                                                    for seg in list(self._gaps):
                                                        seg_from = seg.get("from_open_time")
                                                        seg_to = seg.get("to_open_time")
                                                        if seg_from is None or seg_to is None:
                                                            continue
                                                        if open_time < seg_from or open_time > seg_to:
                                                            continue  # not inside this gap
                                                        remaining = seg.get("remaining_bars", seg.get("missing_bars", 0))
                                                        if remaining <= 0:
                                                            continue
                                                        # Determine candle index offset inside gap range
                                                        offset = (open_time - seg_from) // interval_ms
                                                        # Strategy: decrement remaining_bars by 1 and optionally split if not at edges.
                                                        seg["remaining_bars"] = max(0, remaining - 1)
                                                        # If bar not at edge and remaining large, we could split into two gap segments.
                                                        # Simple heuristic: split only if interior and both resulting spans have >=2 bars missing potential.
                                                        left_span = open_time - seg_from
                                                        right_span = seg_to - open_time
                                                        interior = seg_from < open_time < seg_to
                                                        if interior and seg["remaining_bars"] > 0 and left_span >= interval_ms and right_span >= interval_ms:
                                                            # Create new right segment (exclude the recovered bar position)
                                                            new_from = open_time + interval_ms
                                                            new_to = seg_to
                                                            # Estimate missing bars for right segment conservatively
                                                            right_missing = max(0, (new_to - new_from) // interval_ms + 1)
                                                            # Adjust current (left) segment to end before recovered bar
                                                            seg["to_open_time"] = open_time - interval_ms
                                                            # Recompute missing for left side (approx based on span)
                                                            left_missing = max(0, (seg["to_open_time"] - seg_from) // interval_ms + 1)
                                                            # Remaining bars distribution: we already consumed 1 recovered bar
                                                            # Assign based on proportional span lengths
                                                            if left_missing + right_missing > 0:
                                                                consumed = 1
                                                                # naive: scale remaining bars to new segments (minus consumed)
                                                                total_new_missing = left_missing + right_missing
                                                                # apportion remaining (after consumption) proportionally
                                                                rem_after = seg["remaining_bars"]
                                                                left_alloc = int(rem_after * (left_missing / total_new_missing)) if total_new_missing else 0
                                                                right_alloc = rem_after - left_alloc
                                                            else:
                                                                left_alloc = right_alloc = 0
                                                            seg["missing_bars"] = left_missing
                                                            seg["remaining_bars"] = left_alloc
                                                            new_seg = {
                                                                "from_open_time": new_from,
                                                                "to_open_time": new_to,
                                                                "missing_bars": right_missing,
                                                                "remaining_bars": right_alloc,
                                                                "detected_ts": seg.get("detected_ts", time.time()),  # inherit detection time for age
                                                            }
                                                            self._gaps.append(new_seg)
                                                        if seg.get("remaining_bars", 0) <= 0:
                                                            # Mark recovered in-memory and optionally in DB
                                                            gap_id = seg.get("id")
                                                            self._gaps.remove(seg)
                                                            if gap_id is not None:
                                                                asyncio.create_task(GapRepository.mark_recovered(gap_id))
                                                        updated = True
                                                    if updated:
                                                        self._refresh_gap_metrics()
                                                except Exception:
                                                    pass
                                            if self._last_closed_open_time is not None and open_time > self._last_closed_open_time:
                                                delta = open_time - self._last_closed_open_time
                                                if delta > self._interval_ms:  # missing segment
                                                    missing = (delta // self._interval_ms) - 1
                                                    gap_entry = {
                                                        "from_open_time": self._last_closed_open_time + self._interval_ms,
                                                        "to_open_time": open_time - self._interval_ms,
                                                        "missing_bars": int(missing),  # original missing count
                                                        "remaining_bars": int(missing),  # decremented as backfill recovers
                                                        "detected_ts": time.time(),
                                                    }
                                                    self._gaps.append(gap_entry)
                                                    # persist asynchronously (fire and forget)
                                                    asyncio.create_task(GapRepository.insert_gap(self.symbol.upper(), self.interval, gap_entry["from_open_time"], gap_entry["to_open_time"], gap_entry["missing_bars"]))
                                                    if len(self._gaps) > 200:
                                                        self._gaps.pop(0)
                                                    GAP_DETECTED.labels(self.symbol).inc()
                                                    self._refresh_gap_metrics()
                                            # Update last pointer only if this bar is the new chronological frontier
                                            if self._last_closed_open_time is None or open_time > self._last_closed_open_time:
                                                self._last_closed_open_time = open_time
                                    except Exception:
                                        pass
                                self.last_message_ts = time.time()
                                INGESTION_LAG_GAUGE.labels(self.symbol).set(0.0)
                                await self.on_kline(kline)
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
            except Exception:
                attempt += 1
                self._reconnect_attempts += 1
                if self._max_retries and attempt > self._max_retries:
                    break
                backoff = min(30, (2 ** min(attempt, 6)) + random.uniform(0, 1))
                await asyncio.sleep(backoff)
            finally:
                if self._flusher_task and not self._flusher_task.done():
                    self._flusher_task.cancel()
                    with contextlib.suppress(Exception):
                        await self._flusher_task
        await self.stop()

    async def stop(self) -> None:
        self._running = False
        if self._session:
            await self._session.close()
            self._session = None
        if self._task and not self._task.done():
            self._task.cancel()
        if self._flusher_task and not self._flusher_task.done():
            self._flusher_task.cancel()
            with contextlib.suppress(Exception):
                await self._flusher_task

    def status(self) -> Dict[str, Any]:
        # update lag gauge lazily if we have a timestamp
        if self.last_message_ts:
            INGESTION_LAG_GAUGE.labels(self.symbol).set(max(0.0, time.time() - self.last_message_ts))
        status = {
            "symbol": self.symbol,
            "interval": self.interval,
            "running": self._running,
            "buffer_size": len(self._buffer),
            "total_messages": self.total_messages,
            "total_closed": self.total_closed,
            "last_message_ts": self.last_message_ts,
            "last_closed_kline_close_time": self.last_closed_kline_close_time,
            "batch_size": self._batch_size,
            "flush_interval": self._flush_interval,
            "reconnect_attempts": self._reconnect_attempts,
            "last_flush_ts": self.last_flush_ts,
            "open_gap_segments": len(self._gaps),
        }
        # include aggregate gap stats if any
        if self._gaps:
            remaining = sum(g.get("remaining_bars", g.get("missing_bars", 0)) for g in self._gaps)
            status["gap_remaining_bars"] = remaining
            oldest = min(g.get("detected_ts", time.time()) for g in self._gaps)
            status["gap_oldest_age_seconds"] = time.time() - oldest
        return status

    def get_gaps(self) -> list[dict[str, Any]]:
        return list(self._gaps)

    def add_flush_listener(self, cb: Callable[[List[Dict[str, Any]]], Awaitable[None]]):
        self._flush_listeners.append(cb)

    async def hydrate_persisted(self):
        """Load existing open gaps from DB (only once) at startup so recovery continues after restart."""
        if self._loaded_persisted:
            return
        try:
            rows = await GapRepository.load_open(self.symbol.upper(), self.interval, limit=500)
            for r in rows:
                # convert DB row into in-memory format
                self._gaps.append({
                    "from_open_time": r["from_open_time"],
                    "to_open_time": r["to_open_time"],
                    "missing_bars": r["missing_bars"],
                    "remaining_bars": r["remaining_bars"],
                    "detected_ts": r["detected_at"].timestamp() if r.get("detected_at") else time.time(),
                    "id": r["id"],
                })
            self._loaded_persisted = True
            self._refresh_gap_metrics()
        except Exception:
            pass

async def main():
    consumer = KlineConsumer(symbol=cfg.symbol, interval=cfg.interval)
    await consumer.start()

if __name__ == "__main__":
    asyncio.run(main())
