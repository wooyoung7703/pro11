from __future__ import annotations
import asyncio, time, heapq, contextlib
from typing import Any, List
from prometheus_client import Gauge, Counter
from backend.apps.ingestion.repository.gap_repository import GapRepository
from backend.apps.ingestion.backfill.gap_backfill_service import GapBackfillService

ORCH_ACTIVE = Gauge("kline_gap_orchestrator_active", "1 when gap orchestrator running", ["symbol"])
ORCH_QUEUE_SIZE = Gauge("kline_gap_orchestrator_queue_size", "Current orchestrator priority queue size", ["symbol"])
ORCH_MERGE_EVENTS = Counter("kline_gap_merge_events_total", "Gap merge events", ["symbol"])

class GapOrchestratorService:
    """Enhanced orchestrator that:
      - Periodically loads open gap segments from DB
      - Maintains a priority queue (largest remaining_bars first, then earliest detected)
      - Delegates actual recovery to existing GapBackfillService (single-gap) or directly replicates logic in future.
    For v1 we reuse GapBackfillService semantics by fabricating consumer._gaps list subset.
    """
    def __init__(self, consumer, interval: str, poll_interval: float = 30.0, concurrency: int = 2):
        self.consumer = consumer
        self.symbol = consumer.symbol
        self.interval = interval
        self.poll_interval = poll_interval
        self.concurrency = concurrency
        self._task: asyncio.Task | None = None
        self._running = False
        self._workers: list[asyncio.Task] = []
        self._queue: list[tuple[int, float, dict[str, Any]]] = []  # (-remaining_bars, detected_at_ts, gap)

    async def start(self):
        if self._running:
            return
        self._running = True
        ORCH_ACTIVE.labels(self.symbol).set(1)
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        self._running = False
        ORCH_ACTIVE.labels(self.symbol).set(0)
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(Exception):
                await self._task
        for w in self._workers:
            if not w.done():
                w.cancel()
        self._workers.clear()

    async def _run_loop(self):
        while self._running:
            try:
                await self._refresh_queue()
                await self._ensure_workers()
            except asyncio.CancelledError:  # pragma: no cover
                raise
            except Exception:
                pass
            await asyncio.sleep(self.poll_interval)

    async def _refresh_queue(self):
        rows = await GapRepository.load_open(self.symbol.upper(), self.interval, limit=500)
        # rebuild queue
        self._queue.clear()
        now = time.time()
        for r in rows:
            # priority: remaining_bars DESC, detected_at ASC
            detected_ts = r.get("detected_at").timestamp() if r.get("detected_at") else now
            remaining = r.get("remaining_bars", r.get("missing_bars", 0))
            heapq.heappush(self._queue, (-int(remaining), detected_ts, r))
        ORCH_QUEUE_SIZE.labels(self.symbol).set(len(self._queue))

    async def _ensure_workers(self):
        # prune finished
        self._workers = [w for w in self._workers if not w.done()]
        needed = self.concurrency - len(self._workers)
        for _ in range(needed):
            if not self._queue:
                break
            _ = self._queue[0]  # peek
            self._workers.append(asyncio.create_task(self._worker()))

    async def _worker(self):
        while self._running:
            if not self._queue:
                return
            _, _, gap = heapq.heappop(self._queue)
            ORCH_QUEUE_SIZE.labels(self.symbol).set(len(self._queue))
            # Hand off to GapBackfillService style single recovery by constructing ephemeral gap list
            try:
                # Reuse existing backfill service instance if available
                service = getattr(self.consumer, "_backfill_service", None)
                if isinstance(service, GapBackfillService):
                    await service._recover_gap(gap)  # noqa: SLF001 (intentional reuse)
                else:
                    # Fallback: simple direct recovery not implemented yet
                    pass
            except Exception:
                pass
            await asyncio.sleep(0)  # yield

__all__ = ["GapOrchestratorService"]
