from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.common.db.connection import init_pool


# Compose a timeline from existing persisted sources without introducing a new table.
# Sources:
#  - trading_signals: creation and execution updates
#  - autopilot_event_log: 'signal' and 'signal_clear' events

SQL_TIMELINE = r"""
WITH created AS (
  SELECT
    extract(epoch from s.created_at) AS ts,
    'trading'::text AS source,
    'created'::text AS event,
    s.signal_type,
    NULL::text AS symbol,
    jsonb_build_object(
      'id', s.id,
      'status', s.status,
      'price', s.price,
      'params', s.params,
      'extra', s.extra
    ) AS details
  FROM trading_signals s
  WHERE ($3::text IS NULL OR s.signal_type = $3)
), executed AS (
  SELECT
    extract(epoch from s.executed_at) AS ts,
    'trading'::text AS source,
    COALESCE(NULLIF(s.status, ''), 'executed')::text AS event,
    s.signal_type,
    NULL::text AS symbol,
    jsonb_build_object(
      'id', s.id,
      'order_side', s.order_side,
      'order_size', s.order_size,
      'order_price', s.order_price,
      'error', s.error,
      'extra', s.extra
    ) AS details
  FROM trading_signals s
  WHERE s.executed_at IS NOT NULL AND ($3::text IS NULL OR s.signal_type = $3)
), auto_signal AS (
  SELECT
    extract(epoch from e.ts) AS ts,
    'autopilot'::text AS source,
    e.type::text AS event,
    -- pull signal kind if available
    COALESCE((e.payload->'signal'->>'kind')::text, NULL) AS signal_type,
    (e.payload->'signal'->>'symbol')::text AS symbol,
    e.payload AS details
  FROM autopilot_event_log e
  WHERE e.type IN ('signal','signal_clear')
)
SELECT ts, source, event, signal_type, symbol, details
FROM (
  SELECT * FROM created
  UNION ALL
  SELECT * FROM executed
  UNION ALL
  SELECT * FROM auto_signal
) t
WHERE ($1::double precision IS NULL OR ts >= $1)
  AND ($2::double precision IS NULL OR ts <= $2)
ORDER BY ts DESC
LIMIT $4;
"""


class SignalTimelineRepository:
    async def fetch_timeline(
        self,
        *,
        limit: int = 200,
        from_ts: Optional[float] = None,
        to_ts: Optional[float] = None,
        signal_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        pool = await init_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:  # type: ignore
            rows = await conn.fetch(
                SQL_TIMELINE,
                float(from_ts) if from_ts is not None else None,
                float(to_ts) if to_ts is not None else None,
                str(signal_type) if signal_type is not None else None,
                int(max(1, min(limit, 1000))),
            )
            out: List[Dict[str, Any]] = []
            import json
            for r in rows:
                d = dict(r)
                # Convert details to dict if returned as text
                val = d.get("details")
                if isinstance(val, str):
                    try:
                        d["details"] = json.loads(val)
                    except Exception:
                        pass
                out.append(d)
            return out

__all__ = ["SignalTimelineRepository"]
