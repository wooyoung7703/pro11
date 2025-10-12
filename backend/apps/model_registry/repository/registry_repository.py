from __future__ import annotations
from typing import Any, Dict, List, Optional
import json
import math
import asyncpg
from backend.common.db.connection import init_pool
from datetime import datetime

INSERT_SQL = """
INSERT INTO model_registry (name, version, model_type, status, artifact_path, metrics)
VALUES ($1,$2,$3,$4,$5,$6)
ON CONFLICT (name, version, model_type) DO NOTHING
RETURNING id, created_at;
"""

FETCH_BY_KEYS_SQL = """
SELECT id, created_at FROM model_registry
WHERE name=$1 AND version=$2 AND model_type=$3
LIMIT 1;
"""

FETCH_LATEST_SQL = """
SELECT * FROM model_registry
WHERE name = $1 AND model_type = $2
ORDER BY created_at DESC
LIMIT $3;
"""

PROMOTE_SQL = """
UPDATE model_registry
SET status = 'production', promoted_at = NOW()
WHERE id = $1 AND status != 'production'
RETURNING id, name, version, model_type, status, promoted_at;
"""

APPEND_METRICS_HISTORY_SQL = """
INSERT INTO model_metrics_history (model_id, metrics)
VALUES ($1, $2);
"""

UPDATE_METRICS_SQL = """
UPDATE model_registry
SET metrics = $2
WHERE id = $1;
"""

LINEAGE_SQL = """
INSERT INTO model_lineage (parent_id, child_id)
VALUES ($1,$2)
ON CONFLICT (parent_id, child_id) DO NOTHING;
"""

class ModelRegistryRepository:
    async def fetch_by_id(self, model_id: int) -> Optional[dict]:
        SQL = """
        SELECT * FROM model_registry WHERE id=$1;
        """
        pool = await init_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(SQL, model_id)
            if not row:
                return None
            d = dict(row)
            try:
                mval = d.get("metrics")
                if isinstance(mval, str):
                    d["metrics"] = json.loads(mval)
            except Exception:
                pass
            return d
    async def register(self, name: str, version: str, model_type: str, status: str = "staging", artifact_path: str | None = None, metrics: Dict[str, Any] | None = None) -> Optional[int]:
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
                metrics_payload = json.dumps(safe_metrics, allow_nan=False)
            row = await conn.fetchrow(INSERT_SQL, name, version, model_type, status, artifact_path, metrics_payload)
            if row:
                return row[0]
            # If ON CONFLICT DO NOTHING hit, retrieve existing id to return a stable model_id
            try:
                row2 = await conn.fetchrow(FETCH_BY_KEYS_SQL, name, version, model_type)
                return row2[0] if row2 else None
            except Exception:
                return None

    async def fetch_latest(self, name: str, model_type: str, limit: int = 5) -> List[asyncpg.Record]:
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(FETCH_LATEST_SQL, name, model_type, limit)
            parsed: List[asyncpg.Record] = []
            for r in rows:
                try:
                    mval = r["metrics"]
                    if isinstance(mval, str):
                        # create a dict copy with parsed metrics
                        d = dict(r)
                        try:
                            d["metrics"] = json.loads(mval)
                        except Exception:
                            d["metrics"] = {"_raw": mval, "_parse_error": True}
                        # store back as a lightweight Record substitute using asyncpg.Record-like access via mapping
                        # simplest: just return dicts instead of Records for downstream usage
                        parsed.append(d)  # type: ignore
                        continue
                except Exception:
                    pass
                parsed.append(r)
            return parsed  # type: ignore

    async def promote(self, model_id: int) -> Optional[asyncpg.Record]:
        pool = await init_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(PROMOTE_SQL, model_id)
            return row

    async def demote_others(self, name: str, model_type: str, keep_id: int) -> int:
        """Set all other rows of the same (name, model_type) to staging from production.

        Returns number of rows updated.
        """
        DEMOTE_SQL = """
        UPDATE model_registry
        SET status = 'staging'
        WHERE name = $1 AND model_type = $2 AND id != $3 AND status = 'production';
        """
        pool = await init_pool()
        async with pool.acquire() as conn:
            res = await conn.execute(DEMOTE_SQL, name, model_type, keep_id)
            try:
                # res like 'UPDATE 3'
                return int(str(res).split(' ')[-1])
            except Exception:
                return 0

    async def activate(self, model_id: int) -> Optional[asyncpg.Record]:
        """Force set a model row as production and update promoted_at to NOW()."""
        ACTIVATE_SQL = """
        UPDATE model_registry
        SET status = 'production', promoted_at = NOW()
        WHERE id = $1
        RETURNING id, name, version, model_type, status, promoted_at;
        """
        pool = await init_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(ACTIVATE_SQL, model_id)
            return row

    async def soft_delete(self, model_id: int) -> bool:
        """Soft-delete a model by setting status='deleted'."""
        SOFT_DELETE_SQL = """
        UPDATE model_registry SET status='deleted' WHERE id=$1 AND status != 'deleted' RETURNING id;
        """
        pool = await init_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(SOFT_DELETE_SQL, model_id)
            return bool(row)

    async def fetch_production_history(self, name: str, model_type: str, limit: int = 10) -> List[asyncpg.Record]:
        """Fetch production rows ordered by most recently promoted (fallback created_at)."""
        SQL = """
        SELECT id, name, version, model_type, status, created_at, promoted_at, metrics, artifact_path
        FROM model_registry
        WHERE name=$1 AND model_type=$2 AND status='production'
        ORDER BY COALESCE(promoted_at, created_at) DESC
        LIMIT $3;
        """
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(SQL, name, model_type, limit)
            parsed: List[asyncpg.Record] = []
            for r in rows:
                try:
                    mval = r["metrics"]
                    if isinstance(mval, str):
                        d = dict(r)
                        try:
                            d["metrics"] = json.loads(mval)
                        except Exception:
                            d["metrics"] = {"_raw": mval, "_parse_error": True}
                        parsed.append(d)  # type: ignore
                        continue
                except Exception:
                    pass
                parsed.append(r)
            return parsed  # type: ignore

    async def append_metrics(self, model_id: int, metrics: Dict[str, Any]) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            # Sanitize metrics before persisting to JSONB (no NaN/Infinity)
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
                metrics_payload = json.dumps(safe_metrics, allow_nan=False)
            await conn.execute(APPEND_METRICS_HISTORY_SQL, model_id, metrics_payload)
            await conn.execute(UPDATE_METRICS_SQL, model_id, metrics_payload)

    async def add_lineage(self, parent_id: int, child_id: int) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await conn.execute(LINEAGE_SQL, parent_id, child_id)
