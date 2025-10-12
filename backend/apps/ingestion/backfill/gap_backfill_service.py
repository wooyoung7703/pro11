from __future__ import annotations
import asyncio
import time
import os
import logging
from typing import Any, List
import aiohttp
from prometheus_client import Counter, Gauge, Histogram
from backend.common.config.base_config import load_config
from backend.apps.ingestion.repository.ohlcv_repository import bulk_upsert  # canonical table
from backend.apps.ingestion.repository.gap_repository import GapRepository

cfg = load_config()

logger = logging.getLogger(__name__)
_LOG_FULL_BINANCE = os.getenv("BACKFILL_LOG_BINANCE", "0").lower() in {"1","true","yes","on"}

BACKFILL_ATTEMPTS = Counter("kline_gap_backfill_attempts_total", "Gap backfill attempts", ["symbol"])  # total segments attempted
BACKFILL_RECOVERED = Counter("kline_gap_recovered_total", "Recovered missing OHLCV bars", ["symbol"])  # bars recovered
BACKFILL_SEGMENTS = Counter("kline_gap_recovered_segments_total", "Gap segments fully recovered", ["symbol"])  # segments fully recovered
BACKFILL_ERRORS = Counter("kline_gap_backfill_errors_total", "Backfill segment errors", ["symbol"])  # failed segments
BACKFILL_LATENCY = Histogram("kline_gap_backfill_latency_seconds", "Time to backfill one gap segment", ["symbol"])  # per segment latency
BACKFILL_LAST_RUN = Gauge("kline_gap_backfill_last_run_timestamp", "Unix ts of last backfill cycle", ["symbol"])  # cycle timestamp
BACKFILL_ACTIVE = Gauge("kline_gap_backfill_active", "1 when backfill loop running", ["symbol"])  # loop active flag
BACKFILL_QUEUE_SIZE = Gauge("kline_gap_backfill_queue_size", "Queued gap segments pending recovery", ["symbol"])  # current queue size
GAP_MTTR_SECONDS = Histogram("kline_gap_mttr_seconds", "Gap mean time to recovery (seconds)", ["symbol"])  # detection -> full recovery

# Canonical OHLCV meta gauges (updated opportunistically after backfill events)
OHLCV_COMPLETENESS = Gauge(
    "ohlcv_candles_completeness_percent",
    "Canonical OHLCV completeness ratio (0-1) based on earliest/latest span",
    ["symbol", "interval"],
)
OHLCV_LARGEST_GAP_BARS = Gauge(
    "ohlcv_candles_largest_gap_bars",
    "Largest detected gap (missing bars) within recent sample window",
    ["symbol", "interval"],
)
OHLCV_LARGEST_GAP_SPAN_MS = Gauge(
    "ohlcv_candles_largest_gap_span_ms",
    "Largest gap span (milliseconds beyond one interval) within recent sample window",
    ["symbol", "interval"],
)

# 365d trailing completeness (strict) gauge
OHLCV_COMPLETENESS_365D = Gauge(
    "ohlcv_candles_completeness_365d_percent",
    "Trailing 365d completeness ratio (0-1) based on last 365 days expected bar count",
    ["symbol", "interval"],
)

# Meta refresh error counter (best-effort 관측 실패 카운트)
OHLCV_META_REFRESH_ERRORS = Counter(
    "ohlcv_meta_refresh_errors_total",
    "Errors occurred while refreshing canonical OHLCV meta gauges",
    ["symbol", "interval"],
)

BINANCE_FUTURES_REST = "https://fapi.binance.com"

