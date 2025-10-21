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
        # Optional runtime overrides for label parameters (applied when run_once doesn't receive explicit overrides)
        self._L_override: int | None = None
        self._DD_override: float | None = None
        self._RB_override: float | None = None

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

    # Runtime configuration updates (affects background loop and subsequent run_once calls without explicit overrides)
    def apply_runtime_config(
        self,
        *,
        interval: float | None = None,
        min_age_seconds: int | None = None,
        batch_limit: int | None = None,
        lookahead: int | None = None,
        drawdown: float | None = None,
        rebound: float | None = None,
    ) -> None:
        if isinstance(interval, (int, float)) and float(interval) > 0:
            self._interval = float(interval)
        if isinstance(min_age_seconds, int) and min_age_seconds >= 0:
            self._min_age = int(min_age_seconds)
        if isinstance(batch_limit, int) and batch_limit > 0:
            self._batch_limit = int(batch_limit)
        if isinstance(lookahead, int) and lookahead > 0:
            self._L_override = int(lookahead)
        if isinstance(drawdown, (int, float)) and float(drawdown) > 0:
            self._DD_override = float(drawdown)
        if isinstance(rebound, (int, float)) and float(rebound) > 0:
            self._RB_override = float(rebound)

    async def run_once(
        self,
        *,
        min_age_seconds: int | None = None,
        limit: int | None = None,
        lookahead: int | None = None,
        drawdown: float | None = None,
        rebound: float | None = None,
        only_target: str | None = None,
        only_symbol: str | None = None,
        only_interval: str | None = None,
    ) -> Dict[str, Any]:
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
            # Resolve override params with CFG defaults
            # Determine effective label parameters (explicit override > runtime override > CFG)
            L = int(
                lookahead
                if lookahead is not None
                else (self._L_override if self._L_override is not None else getattr(CFG, 'bottom_lookahead', 30))
            )
            DD = float(
                drawdown
                if drawdown is not None
                else (self._DD_override if self._DD_override is not None else getattr(CFG, 'bottom_drawdown', 0.005))
            )
            RB = float(
                rebound
                if rebound is not None
                else (self._RB_override if self._RB_override is not None else getattr(CFG, 'bottom_rebound', 0.003))
            )

            labeled: List[Dict[str, Any]] = []
            # Group candidates by (symbol, interval, target) to fetch appropriate OHLCV batches
            groups: dict[tuple[str, str, str], list[Any]] = {}
            import json as _json
            # Optional filtering by target/symbol/interval
            filtered: list[Any] = []
            for c in candidates:
                sym = str(c.get("symbol") or CFG.symbol)
                itv = str(c.get("interval") or CFG.kline_interval)
                extra = c.get("extra") or {}
                # extra may be JSON text depending on DB driver/column type; parse defensively
                if isinstance(extra, str):
                    try:
                        extra = _json.loads(extra)
                    except Exception:
                        extra = {}
                tgt = str((extra.get("target") if isinstance(extra, dict) else None) or "bottom")
                if only_target and tgt.lower() != str(only_target).lower():
                    continue
                if only_symbol and sym.upper() != str(only_symbol).upper():
                    continue
                if only_interval and itv != str(only_interval):
                    continue
                c["_sym"] = sym
                c["_itv"] = itv
                c["_tgt"] = tgt
                filtered.append(c)

            if not filtered:
                AUTO_LABELER_LATENCY.observe(time.perf_counter() - t0)
                return {"status": "no_candidates"}

            for c in filtered:
                sym = c["_sym"]
                itv = c["_itv"]
                tgt = c["_tgt"]
                groups.setdefault((sym, itv, tgt), []).append(c)

            from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as fetch_ohlcv_recent
            for (sym, itv, tgt), rows in groups.items():
                # Fetch per-group OHLCV window sized to lookahead
                ohlcv_rows = await fetch_ohlcv_recent(sym, itv, limit=max(200, min(2000, batch_limit * (L + 5))))
                ohlcv_rows = list(sorted(ohlcv_rows, key=lambda r: r.get('open_time', 0)))
                for c in rows:
                    created_ts = c["created_at"].timestamp()
                    label_val = None
                    try:
                        lb = label_for_created_ts(
                            ohlcv_rows,
                            created_ts,
                            lookahead=int(L),
                            drawdown=float(DD),
                            rebound=float(RB),
                        )
                        if lb is not None:
                            label_val = int(lb)
                    except Exception:
                        label_val = None
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
