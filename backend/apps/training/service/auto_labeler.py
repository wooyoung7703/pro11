from __future__ import annotations
import asyncio
import time
from typing import List, Dict, Any
from prometheus_client import Counter, Histogram, Gauge
from backend.common.config.base_config import load_config
from backend.common.db.connection import init_pool
from backend.apps.training.repository.inference_log_repository import InferenceLogRepository
from backend.apps.features.service.feature_service import FeatureService  # ensure schema exists
from backend.apps.training.service.bottom_labeler import label_for_created_ts

CFG = load_config()

AUTO_LABELER_RUNS = Counter("auto_labeler_runs_total", "Total automatic labeler scans")
AUTO_LABELER_LABELED = Counter("auto_labeler_labeled_total", "Total inference rows labeled automatically")
AUTO_LABELER_ERRORS = Counter("auto_labeler_errors_total", "Errors during automatic labeling")
AUTO_LABELER_LATENCY = Histogram("auto_labeler_latency_seconds", "Automatic labeler scan latency seconds", buckets=(0.005,0.01,0.025,0.05,0.1,0.25,0.5,1.0,2.0,5.0))
AUTO_LABELER_LAST_SUCCESS = Gauge("auto_labeler_last_success_timestamp", "Last successful labeler run (unix timestamp)")
AUTO_LABELER_LAST_ERROR = Gauge("auto_labeler_last_error_timestamp", "Last labeler error (unix timestamp)")
AUTO_LABELER_BACKLOG = Gauge("auto_labeler_backlog_unlabeled", "Approximate unlabeled candidate backlog older than min age")

# We derive realized labels using feature_snapshot.ret_1 sign for the *next* bar after inference creation.
# Strategy: For each unlabeled inference (created_at), find the earliest feature_snapshot with created_at > inference.created_at
# and use ret_1: label = 1 if ret_1 > 0 else 0 when ret_1 is not null.
# NOTE: feature_snapshot table has open_time/close_time, not created_at; we approximate by matching the earliest snapshot whose close_time epoch (ms) converted to seconds
# is >= inference.created_at timestamp. This assumes the scheduler runs shortly after kline close.

