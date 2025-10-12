from __future__ import annotations
from typing import Any, Dict, List
import json
import math
from backend.common.db.connection import init_pool
import asyncpg


INSERT_SQL = """
INSERT INTO training_jobs(status, trigger, drift_feature, drift_z)
VALUES ($1,$2,$3,$4)
RETURNING id, created_at;
"""

UPDATE_SUCCESS_SQL = """
UPDATE training_jobs
SET status='success', artifact_path=$2, model_id=$3, version=$4, metrics=$5, finished_at=NOW(), duration_seconds=$6
WHERE id=$1;
"""

UPDATE_ERROR_SQL = """
UPDATE training_jobs
SET status='error', error=$2, finished_at=NOW(), duration_seconds=$3
WHERE id=$1;
"""

FETCH_RECENT_SQL = """
SELECT * FROM training_jobs
ORDER BY created_at DESC
LIMIT $1;
"""

class TrainingJobRepository:

    async def create_job(self, trigger: str, drift_feature: str | None = None, drift_z: float | None = None) -> int:
        pool = await init_pool()
        async with pool.acquire() as conn:
            # status starts as 'running' and will be updated to 'success' or 'error'
            row = await conn.fetchrow(INSERT_SQL, 'running', trigger, drift_feature, drift_z)
            return int(row['id'])  # type: ignore

    async def mark_success(self, job_id: int, artifact_path: str, model_id: int | None, version: str, metrics: Dict[str, Any], duration_seconds: float | None) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            # Ensure metrics is strict-JSON-serializable (no NaN/Infinity). Replace non-finite numbers with null.
            def _sanitize(o: Any) -> Any:
                if isinstance(o, float):
                    return o if math.isfinite(o) else None
                if isinstance(o, dict):
                    return {k: _sanitize(v) for k, v in o.items()}
                if isinstance(o, list):
                    return [_sanitize(v) for v in o]
                return o

            metrics_payload = None
            if metrics is not None:
                safe_metrics = _sanitize(metrics)
                # Disallow NaN in dumps to catch any missed cases
                metrics_payload = json.dumps(safe_metrics, allow_nan=False)
            await conn.execute(UPDATE_SUCCESS_SQL, job_id, artifact_path, model_id, version, metrics_payload, duration_seconds)

    async def mark_error(self, job_id: int, error: str, duration_seconds: float | None) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await conn.execute(UPDATE_ERROR_SQL, job_id, error, duration_seconds)

    async def fetch_recent(self, limit: int = 50) -> List[asyncpg.Record]:
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(FETCH_RECENT_SQL, limit)
            return list(rows)

__all__ = ["TrainingJobRepository"]
