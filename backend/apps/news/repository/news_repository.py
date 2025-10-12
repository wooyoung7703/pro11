from __future__ import annotations
import asyncpg, time, logging, os
from collections import defaultdict
from typing import List, Optional, Dict, Any
from backend.common.db.connection import init_pool
from backend.common.config.base_config import load_config
_news_cfg = load_config()

logger = logging.getLogger(__name__)

NEWS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS news_articles (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    symbol TEXT NULL,
    title TEXT NOT NULL,
    body TEXT NULL,
    url TEXT NULL,
    published_ts BIGINT NOT NULL,
    ingested_ts BIGINT NOT NULL,
    sentiment REAL NULL,
    lang VARCHAR(8) NULL,
    hash TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_news_published_ts ON news_articles(published_ts DESC);
CREATE INDEX IF NOT EXISTS idx_news_symbol_published_ts ON news_articles(symbol, published_ts DESC);
"""

class NewsRepository:
    # Simple in-memory cache: key -> {value, ts}
    _sent_cache: dict[tuple[int, str | None], dict] = {}
    _sent_cache_ttl_sec: int = int(os.getenv("NEWS_SENTIMENT_CACHE_TTL", "30")) if os.getenv("NEWS_SENTIMENT_CACHE_TTL") else 30
    try:
        from prometheus_client import Counter
        _metric_cache_hit = Counter("news_sentiment_cache_hits_total", "News sentiment window cache hits")
        _metric_cache_miss = Counter("news_sentiment_cache_miss_total", "News sentiment window cache misses")
    except Exception:  # pragma: no cover
        _metric_cache_hit = None
        _metric_cache_miss = None
    async def _ensure_table(self, conn: asyncpg.Connection):  # type: ignore
        await conn.execute(NEWS_TABLE_SQL)

    async def ensure_schema(self) -> None:
        """Explicitly ensure the news table & indexes exist.

        Although inserts call _ensure_table lazily, some operational flows
        (예: 초기 부트스트랩, 상태 점검) 에서 선행 테이블 존재가 필요할 수 있어
        명시적으로 호출할 수 있는 public 메서드를 제공한다.
        """
        pool = await init_pool()
        if pool is not None:
            async with pool.acquire() as conn:  # type: ignore
                await self._ensure_table(conn)
            return
        # Fallback: direct one-off connection (pool not ready yet). Avoid raising if transient.
        # This mirrors earlier schema manager fallback pattern.
        try:
            conn = await asyncpg.connect(dsn=_news_cfg.dsn, timeout=3.5)  # type: ignore[arg-type]
        except Exception:
            # second attempt with explicit params
            try:
                conn = await asyncpg.connect(
                    host=_news_cfg.postgres_host,
                    port=_news_cfg.postgres_port,
                    user=_news_cfg.postgres_user,
                    password=_news_cfg.postgres_password,
                    database=_news_cfg.postgres_db,
                    timeout=3.5,
                )
            except Exception as e2:  # noqa: BLE001
                raise RuntimeError("db_unavailable_fallback") from e2
        try:
            await self._ensure_table(conn)
        finally:
            with contextlib.suppress(Exception):  # type: ignore
                await conn.close()

    async def insert_articles(self, rows: List[Dict[str, Any]]) -> int:
        """Insert articles into persistent store (Postgres).

        모든 환경(개발/로컬/검증/운영)에서 항상 실 DB 를 사용하도록 강제한다.
        과거 지원되던 NEWS_TEST_INMEMORY 경로는 제거되었다.
        """
        pool = await init_pool()
        if pool is None:
            raise RuntimeError("db_unavailable")
        inserted = 0
        forbidden_sources = {"demo_feed"}
        allow_legacy_demo = os.getenv("ALLOW_DEMO_FEED", "0") in {"1", "true", "TRUE", "yes", "on"}
        async with pool.acquire() as conn:  # type: ignore
            await self._ensure_table(conn)
            for r in rows:
                src = r.get('source')
                if (not allow_legacy_demo) and src in forbidden_sources:
                    # Skip silently but log once per run (rate limited via attribute)
                    flag_attr = "_logged_demo_feed_skip"
                    if not getattr(self, flag_attr, False):
                        logger.info("skip_inserting_forbidden_source source=%s", src)
                        setattr(self, flag_attr, True)
                    continue
                try:
                    res = await conn.execute(
                        """
                        INSERT INTO news_articles (source, symbol, title, body, url, published_ts, ingested_ts, sentiment, lang, hash)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                        ON CONFLICT (hash) DO NOTHING
                        """,
                        r.get('source'), r.get('symbol'), r.get('title'), r.get('body'), r.get('url'),
                        r.get('published_ts'), r.get('ingested_ts', int(time.time())), r.get('sentiment'), r.get('lang'), r.get('hash')
                    )
                    # asyncpg returns command tag like 'INSERT 0 1' when a row inserted, or 'INSERT 0 0' if ON CONFLICT DO NOTHING
                    if isinstance(res, str) and res.endswith(" 1"):
                        inserted += 1
                    else:
                        # Dedup skip (hash conflict) – log at very low verbosity once per hash optionally
                        if logger.isEnabledFor(logging.DEBUG):
                            logger.debug("news_insert_dedup_skip title=%s hash=%s", r.get('title'), r.get('hash'))
                except Exception as e:
                    logger.warning("news_insert_error err=%s title=%s source=%s", e, r.get('title'), r.get('source'))
        return inserted

    async def fetch_recent(
        self,
        limit: int = 50,
        symbol: Optional[str] = None,
        since_ts: Optional[int] = None,
        summary_only: bool = True,
    ) -> List[Dict[str, Any]]:
        pool = await init_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:  # type: ignore
            # 선택 컬럼: summary_only=False 인 경우 body 포함 (초기 풀 페치), summary_only=True 인 경우 body 제외
            cols = "id, source, symbol, title, url, published_ts, ingested_ts, sentiment, lang"
            if not summary_only:
                cols = cols + ", body"
            base = f"SELECT {cols} FROM news_articles"
            params: List[Any] = []
            where_clauses: List[str] = []
            if symbol:
                params.append(symbol)
                where_clauses.append(f"symbol = ${len(params)}")
            if since_ts is not None:
                params.append(since_ts)
                where_clauses.append(f"published_ts >= ${len(params)}")
            where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
            params.append(limit)
            sql = base + where_sql + " ORDER BY published_ts DESC LIMIT $" + str(len(params))
            rows = await conn.fetch(sql, *params)
            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append({k: r[k] for k in r.keys()})
            return out

    async def fetch_by_id(self, article_id: int) -> Optional[Dict[str, Any]]:
        pool = await init_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:  # type: ignore
            row = await conn.fetchrow(
                "SELECT id, source, symbol, title, body, url, published_ts, ingested_ts, sentiment, lang FROM news_articles WHERE id=$1",
                article_id,
            )
            if not row:
                return None
            return {k: row[k] for k in row.keys()}

    async def fetch_bodies(self, ids: List[int]) -> List[Dict[str, Any]]:
        if not ids:
            return []
        pool = await init_pool()
        if pool is None:
            return []
        async with pool.acquire() as conn:  # type: ignore
            # limit number of ids to avoid SQL injection risk & huge queries
            safe_ids = ids[:200]
            rows = await conn.fetch(
                "SELECT id, body FROM news_articles WHERE id = ANY($1::bigint[])",
                safe_ids,
            )
            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append({"id": r["id"], "body": r["body"]})
            return out

    async def latest_article_ts(self) -> Optional[int]:
        pool = await init_pool()
        if pool is None:
            return None
        async with pool.acquire() as conn:  # type: ignore
            row = await conn.fetchrow("SELECT published_ts FROM news_articles ORDER BY published_ts DESC LIMIT 1")
            if not row:
                return None
            return int(row["published_ts"]) if row["published_ts"] is not None else None

    async def sentiment_window_stats(self, minutes: int = 60, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Aggregate sentiment stats for articles within the last N minutes."""
        if minutes <= 0:
            minutes = 60
        # Cache lookup first
        key = (minutes, symbol)
        now = time.time()
        entry = self._sent_cache.get(key)
        if entry and (now - entry.get("ts", 0)) < self._sent_cache_ttl_sec:
            if self._metric_cache_hit:
                try: self._metric_cache_hit.inc()
                except Exception: pass
            return entry.get("value", {})
        # (이전 in-memory 분기 제거됨)
        # --- DB path ---
        cutoff = int(time.time()) - minutes * 60
        pool = await init_pool()
        if pool is None:
            return {"window_minutes": minutes, "count": 0, "avg": None, "pos": 0, "neg": 0, "neutral": 0, "symbol": symbol}
        async with pool.acquire() as conn:  # type: ignore
            if symbol:
                rows = await conn.fetch(
                    """
                    SELECT sentiment FROM news_articles
                    WHERE published_ts >= $1 AND symbol = $2 AND sentiment IS NOT NULL
                    """,
                    cutoff, symbol,
                )
            else:
                rows = await conn.fetch(
                    "SELECT sentiment FROM news_articles WHERE published_ts >= $1 AND sentiment IS NOT NULL",
                    cutoff,
                )
            sentiments: List[float] = [float(r["sentiment"]) for r in rows if r["sentiment"] is not None]
            if not sentiments:
                result = {"window_minutes": minutes, "count": 0, "avg": None, "pos": 0, "neg": 0, "neutral": 0, "symbol": symbol}
                self._sent_cache[key] = {"value": result, "ts": now}
                if self._metric_cache_miss:
                    try: self._metric_cache_miss.inc()
                    except Exception: pass
                return result
            total = len(sentiments)
            pos = sum(1 for s in sentiments if s > 0.05)
            neg = sum(1 for s in sentiments if s < -0.05)
            neutral = total - pos - neg
            avg = sum(sentiments) / total if total else None
            result = {"window_minutes": minutes, "count": total, "avg": avg, "pos": pos, "neg": neg, "neutral": neutral, "symbol": symbol}
            self._sent_cache[key] = {"value": result, "ts": now}
            if self._metric_cache_miss:
                try: self._metric_cache_miss.inc()
                except Exception: pass
            return result

    async def delete_by_source(self, source: str) -> int:
        """Delete all articles for a given source. Returns rows deleted."""
        if not source:
            return 0
        pool = await init_pool()
        if pool is None:
            return 0
        async with pool.acquire() as conn:  # type: ignore
            try:
                res = await conn.execute("DELETE FROM news_articles WHERE source=$1", source)
                # asyncpg returns e.g. 'DELETE <n>'
                try:
                    return int(str(res).split()[-1])
                except Exception:  # noqa: BLE001
                    return 0
            except Exception as e:  # noqa: BLE001
                logger.warning("news_delete_by_source_failed source=%s err=%s", source, e)
                return 0

    async def table_exists(self) -> bool:
        """Diagnostic helper: returns True if news_articles table exists in current DB."""
        pool = await init_pool()
        if pool is None:
            return False
        async with pool.acquire() as conn:  # type: ignore
            try:
                row = await conn.fetchrow("SELECT 1 FROM pg_tables WHERE tablename='news_articles' LIMIT 1")
                return bool(row)
            except Exception:
                return False

    async def row_count(self) -> int:
        """Return total number of rows in news_articles (0 if unavailable)."""
        pool = await init_pool()
        if pool is None:
            return 0
        async with pool.acquire() as conn:  # type: ignore
            try:
                val = await conn.fetchval("SELECT COUNT(*) FROM news_articles")
                return int(val or 0)
            except Exception:
                return 0
