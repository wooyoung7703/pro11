from __future__ import annotations
import os, time, json
from typing import Any, List, Dict
from pathlib import Path
from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository
from prometheus_client import Counter, Gauge

DEFAULT_KEEP_STAGING = int(os.getenv("ARTIFACT_CLEANUP_KEEP_STAGING", 5)) if os.getenv("ARTIFACT_CLEANUP_KEEP_STAGING") else 5
DEFAULT_INTERVAL_SECONDS = int(os.getenv("ARTIFACT_CLEANUP_INTERVAL_SECONDS", 1800))  # 30m

class ArtifactCleanupService:
    def __init__(self, base_dir: str, keep_staging: int = DEFAULT_KEEP_STAGING):
        self.base_dir = Path(base_dir)
        self.keep_staging = keep_staging
        self.repo = ModelRegistryRepository()
        self.last_run: float | None = None
        # Metrics
        self.metric_runs = Counter("artifact_cleanup_runs_total", "Artifact cleanup executions")
        self.metric_removed = Counter("artifact_cleanup_removed_total", "Total artifacts removed")
        self.metric_errors = Counter("artifact_cleanup_errors_total", "Total cleanup errors")
        self.metric_last_run_ts = Gauge("artifact_cleanup_last_run_timestamp", "Last artifact cleanup unixtime")
        self.metric_last_removed = Gauge("artifact_cleanup_last_removed_count", "Removed count in last cleanup run")
        self.metric_last_kept = Gauge("artifact_cleanup_last_kept_count", "Kept count in last cleanup run")

    async def run_once(self) -> dict:
        """Remove stale artifacts not linked to production or latest N staging per model name."""
        start = time.time()
        removed: List[str] = []
        kept: List[str] = []
        errors: List[str] = []
        self.metric_runs.inc()
        if not self.base_dir.exists():
            return {"status": "no_dir", "base_dir": str(self.base_dir)}
        # Index files on disk (.json)
        files = [p for p in self.base_dir.glob("*.json") if p.is_file()]
        # Group by model prefix (name__version.json)
        groups: Dict[str, List[Path]] = {}
        for p in files:
            parts = p.name.split("__", 1)
            if len(parts) != 2:
                continue
            groups.setdefault(parts[0], []).append(p)
        for model_name, paths in groups.items():
            # Registry latest to decide keep set
            try:
                rows = await self.repo.fetch_latest(model_name, "supervised", limit=50)
            except Exception as e:
                errors.append(f"registry_fetch:{model_name}:{e}")
                continue
            prod_ids = set()
            staging_versions: List[str] = []
            prod_versions: List[str] = []
            for r in rows:
                metrics = r.get("metrics") or {}
                version = r.get("version")
                if r.get("status") == "production":
                    prod_versions.append(str(version))
                else:
                    staging_versions.append(str(version))
            staging_versions_sorted = staging_versions[:self.keep_staging]
            keep_versions = set(prod_versions) | set(staging_versions_sorted)
            for p in paths:
                # parse version from filename suffix after '__'
                try:
                    ver = p.name.split("__",1)[1].rsplit('.json',1)[0]
                except Exception:
                    continue
                if ver in keep_versions:
                    kept.append(str(p))
                else:
                    try:
                        p.unlink()
                        removed.append(str(p))
                    except Exception as e:
                        errors.append(f"unlink:{p.name}:{e}")
        self.last_run = time.time()
        # Update metrics
        self.metric_removed.inc(len(removed))
        self.metric_last_run_ts.set(self.last_run)
        self.metric_last_removed.set(len(removed))
        self.metric_last_kept.set(len(kept))
        for _ in errors:
            self.metric_errors.inc()
        return {
            "status": "ok",
            "removed": removed,
            "removed_count": len(removed),
            "kept_count": len(kept),
            "errors": errors,
            "duration_sec": time.time() - start,
        }

async def periodic_artifact_cleanup(service: ArtifactCleanupService, interval: int = DEFAULT_INTERVAL_SECONDS, logger_name: str = "artifact.cleanup"):
    import logging, asyncio
    log = logging.getLogger(logger_name)
    while True:  # pragma: no cover (background loop)
        try:
            result = await service.run_once()
            log.info("artifact_cleanup result removed=%s kept=%s errors=%s", result.get("removed_count"), result.get("kept_count"), len(result.get("errors", [])))
        except Exception as e:
            log.warning("artifact_cleanup error=%s", e)
        await asyncio.sleep(interval)

__all__ = ["ArtifactCleanupService", "periodic_artifact_cleanup"]