class AutoLabelerService:
    def __init__(self):
        self._interval = CFG.auto_labeler_interval
        self._min_age = CFG.auto_labeler_min_age_seconds
        self._batch_limit = CFG.auto_labeler_batch_limit
        self._repo = InferenceLogRepository()
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        if not CFG.auto_labeler_enabled or self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                # expected on cancellation
                pass
            except Exception:
                # swallow all shutdown-time errors
                pass
            finally:
                self._task = None

    async def run_once(self, *, min_age_seconds: int | None = None, limit: int | None = None) -> Dict[str, Any]:
        t0 = time.perf_counter()
        AUTO_LABELER_RUNS.inc()
        pool = await init_pool()
        try:
            min_age = int(self._min_age if min_age_seconds is None else max(0, int(min_age_seconds)))
            batch_limit = int(self._batch_limit if limit is None else max(1, int(limit)))
            candidates = await self._repo.fetch_unlabeled_candidates(min_age_seconds=min_age, limit=batch_limit)
            AUTO_LABELER_BACKLOG.set(len(candidates))
            if not candidates:
                AUTO_LABELER_LATENCY.observe(time.perf_counter() - t0)
                return {"status": "no_candidates"}
            # Fetch mapping from feature_snapshot using close_time
            # We pull enough recent snapshots to cover possible times
            async with pool.acquire() as conn:
                # Ensure the feature snapshot schema (meta/value tables) exists before querying
                try:
                    svc = FeatureService(CFG.symbol, CFG.kline_interval)
                    await svc._ensure_schema(conn)  # idempotent
                except Exception:
                    # Don't fail here; the subsequent query will surface a concrete error if schema truly missing
                    pass
                # Get recent snapshots (limit heuristic = batch_limit * 3) from long schema
                snap_rows = await conn.fetch(
                    """
                    SELECT m.open_time, m.close_time, v.feature_value AS ret_1
                    FROM feature_snapshot_meta m
                    LEFT JOIN feature_snapshot_value v
                      ON v.snapshot_id = m.id AND v.feature_name = 'ret_1'
                    WHERE m.symbol = $1 AND m.interval = $2
                    ORDER BY m.open_time DESC
                    LIMIT $3
                    """,
                    CFG.symbol,
                    CFG.kline_interval,
                    batch_limit * 3,
                )
            # Build chronological list
            snaps = list(reversed(snap_rows))
            labeled: List[Dict[str, Any]] = []
            # Fetch recent OHLCV to evaluate bottom labels if needed
            from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as fetch_ohlcv_recent
            ohlcv_rows = await fetch_ohlcv_recent(CFG.symbol, CFG.kline_interval, limit=max(200, min(2000, batch_limit * (CFG.bottom_lookahead + 5))))
            # Ensure chronological order for labeler convenience
            ohlcv_rows = list(sorted(ohlcv_rows, key=lambda r: r.get('open_time', 0)))
            for c in candidates:
                created_ts = c["created_at"].timestamp()  # datetime -> float seconds
                # Default: direction label via ret_1 sign
                label_val = None
                extra_target = None
                try:
                    # We do not have extra in this query; fall back to global target config
                    extra_target = getattr(CFG, 'training_target_type', 'direction')
                except Exception:
                    extra_target = 'direction'
                if str(extra_target).lower() == 'bottom':
                    # Compute bottom-event label using OHLCV window and configured params
                    try:
                        lb = label_for_created_ts(
                            ohlcv_rows,
                            created_ts,
                            lookahead=int(getattr(CFG, 'bottom_lookahead', 30) or 30),
                            drawdown=float(getattr(CFG, 'bottom_drawdown', 0.005) or 0.005),
                            rebound=float(getattr(CFG, 'bottom_rebound', 0.003) or 0.003),
                        )
                        if lb is not None:
                            label_val = int(lb)
                    except Exception:
                        label_val = None
                if label_val is None:
                    # Fallback: direction realized via ret_1 next bar
                    for s in snaps:
                        close_sec = s["close_time"] / 1000.0
                        if close_sec >= created_ts:
                            ret1 = s["ret_1"]
                            if ret1 is not None:
                                try:
                                    retf = float(ret1)
                                    label_val = 1 if retf > 0 else 0
                                except Exception:
                                    label_val = None
                            break
                if label_val is not None:
                    labeled.append({"id": c["id"], "realized": label_val})
            if labeled:
                updated = await self._repo.update_realized_batch(labeled)
                AUTO_LABELER_LABELED.inc(updated)
                AUTO_LABELER_LAST_SUCCESS.set(time.time())
                AUTO_LABELER_LATENCY.observe(time.perf_counter() - t0)
                return {"status": "ok", "labeled": updated, "candidates": len(candidates)}
            else:
                AUTO_LABELER_LATENCY.observe(time.perf_counter() - t0)
                return {"status": "pending", "candidates": len(candidates)}
        except Exception as e:
            AUTO_LABELER_ERRORS.inc()
            AUTO_LABELER_LAST_ERROR.set(time.time())
            AUTO_LABELER_LATENCY.observe(time.perf_counter() - t0)
            # Surface a minimal error string for easier diagnosis (e.g., missing tables)
            return {"status": "error", "error": str(e)}

    async def _loop(self):
        while self._running:
            await self.run_once()
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                # graceful exit on cancellation during sleep
                break

_service: AutoLabelerService | None = None

def get_auto_labeler_service() -> AutoLabelerService:
    global _service
    if _service is None:
        _service = AutoLabelerService()
    return _service

__all__ = ["get_auto_labeler_service", "AutoLabelerService"]
