from __future__ import annotations
import asyncpg
import asyncio
import logging
from typing import Optional, Literal
from backend.common.config.base_config import load_config

_config = load_config()
_pool: Optional[asyncpg.Pool] = None
_creating: bool = False
_last_error: str | None = None
_last_success_ts: float | None = None
_retry_task: asyncio.Task | None = None
_pool_mode: Literal["uninitialized","creating","ready","failed","skipped"] = "uninitialized"
_logger = logging.getLogger(__name__)

async def _single_connect_health(timeout: float = 3.0) -> bool:
    # 1차: DSN 직접
    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn=_config.dsn, timeout=timeout), timeout=timeout+0.5)
        try:
            await conn.fetchval("SELECT 1")
        finally:
            await conn.close()
        return True
    except Exception as e1:
        _logger.debug("single_connect_health dsn attempt failed: %s", e1)
        # 2차: 파라미터 분리 시도
        try:
            conn = await asyncio.wait_for(asyncpg.connect(
                host=_config.postgres_host,
                port=_config.postgres_port,
                user=_config.postgres_user,
                password=_config.postgres_password,
                database=_config.postgres_db,
                timeout=timeout,
            ), timeout=timeout+0.5)
            try:
                await conn.fetchval("SELECT 1")
            finally:
                await conn.close()
            _logger.info("single_connect_health succeeded on param fallback")
            return True
        except Exception as e2:
            _logger.warning("single_connect_health both attempts failed: dsn=%s param=%s", e1, e2)
            return False

async def _create_pool_once() -> None:
    global _pool, _creating, _last_error, _last_success_ts, _pool_mode
    if _pool is not None or _creating:
        return
    if getattr(_config, "skip_db", False):
        _pool_mode = "skipped"
        return
    _creating = True
    _pool_mode = "creating"
    try:
        ok = await _single_connect_health()
        if not ok:
            raise RuntimeError("single_connect_health_failed")
        _pool = await asyncpg.create_pool(dsn=_config.dsn, min_size=1, max_size=5, statement_cache_size=0)
        _last_success_ts = asyncio.get_event_loop().time()
        _pool_mode = "ready"
        _logger.info("DB pool created (background)")
    except Exception as e:
        _last_error = str(e)
        _pool_mode = "failed"
        _logger.warning("DB pool create attempt failed: %s", e)
    finally:
        _creating = False

async def _retry_loop(initial_delay: float = 2.0, max_delay: float = 30.0) -> None:
    global _retry_task, _pool_mode
    delay = initial_delay
    while _pool is None and not getattr(_config, "skip_db", False):
        await _create_pool_once()
        if _pool is not None:
            break
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_delay)
    _retry_task = None

async def ensure_pool_background() -> None:
    """Kick off background pool creation if not already ready/creating.
    Returns immediately; does not guarantee pool availability.
    """
    global _retry_task
    if _pool is not None or getattr(_config, "skip_db", False):
        return
    if _retry_task is None:
        _retry_task = asyncio.create_task(_retry_loop())

async def init_pool(_unused: bool = False) -> Optional[asyncpg.Pool]:  # keep signature compatibility
    """Backward compatible entry point.
    Tries a fast path: if pool already ready return it.
    Otherwise trigger background creation and return current (likely None).
    """
    if _pool is not None:
        return _pool
    await ensure_pool_background()
    return _pool

async def get_conn() -> asyncpg.Connection:
    if _pool is None:
        await init_pool()
    if _pool is None:
        raise RuntimeError("Database pool is not initialized (skip_db or connection failure)")
    return await _pool.acquire()

async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

def pool_status() -> dict:
    return {
        "mode": _pool_mode,
        "has_pool": _pool is not None,
        "creating": _creating,
        "last_error": _last_error,
        "last_success_ts": _last_success_ts,
        "skip_db": getattr(_config, "skip_db", False),
    }

async def force_pool_retry() -> dict:
    if getattr(_config, "skip_db", False):
        return {"skipped": True}
    if _pool is not None:
        return {"status": "already_ready"}
    await _create_pool_once()
    if _pool is None:
        return {"status": "failed", "detail": _last_error}
    return {"status": "ready"}