class GapBackfillService:
    """Background task that periodically inspects detected gap segments from the live
    consumer (in-memory) and attempts REST historical fetch to fill missing bars.

    Strategy v1 (simple, synchronous within segment):
    - Poll consumer.get_gaps()
    - Maintain internal recovery cursor set to attempt oldest first (FIFO)
    - For each gap: fetch historical klines via REST endpoint (/fapi/v1/klines)
      in batches (max 1500 per request) until range covered or limit reached
    - Upsert recovered bars (closed bars only)
    - On full success: increment recovered bars + segment counters
    - On partial/exception: increment attempts + error counter, segment stays in queue (retry later)

    Limitations:
    - Does not yet persist unrecovered gap metadata to DB (memory only)
    - No rate limit adaptation beyond naive sleep
    - Assumes 1m interval (uses cfg.interval ms to step); multi-interval logic minimal
    """

    def __init__(self, consumer, interval: str, poll_interval: float = 30.0, max_batch: int = 1500):
        self.consumer = consumer
        self.symbol = consumer.symbol
        self.interval = interval
        self.poll_interval = poll_interval
        self.max_batch = max_batch
        self._task: asyncio.Task | None = None
        self._running = False
        self._session: aiohttp.ClientSession | None = None
        # meta refresh throttling
        self._last_meta_refresh: float = 0.0
        self._meta_refresh_min_interval = 5.0  # seconds

    async def start(self):
        logger.info("woo")
        if self._running:
            return
        self._running = True
        BACKFILL_ACTIVE.labels(self.symbol).set(1)
        self._session = aiohttp.ClientSession()
        # 최초 메타 즉시 계산 (강제) -> metrics 초기값 제공
        try:
            await self._refresh_canonical_meta(force=True)
        except Exception:
            pass
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        self._running = False
        BACKFILL_ACTIVE.labels(self.symbol).set(0)
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
        if self._session:
            await self._session.close()
            self._session = None

    async def _run_loop(self):
        while self._running:
            try:
                # 주기적(쓰로틀 적용) 메타 갱신: 갭 유무와 무관하게 최신화 유지
                try:
                    await self._refresh_canonical_meta(force=False)
                except Exception:
                    pass
                await self._cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                # swallow & continue
                pass
            await asyncio.sleep(self.poll_interval)

    async def _cycle(self):
        BACKFILL_LAST_RUN.labels(self.symbol).set(time.time())
        gaps = list(self.consumer.get_gaps())
        BACKFILL_QUEUE_SIZE.labels(self.symbol).set(len(gaps))
        logger.info("woo")
        if not gaps:
            return
        # attempt oldest (FIFO): consumer stores in detection order
        gap = gaps[0]
        await self._recover_gap(gap)

    async def _recover_gap(self, gap: dict[str, Any]):
        start_time = time.perf_counter()
        BACKFILL_ATTEMPTS.labels(self.symbol).inc()
        from_open = gap["from_open_time"]
        to_open = gap["to_open_time"]
        interval_ms = self.consumer._interval_ms  # internal field
        missing_bars = gap.get("missing_bars", 0)
        # Binance REST /fapi/v1/klines params: symbol, interval, startTime, endTime, limit
        if not self._session:
            return
        url = f"{BINANCE_FUTURES_REST}/fapi/v1/klines"
        params = {
            "symbol": self.symbol.upper(),
            "interval": self.interval,
            "startTime": from_open,
            "endTime": to_open + interval_ms,  # inclusive end boundary
            "limit": min(self.max_batch, missing_bars + 2),  # safety margin
        }
        recovered = 0
        try:
            req_started = time.perf_counter()
            async with self._session.get(url, params=params, timeout=10) as resp:
                latency = time.perf_counter() - req_started
                if resp.status != 200:
                    BACKFILL_ERRORS.labels(self.symbol).inc()
                    logger.warning(
                        "gap_backfill_http_error symbol=%s status=%s missing_bars=%s params_start=%s params_end=%s latency_ms=%.2f",
                        self.symbol,
                        resp.status,
                        missing_bars,
                        params.get("startTime"),
                        params.get("endTime"),
                        latency*1000,
                    )
                    return
                data = await resp.json()
                if not isinstance(data, list):
                    logger.warning("gap_backfill_unexpected_payload_type symbol=%s type=%s", self.symbol, type(data))
                else:
                    batch_len = len(data)
                    sample_first = int(data[0][0]) if batch_len else None
                    sample_last = int(data[-1][0]) if batch_len else None
                    logger.debug(
                        "gap_backfill_fetch symbol=%s interval=%s batch=%s missing_est=%s from=%s to=%s first_ot=%s last_ot=%s latency_ms=%.2f",
                        self.symbol,
                        self.interval,
                        batch_len,
                        missing_bars,
                        from_open,
                        to_open,
                        sample_first,
                        sample_last,
                        latency*1000,
                    )
                    if _LOG_FULL_BINANCE:
                        # Careful: can be large; only log when explicitly enabled.
                        logger.info(
                            "gap_backfill_payload_dump symbol=%s interval=%s data=%s",
                            self.symbol,
                            self.interval,
                            data,
                        )
                # data: list of lists
                payload = []
                for item in data:
                    # item fields (Binance): [ openTime, open, high, low, close, volume, closeTime, quoteVolume, trades, takerBuyBase, takerBuyQuote, ignore ]
                    open_time = int(item[0])
                    if open_time < from_open or open_time > to_open:
                        continue
                    k = {
                        "t": open_time,
                        "T": int(item[6]),
                        "s": self.symbol.upper(),
                        "i": self.interval,
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

                    logger.info("gap_=%s type=%s", self.symbol, k)
                    payload.append(k)
                if payload:
                    await bulk_upsert(payload, ingestion_source="gap_backfill")
                    recovered = len(payload)
        except Exception:
            BACKFILL_ERRORS.labels(self.symbol).inc()
            return
        finally:
            BACKFILL_LATENCY.labels(self.symbol).observe(time.perf_counter() - start_time)
        if recovered >= missing_bars:
            BACKFILL_RECOVERED.labels(self.symbol).inc(recovered)
            BACKFILL_SEGMENTS.labels(self.symbol).inc()
            # remove gap from consumer list (in-place mutate) naive approach
            # (Better: provide consumer method) – for now rebuild excluding first
            current = self.consumer._gaps  # access internal list intentionally
            if current and current[0] is gap:
                current.pop(0)
            BACKFILL_QUEUE_SIZE.labels(self.symbol).set(len(self.consumer._gaps))
            # MTTR observe (seconds) if detected_ts available
            try:
                detected_ts = gap.get("detected_ts") or gap.get("detected_at")
                if detected_ts:
                    now = time.time()
                    # detected_ts may be datetime or epoch float
                    if hasattr(detected_ts, 'timestamp'):
                        detected_sec = detected_ts.timestamp()  # type: ignore
                    else:
                        detected_sec = float(detected_ts) if detected_ts > 1e10 else float(detected_ts)  # heuristic
                    delta_sec = max(0.0, now - detected_sec)
                    GAP_MTTR_SECONDS.labels(self.symbol).observe(delta_sec)
            except Exception:
                pass
            # refresh aggregate gap gauges
            try:
                self.consumer._refresh_gap_metrics()
                # force meta refresh (full recovery likely changed completeness or largest gap)
                await self._refresh_canonical_meta(force=True)
                # mark recovered in DB if id present
                gap_id = gap.get("id")
                if gap_id is not None:
                    await GapRepository.mark_recovered(gap_id)
                # --- WebSocket repair broadcast (full recovery) ---
                try:
                    from backend.apps.api.main import ohlcv_ws_manager  # lazy import to avoid cycles
                    if ohlcv_ws_manager is not None:
                        # Recovered klines already upserted (payload variable). Convert to WS candle schema.
                        repaired_candles = []
                        interval_ms = self.consumer._interval_ms
                        for k in payload:
                            # payload items are raw Binance kline dicts (kline consumer style)
                            ot = int(k.get("t"))
                            ct = int(k.get("T", ot + interval_ms - 1))
                            repaired_candles.append({
                                "open_time": ot,
                                "close_time": ct,
                                "open": k.get("o"),
                                "high": k.get("h"),
                                "low": k.get("l"),
                                "close": k.get("c"),
                                "volume": k.get("v"),
                                "trades": k.get("n"),
                                "is_closed": True,
                            })
                        # meta_delta: completeness / largest gap gauges AFTER refresh (read from gauges if feasible)
                        meta_delta = {}
                        try:
                            # We can recompute minimally using refreshed metrics rather than scanning again
                            # Simplicity: call internal refresh again (fast due to throttle override previously) then recompute expected fields
                            # Already forced refresh above; compute completeness again using same logic reused in refresh.
                            # To avoid duplicating DB logic, we build a lightweight recompute using _refresh_canonical_meta side-effects
                            # Here just mark delta presence so client can trigger UI updates.
                            meta_delta["completeness_percent"] = None  # explicit placeholder (client may refetch meta)
                        except Exception:
                            pass
                        await ohlcv_ws_manager.broadcast_repair(repaired_candles, meta_delta or None)
                except Exception:
                    pass
            except Exception:
                pass
        elif recovered > 0:
            BACKFILL_RECOVERED.labels(self.symbol).inc(recovered)
            # partial recovery: decrement remaining_bars and refresh gauges
            try:
                rem = gap.get("remaining_bars", gap.get("missing_bars", 0))
                new_rem = max(0, rem - recovered)
                gap["remaining_bars"] = new_rem
                self.consumer._refresh_gap_metrics()
                # opportunistic (throttled) meta refresh
                await self._refresh_canonical_meta(force=False)
                gap_id = gap.get("id")
                if gap_id is not None:
                    await GapRepository.partial_recover(gap_id, new_rem, recovered)
                # --- WebSocket repair broadcast (partial recovery) ---
                try:
                    from backend.apps.api.main import ohlcv_ws_manager
                    if ohlcv_ws_manager is not None and payload:
                        repaired_candles = []
                        interval_ms = self.consumer._interval_ms
                        for k in payload:
                            ot = int(k.get("t"))
                            ct = int(k.get("T", ot + interval_ms - 1))
                            repaired_candles.append({
                                "open_time": ot,
                                "close_time": ct,
                                "open": k.get("o"),
                                "high": k.get("h"),
                                "low": k.get("l"),
                                "close": k.get("c"),
                                "volume": k.get("v"),
                                "trades": k.get("n"),
                                "is_closed": True,
                            })
                        meta_delta = {"remaining_gap_bars": new_rem}
                        await ohlcv_ws_manager.broadcast_repair(repaired_candles, meta_delta)
                except Exception:
                    pass
            except Exception:
                pass

    async def _refresh_canonical_meta(self, force: bool = False, sample_for_gap: int = 2000):
        """Compute canonical OHLCV meta and update Prometheus gauges.

        Mirrors logic in /api/ohlcv/meta (earliest/latest/count/completeness + largest gap in
        recent sample window) with throttling to reduce DB load.

        Args:
            force: If True bypass rate limit.
            sample_for_gap: number of most recent rows to scan for largest gap.
        """
        now = time.time()
        if (not force) and (now - self._last_meta_refresh < self._meta_refresh_min_interval):
            return
        # import lazily to avoid circulars
        try:
            from backend.common.db.connection import init_pool as _init_pool
            pool = await _init_pool()
            if pool is None:
                return
            interval_ms = self.consumer._interval_ms
            sym_u = self.symbol.upper()
            itv = self.interval
            earliest_open = None
            latest_open = None
            total_count = 0
            largest_gap_bars = 0
            largest_gap_span_ms = 0
            completeness_percent = None
            completeness_365d = None
            async with pool.acquire() as conn:  # type: ignore
                row = await conn.fetchrow(
                    """
                    SELECT MIN(open_time) AS earliest, MAX(open_time) AS latest, COUNT(*) AS cnt
                    FROM ohlcv_candles
                    WHERE symbol=$1 AND interval=$2
                    """,
                    sym_u, itv,
                )
                if row and row["cnt"] > 0:
                    earliest_open = int(row["earliest"]) if row["earliest"] is not None else None
                    latest_open = int(row["latest"]) if row["latest"] is not None else None
                    total_count = int(row["cnt"]) if row["cnt"] is not None else 0
                    if earliest_open is not None and latest_open is not None and latest_open >= earliest_open:
                        expected = ((latest_open - earliest_open) // interval_ms) + 1
                        if expected > 0:
                            completeness_percent = total_count / expected
                if total_count > 1:
                    limit_scan = min(sample_for_gap, total_count)
                    rows_scan = await conn.fetch(
                        """
                        SELECT open_time FROM ohlcv_candles
                        WHERE symbol=$1 AND interval=$2
                        ORDER BY open_time DESC
                        LIMIT $3
                        """,
                        sym_u, itv, limit_scan,
                    )
                    scan_list = [int(r["open_time"]) for r in rows_scan]
                    scan_list.reverse()
                    prev = None
                    for ot in scan_list:
                        if prev is not None:
                            delta = ot - prev
                            if delta > interval_ms:
                                missing_bars = (delta // interval_ms) - 1
                                if missing_bars > largest_gap_bars:
                                    largest_gap_bars = missing_bars
                                    largest_gap_span_ms = delta - interval_ms
                        prev = ot
                # trailing 365d completeness if latest_open available
                if latest_open is not None:
                    window_start = latest_open - (365 * 24 * 60 * 60 * 1000)
                    row_365 = None
                    try:
                        async with pool.acquire() as conn2:  # type: ignore
                            row_365 = await conn2.fetchrow(
                                """
                                SELECT COUNT(*) AS cnt FROM ohlcv_candles
                                WHERE symbol=$1 AND interval=$2 AND open_time BETWEEN $3 AND $4
                                """,
                                sym_u, itv, window_start, latest_open,
                            )
                    except Exception:
                        row_365 = None
                    if row_365 and row_365["cnt"] is not None:
                        bars_365 = int(row_365["cnt"])
                        expected_365 = ((latest_open - window_start) // interval_ms) + 1
                        if expected_365 > 0:
                            completeness_365d = bars_365 / expected_365
            # update gauges (None guarded)
            if completeness_percent is not None:
                OHLCV_COMPLETENESS.labels(sym_u, itv).set(completeness_percent)
            if completeness_365d is not None:
                OHLCV_COMPLETENESS_365D.labels(sym_u, itv).set(completeness_365d)
            OHLCV_LARGEST_GAP_BARS.labels(sym_u, itv).set(largest_gap_bars)
            OHLCV_LARGEST_GAP_SPAN_MS.labels(sym_u, itv).set(largest_gap_span_ms)
            self._last_meta_refresh = now
        except Exception:
            # metrics best-effort: 에러 카운터 증가 후 조용히 종료
            try:
                OHLCV_META_REFRESH_ERRORS.labels(self.symbol.upper(), self.interval).inc()
            except Exception:
                pass
            return

__all__ = ["GapBackfillService"]
