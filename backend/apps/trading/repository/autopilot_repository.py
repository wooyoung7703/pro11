from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.common.db.connection import init_pool


CREATE_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS autopilot_event_log (
  id SERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL,
  type TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
"""

CREATE_STATE_SQL = """
CREATE TABLE IF NOT EXISTS autopilot_state_snapshot (
  id SERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL,
  strategy JSONB,
  position JSONB,
  risk JSONB,
  exit_policy JSONB,
  health JSONB
);
"""

# Indexes to speed up common queries
CREATE_EVENTS_INDEX_TS_SQL = """
CREATE INDEX IF NOT EXISTS idx_autopilot_event_ts ON autopilot_event_log (ts DESC);
"""
CREATE_EVENTS_INDEX_TYPE_SQL = """
CREATE INDEX IF NOT EXISTS idx_autopilot_event_type ON autopilot_event_log (type);
"""
CREATE_STATE_INDEX_TS_SQL = """
CREATE INDEX IF NOT EXISTS idx_autopilot_state_ts ON autopilot_state_snapshot (ts DESC);
"""

INSERT_EVENT_SQL = (
    "INSERT INTO autopilot_event_log (ts, type, payload) VALUES (to_timestamp($1), $2, $3::jsonb) RETURNING id"
)

FETCH_EVENTS_SQL = (
    "SELECT id, extract(epoch from ts) AS ts, type, payload FROM autopilot_event_log"
    " WHERE ($1::double precision IS NULL OR ts >= to_timestamp($1))"
    "   AND ($2::double precision IS NULL OR ts <= to_timestamp($2))"
    "   AND ($3::text IS NULL OR type = $3)"
    "   AND ($4::integer IS NULL OR id < $4)"
    " ORDER BY id DESC LIMIT $5"
)

INSERT_STATE_SQL = (
    "INSERT INTO autopilot_state_snapshot (ts, strategy, position, risk, exit_policy, health)"
    " VALUES (to_timestamp($1), $2::jsonb, $3::jsonb, $4::jsonb, $5::jsonb, $6::jsonb) RETURNING id"
)

FETCH_STATE_RANGE_SQL = (
    "SELECT id, extract(epoch from ts) AS ts, strategy, position, risk, exit_policy, health"
    " FROM autopilot_state_snapshot"
    " WHERE ($1::double precision IS NULL OR ts >= to_timestamp($1))"
    "   AND ($2::double precision IS NULL OR ts <= to_timestamp($2))"
    "   AND ($3::integer IS NULL OR id < $3)"
    " ORDER BY id DESC LIMIT $4"
)


class AutopilotRepository:
    async def _ensure(self, conn) -> None:
        await conn.execute(CREATE_EVENTS_SQL)
        await conn.execute(CREATE_STATE_SQL)
        # Indexes (safe to call repeatedly with IF NOT EXISTS)
        await conn.execute(CREATE_EVENTS_INDEX_TS_SQL)
        await conn.execute(CREATE_EVENTS_INDEX_TYPE_SQL)
        await conn.execute(CREATE_STATE_INDEX_TS_SQL)

    async def insert_event(self, *, ts: float, type: str, payload: Dict[str, Any]) -> Optional[int]:
        pool = await init_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            row = await conn.fetchrow(INSERT_EVENT_SQL, float(ts), str(type), __import__("json").dumps(payload, allow_nan=False))
            return int(row[0]) if row else None

    async def fetch_events(
        self,
        *,
        limit: int = 100,
        from_ts: Optional[float] = None,
        to_ts: Optional[float] = None,
        event_type: Optional[str] = None,
        before_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        pool = await init_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            rows = await conn.fetch(
                FETCH_EVENTS_SQL,
                float(from_ts) if from_ts is not None else None,
                float(to_ts) if to_ts is not None else None,
                str(event_type) if event_type is not None else None,
                int(before_id) if before_id is not None else None,
                int(max(1, min(limit, 1000))),
            )
            return [dict(r) for r in rows]

    async def insert_state_snapshot(
        self,
        *,
        ts: float,
        strategy: Dict[str, Any],
        position: Dict[str, Any],
        risk: Dict[str, Any],
        exit_policy: Optional[Dict[str, Any]],
        health: Dict[str, Any],
    ) -> Optional[int]:
        pool = await init_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            import json
            row = await conn.fetchrow(
                INSERT_STATE_SQL,
                float(ts),
                json.dumps(strategy, allow_nan=False),
                json.dumps(position, allow_nan=False),
                json.dumps(risk, allow_nan=False),
                json.dumps(exit_policy, allow_nan=False) if exit_policy is not None else None,
                json.dumps(health, allow_nan=False),
            )
            return int(row[0]) if row else None

    async def fetch_state_history(
        self,
        *,
        from_ts: Optional[float] = None,
        to_ts: Optional[float] = None,
        before_id: Optional[int] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        pool = await init_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure(conn)
            rows = await conn.fetch(
                FETCH_STATE_RANGE_SQL,
                float(from_ts) if from_ts is not None else None,
                float(to_ts) if to_ts is not None else None,
                int(before_id) if before_id is not None else None,
                int(max(1, min(limit, 2000))),
            )
            out: List[Dict[str, Any]] = []
            import json
            for r in rows:
                d = dict(r)
                # Convert JSON strings to dict if driver returns text
                for key in ("strategy", "position", "risk", "exit_policy", "health"):
                    val = d.get(key)
                    if isinstance(val, str):
                        try:
                            d[key] = json.loads(val)
                        except Exception:
                            pass
                out.append(d)
            return out

__all__ = ["AutopilotRepository"]
