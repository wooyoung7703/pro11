from __future__ import annotations
import asyncio
import contextlib
import time
from typing import Any, Dict, List
from prometheus_client import Counter, Histogram, Gauge
from backend.common.config.base_config import load_config
from backend.apps.training.repository.inference_log_repository import InferenceLogRepository

CFG = load_config()

QUEUE_SIZE = Gauge("inference_log_queue_size", "Current size of inference log queue")
QUEUE_DROPS = Counter("inference_log_queue_dropped_total", "Dropped inference log entries due to full queue")
QUEUE_FLUSHES = Counter("inference_log_queue_flush_total", "Number of queue flush operations")
QUEUE_FLUSH_LATENCY = Histogram("inference_log_queue_flush_latency_seconds", "Flush latency seconds", buckets=(0.001,0.005,0.01,0.025,0.05,0.1,0.25,0.5,1.0,2.0,5.0))
QUEUE_FLUSH_RECORDS = Counter("inference_log_queue_flushed_records_total", "Number of records flushed from queue")
QUEUE_ADAPTATIONS = Counter("inference_log_queue_adaptations_total", "Number of adaptive interval adjustments", labelnames=["direction"])  # direction: faster|slower
QUEUE_FLUSH_INTERVAL = Gauge("inference_log_queue_flush_interval_seconds", "Current effective flush interval seconds")
QUEUE_ENQUEUE_ATTEMPTS = Counter("inference_log_queue_enqueue_attempts_total", "Total enqueue attempts")
QUEUE_ENQUEUE_SUCCESS = Counter("inference_log_queue_enqueue_success_total", "Successful enqueue operations")
QUEUE_ENQUEUE_ERRORS = Counter("inference_log_queue_enqueue_errors_total", "Enqueue errors (exceptions)")

class InferenceLogQueue:
    def __init__(self, max_size: int, flush_batch: int, flush_interval: float):
        self._q: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._max = max_size
        self._batch = flush_batch
        self._base_interval = flush_interval
        # dynamic interval starts at base
        self._interval = flush_interval
        self._running = False
        self._task: asyncio.Task | None = None
        self._repo = InferenceLogRepository()
        # adaptive config
        self._min_interval = CFG.inference_log_flush_min_interval
        self._high_watermark = CFG.inference_log_flush_high_watermark
        self._low_watermark = CFG.inference_log_flush_low_watermark
        QUEUE_FLUSH_INTERVAL.set(self._interval)

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        # 신호 먼저 내려 loop 가 다음 sleep 후 정상 종료하도록
        self._running = False
        # loop 가 sleep 중이면 즉시 깨우기 위해 cancel -> 이후 graceful flush
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        # 모든 잔여 데이터 배치 플러시
        await self._flush_all()

    async def enqueue(self, item: Dict[str, Any]):
        QUEUE_ENQUEUE_ATTEMPTS.inc()
        try:
            async with self._lock:
                if len(self._q) >= self._max:
                    QUEUE_DROPS.inc()
                    return False
                self._q.append(item)
                QUEUE_SIZE.set(len(self._q))
                QUEUE_ENQUEUE_SUCCESS.inc()
                return True
        except Exception:
            QUEUE_ENQUEUE_ERRORS.inc()
            return False

    async def _run(self):
        try:
            while self._running:
                try:
                    await asyncio.sleep(self._interval)
                except asyncio.CancelledError:
                    # stop() 에서 cancel 로 깨운 경우 즉시 루프 탈출
                    break
                try:
                    await self._flush_batch()
                    await self._maybe_adapt_interval()
                except Exception:
                    pass
        finally:
            # 종료 직전 한 번 더 배치 flush (stop()의 flush_all 이전에 잔여 소량 줄이기)
            with contextlib.suppress(Exception):
                await self._flush_batch()

    async def _maybe_adapt_interval(self):
        # Decide if we should speed up or slow down based on occupancy ratio
        async with self._lock:
            q_len = len(self._q)
            max_size = self._max
        ratio = q_len / max_size if max_size > 0 else 0.0
        new_interval = self._interval
        direction = None
        if ratio >= self._high_watermark and self._interval > self._min_interval:
            # speed up (halve interval but not below min)
            new_interval = max(self._min_interval, self._interval / 2.0)
            direction = "faster" if new_interval < self._interval else None
        elif ratio <= self._low_watermark and self._interval < self._base_interval:
            # slow down (increase interval toward base by 50%)
            gap = self._base_interval - self._interval
            if gap > 0:
                new_interval = min(self._base_interval, self._interval + gap * 0.5)
                direction = "slower" if new_interval > self._interval else None
        if new_interval != self._interval:
            self._interval = new_interval
            QUEUE_FLUSH_INTERVAL.set(self._interval)
            if direction:
                QUEUE_ADAPTATIONS.labels(direction=direction).inc()

    async def _flush_batch(self):
        async with self._lock:
            if not self._q:
                return
            batch = self._q[: self._batch]
            del self._q[: self._batch]
            QUEUE_SIZE.set(len(self._q))
        if not batch:
            return
        t0 = time.perf_counter()
        await self._bulk_insert(batch)
        dt = time.perf_counter() - t0
        QUEUE_FLUSH_LATENCY.observe(dt)
        QUEUE_FLUSHES.inc()
        QUEUE_FLUSH_RECORDS.inc(len(batch))

    async def _flush_all(self):
        while True:
            async with self._lock:
                if not self._q:
                    QUEUE_SIZE.set(0)
                    return
                batch = self._q[: self._batch]
                del self._q[: self._batch]
                QUEUE_SIZE.set(len(self._q))
            await self._bulk_insert(batch)
            QUEUE_FLUSHES.inc()
            QUEUE_FLUSH_RECORDS.inc(len(batch))

    async def _bulk_insert(self, items: List[Dict[str, Any]]):
        if not items:
            return
        # Bulk insert uses executemany (no RETURNING) for reduced round-trips
        await self._repo.bulk_insert(items)

    # --- Admin/diagnostic helpers ---
    async def flush_now(self) -> int:
        """Force a single batch flush. Returns number of records flushed."""
        async with self._lock:
            if not self._q:
                return 0
            batch = self._q[: self._batch]
            del self._q[: self._batch]
            QUEUE_SIZE.set(len(self._q))
        await self._bulk_insert(batch)
        QUEUE_FLUSHES.inc()
        QUEUE_FLUSH_RECORDS.inc(len(batch))
        return len(batch)

    def status(self) -> Dict[str, Any]:
        """Lightweight status snapshot for admin endpoint."""
        return {
            "running": self._running,
            "queue_len": len(self._q),
            "max_size": self._max,
            "flush_batch": self._batch,
            "interval": self._interval,
            "base_interval": self._base_interval,
            "min_interval": self._min_interval,
            "high_watermark": self._high_watermark,
            "low_watermark": self._low_watermark,
        }

# Singleton accessor
_LOG_QUEUE: InferenceLogQueue | None = None

def get_inference_log_queue() -> InferenceLogQueue:
    global _LOG_QUEUE
    if _LOG_QUEUE is None:
        _LOG_QUEUE = InferenceLogQueue(
            max_size=CFG.inference_log_queue_max,
            flush_batch=CFG.inference_log_flush_batch,
            flush_interval=CFG.inference_log_flush_interval,
        )
    return _LOG_QUEUE

__all__ = ["get_inference_log_queue", "InferenceLogQueue"]
