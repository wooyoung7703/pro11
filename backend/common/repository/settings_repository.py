from __future__ import annotations

import contextlib
import inspect
import json
from typing import Any, Optional, List, Dict

import asyncpg

from backend.common.db.connection import init_pool


async def _direct_connect_from_env() -> Optional[asyncpg.Connection]:
    """Best-effort direct connection if pool is unavailable.

    Mirrors other repositories' fallback behavior for early startup.
    """
    try:
        # Defer import to avoid config import cycles at module import time
        from backend.common.config.base_config import load_config  # type: ignore
        cfg = load_config()
        return await asyncpg.connect(  # type: ignore
            host=cfg.postgres_host,
            port=cfg.postgres_port,
            user=cfg.postgres_user,
            password=cfg.postgres_password,
            database=cfg.postgres_db,
        )
    except Exception:
        return None


class SettingsRepository:
    async def get_all(self) -> List[Dict[str, Any]]:
        pool = await init_pool()
        sql = """
        SELECT key, value, scope, updated_at, updated_by
        FROM app_settings
        ORDER BY key
        """
        if pool is None:
            conn = await _direct_connect_from_env()
            if conn is None:
                return []
            try:
                rows = await conn.fetch(sql)
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
        else:
            async with pool.acquire() as conn:  # type: ignore
                rows = await conn.fetch(sql)
        out: List[Dict[str, Any]] = []
        for r in rows:
            v = r["value"]
            if isinstance(v, (bytes, bytearray)):
                try:
                    v = json.loads(v.decode("utf-8"))
                except Exception:
                    v = v.decode("utf-8", errors="ignore")
            elif isinstance(v, str):
                try:
                    v = json.loads(v)
                except Exception:
                    pass
            out.append({
                "key": r["key"],
                "value": v,
                "scope": r.get("scope") if isinstance(r, dict) else r["scope"],
                "updated_at": r["updated_at"],
                "updated_by": r.get("updated_by") if isinstance(r, dict) else r["updated_by"],
            })
        return out

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        pool = await init_pool()
        sql = "SELECT key, value, scope, updated_at, updated_by FROM app_settings WHERE key=$1"
        row = None
        if pool is None:
            conn = await _direct_connect_from_env()
            if conn is None:
                return None
            try:
                row = await conn.fetchrow(sql, key)
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
        else:
            async with pool.acquire() as conn:  # type: ignore
                row = await conn.fetchrow(sql, key)
        if not row:
            return None
        v = row["value"]
        if isinstance(v, (bytes, bytearray)):
            try:
                v = json.loads(v.decode("utf-8"))
            except Exception:
                v = v.decode("utf-8", errors="ignore")
        elif isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                pass
        return {
            "key": row["key"],
            "value": v,
            "scope": row["scope"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }

    async def upsert(self, key: str, value: Any, *, scope: Optional[str] = None, updated_by: Optional[str] = None) -> Dict[str, Any]:
        pool = await init_pool()
        sql = (
            "INSERT INTO app_settings (key, value, scope, updated_at, updated_by) "
            "VALUES ($1, $2, $3, NOW(), $4) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, scope=COALESCE(EXCLUDED.scope, app_settings.scope), updated_at=NOW(), updated_by=EXCLUDED.updated_by "
            "RETURNING key, value, scope, updated_at, updated_by"
        )
        # Store as JSON text for maximum compatibility
        try:
            payload = json.dumps(value)
        except Exception:
            # Fallback to string representation
            payload = json.dumps({"_": str(value)})
        row = None
        if pool is None:
            conn = await _direct_connect_from_env()
            if conn is None:
                raise RuntimeError("db_unavailable")
            try:
                row = await conn.fetchrow(sql, key, payload, scope, (updated_by or "api"))
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
        else:
            async with pool.acquire() as conn:  # type: ignore
                row = await conn.fetchrow(sql, key, payload, scope, (updated_by or "api"))
        # Decode on return
        v = row["value"]
        try:
            v = json.loads(v)
        except Exception:
            pass
        return {
            "key": row["key"],
            "value": v,
            "scope": row["scope"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
        }
