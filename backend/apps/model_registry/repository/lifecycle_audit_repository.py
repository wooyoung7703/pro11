from __future__ import annotations
from typing import Any, Dict, List
import asyncpg
from backend.common.db.connection import init_pool

INSERT_RETRAIN_SQL = """
INSERT INTO retrain_events (job_id, trigger, status, drift_feature, drift_z, model_id, version, artifact_path, samples, error)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10);
"""

INSERT_PROMOTION_SQL = """
INSERT INTO promotion_events (model_id, previous_production_model_id, decision, reason, samples_old, samples_new)
VALUES ($1,$2,$3,$4,$5,$6);
"""

FETCH_RETRAIN_SQL = """
SELECT * FROM retrain_events ORDER BY created_at DESC LIMIT $1;
"""

FETCH_RETRAIN_WITH_OFFSET_SQL = """
SELECT * FROM retrain_events ORDER BY created_at DESC LIMIT $1 OFFSET $2;
"""

FETCH_PROMOTION_SQL = """
SELECT * FROM promotion_events ORDER BY created_at DESC LIMIT $1;
"""

FETCH_PROMOTION_WITH_OFFSET_SQL = """
SELECT * FROM promotion_events ORDER BY created_at DESC LIMIT $1 OFFSET $2;
"""

class LifecycleAuditRepository:
    async def log_retrain(self, *, job_id: int | None, trigger: str, status: str, drift_feature: str | None, drift_z: float | None, model_id: int | None, version: str | None, artifact_path: str | None, samples: int | None, error: str | None) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:  # type: ignore
            await conn.execute(
                INSERT_RETRAIN_SQL,
                job_id,
                trigger,
                status,
                drift_feature,
                drift_z,
                model_id,
                version,
                artifact_path,
                samples,
                error,
            )

    async def log_promotion(self, *, model_id: int | None, previous_production_model_id: int | None, decision: str, reason: str | None, samples_old: int | None, samples_new: int | None) -> None:
        pool = await init_pool()
        async with pool.acquire() as conn:
            await conn.execute(INSERT_PROMOTION_SQL, model_id, previous_production_model_id, decision, reason, samples_old, samples_new)

    async def fetch_retrain(self, limit: int = 50) -> List[asyncpg.Record]:
        """Backward compatible simple fetch (no offset)."""
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(FETCH_RETRAIN_SQL, limit)
            return list(rows)

    async def fetch_retrain_events(self, *, limit: int = 50, offset: int = 0) -> List[asyncpg.Record]:
        """Fetch retrain events with optional offset used by API endpoint."""
        if offset <= 0:
            return await self.fetch_retrain(limit=limit)
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(FETCH_RETRAIN_WITH_OFFSET_SQL, limit, offset)
            return list(rows)

    async def fetch_promotions(self, limit: int = 50) -> List[asyncpg.Record]:
        """Backward compatible simple fetch (no offset)."""
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(FETCH_PROMOTION_SQL, limit)
            return list(rows)

    async def fetch_promotion_events(self, *, limit: int = 50, offset: int = 0) -> List[asyncpg.Record]:
        """Fetch promotion events with optional offset used by API endpoint."""
        if offset <= 0:
            return await self.fetch_promotions(limit=limit)
        pool = await init_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(FETCH_PROMOTION_WITH_OFFSET_SQL, limit, offset)
            return list(rows)

__all__ = ["LifecycleAuditRepository"]
