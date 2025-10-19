import asyncio
import os
import time
from typing import Any, Dict, List, Optional

import pytest
import pytest_asyncio

# Ensure test-friendly startup behaviour before the FastAPI app is imported
os.environ.setdefault("FAST_STARTUP", "true")
os.environ.setdefault("APP_ENV", "test")

from backend.apps.api.main import app


@pytest_asyncio.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def app_lifespan():
    """Run the FastAPI lifespan once for the test session."""
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    try:
        yield
    finally:
        await ctx.__aexit__(None, None, None)


class InMemoryNewsRepository:
    """Lightweight stand-in for NewsRepository that keeps data in memory."""

    _store: Dict[str, Any] = {
        "articles": [],
        "hashes": set(),
        "id_seq": 1,
    }
    _sent_cache: Dict[tuple[int, Optional[str]], Dict[str, Any]] = {}
    _v = os.getenv("NEWS_SENTIMENT_CACHE_TTL", "30")
    if isinstance(_v, str) and '#' in _v:
        _v = _v.split('#', 1)[0].strip()
    _sent_cache_ttl_sec: int = int(float(_v)) if os.getenv("NEWS_SENTIMENT_CACHE_TTL") else 30

    def __init__(self) -> None:
        self._store = InMemoryNewsRepository._store
        self._sent_cache = InMemoryNewsRepository._sent_cache

    @classmethod
    def reset_global(cls) -> None:
        cls._store = {"articles": [], "hashes": set(), "id_seq": 1}
        cls._sent_cache = {}

    async def ensure_schema(self) -> None:  # pragma: no cover - noop for tests
        return None

    async def table_exists(self) -> bool:
        return True

    async def delete_by_source(self, source: str) -> int:
        if not source:
            return 0
        before = len(self._store["articles"])
        self._store["articles"] = [a for a in self._store["articles"] if a.get("source") != source]
        self._store["hashes"] = {a.get("hash") for a in self._store["articles"] if a.get("hash")}
        return before - len(self._store["articles"])

    async def row_count(self) -> int:
        return len(self._store["articles"])

    async def insert_articles(self, rows: List[Dict[str, Any]]) -> int:
        inserted = 0
        now = int(time.time())
        for row in rows:
            data = dict(row)
            h = data.get("hash")
            if h and h in self._store["hashes"]:
                continue
            data.setdefault("ingested_ts", now)
            data.setdefault("published_ts", now)
            if "id" not in data:
                data["id"] = self._store["id_seq"]
                self._store["id_seq"] += 1
            self._store["articles"].append(data)
            if h:
                self._store["hashes"].add(h)
            inserted += 1
        self._store["articles"].sort(key=lambda a: int(a.get("published_ts", 0)), reverse=True)
        if inserted:
            self._sent_cache.clear()
            cache = getattr(app.state, "news_recent_cache", None)
            if isinstance(cache, dict):
                cache.setdefault("store", {}).clear()
                order = cache.get("order")
                if isinstance(order, list):
                    order.clear()
        return inserted

    async def fetch_recent(
        self,
        limit: int = 50,
        symbol: Optional[str] = None,
        since_ts: Optional[int] = None,
        summary_only: bool = True,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for art in self._store["articles"]:
            if symbol and art.get("symbol") != symbol:
                continue
            published = int(art.get("published_ts", 0))
            if since_ts is not None and published <= since_ts:
                continue
            record = {
                "id": art.get("id"),
                "source": art.get("source"),
                "symbol": art.get("symbol"),
                "title": art.get("title"),
                "url": art.get("url"),
                "published_ts": published,
                "ingested_ts": int(art.get("ingested_ts", published)),
                "sentiment": art.get("sentiment"),
                "lang": art.get("lang"),
            }
            if not summary_only:
                record["body"] = art.get("body")
            items.append(record)
            if len(items) >= limit:
                break
        return items

    async def fetch_bodies(self, ids: List[int]) -> List[Dict[str, Any]]:
        if not ids:
            return []
        wanted = set(ids)
        out: List[Dict[str, Any]] = []
        for art in self._store["articles"]:
            art_id = art.get("id")
            if art_id in wanted:
                out.append({"id": art_id, "body": art.get("body")})
        return out

    async def latest_article_ts(self) -> Optional[int]:
        if not self._store["articles"]:
            return None
        return int(self._store["articles"][0].get("published_ts", 0))

    async def sentiment_window_stats(self, minutes: int = 60, symbol: Optional[str] = None) -> Dict[str, Any]:
        if minutes <= 0:
            minutes = 60
        key = (minutes, symbol)
        now = time.time()
        entry = self._sent_cache.get(key)
        if entry and (now - entry.get("ts", 0)) < self._sent_cache_ttl_sec:
            return entry.get("value", {})
        cutoff = int(time.time()) - minutes * 60
        sentiments: List[float] = []
        pool = None
        try:
            pool = await _news_repo_module.init_pool()
        except Exception:
            pool = None
        if pool is not None:
            try:
                async with pool.acquire() as conn:  # type: ignore[attr-defined]
                    if symbol:
                        rows = await conn.fetch(
                            """
                            SELECT sentiment FROM news_articles
                            WHERE published_ts >= $1 AND symbol = $2 AND sentiment IS NOT NULL
                            """,
                            cutoff,
                            symbol,
                        )
                    else:
                        rows = await conn.fetch(
                            "SELECT sentiment FROM news_articles WHERE published_ts >= $1 AND sentiment IS NOT NULL",
                            cutoff,
                        )
                for r in rows:
                    s = r.get("sentiment")
                    if s is None:
                        continue
                    try:
                        sentiments.append(float(s))
                    except Exception:
                        continue
            except Exception:
                sentiments = []
        if not sentiments:
            for art in self._store["articles"]:
                if symbol and art.get("symbol") != symbol:
                    continue
                published = int(art.get("published_ts", 0))
                if published < cutoff:
                    continue
                s = art.get("sentiment")
                if s is None:
                    continue
                try:
                    sentiments.append(float(s))
                except Exception:
                    continue
        total = len(sentiments)
        if not sentiments:
            result = {"window_minutes": minutes, "count": 0, "avg": None, "pos": 0, "neg": 0, "neutral": 0, "symbol": symbol}
            self._sent_cache[key] = {"value": result, "ts": now}
            return result
        pos = sum(1 for s in sentiments if s > 0.05)
        neg = sum(1 for s in sentiments if s < -0.05)
        neutral = total - pos - neg
        avg = sum(sentiments) / total if total else None
        result = {"window_minutes": minutes, "count": total, "avg": avg, "pos": pos, "neg": neg, "neutral": neutral, "symbol": symbol}
        self._sent_cache[key] = {"value": result, "ts": now}
        return result


# Replace NewsRepository references with in-memory stub for tests
from backend.apps.news.repository import news_repository as _news_repo_module
from backend.apps.news.service import news_service as _news_service_module
from backend.apps.api import main as _api_main_module

_news_repo_module.NewsRepository = InMemoryNewsRepository
_news_service_module.NewsRepository = InMemoryNewsRepository
_api_main_module.NewsRepository = InMemoryNewsRepository


@pytest.fixture(autouse=True)
def _prepare_news_repo():
    InMemoryNewsRepository.reset_global()
    svc = getattr(app.state, "news_service", None)
    if svc is not None:
        svc.repo = InMemoryNewsRepository()  # type: ignore[attr-defined]
        svc._recent_hashes = []
        svc._recent_hashes_set = set()
        svc.articles_ingested = 0
        svc.fetch_runs = 0
        svc.last_poll_ts = None
        if hasattr(svc, "_reload_fetchers_from_env_if_needed"):
            svc._reload_fetchers_from_env_if_needed()
    yield


@pytest.fixture(autouse=True)
def _reset_news_caches():
    cache = getattr(app.state, "news_recent_cache", None)
    if isinstance(cache, dict):
        cache.setdefault("store", {}).clear()
        cache.setdefault("order", [])
        cache["order"].clear()
    InMemoryNewsRepository._sent_cache.clear()
    buckets = getattr(app.state, "news_rate_buckets", None)
    if isinstance(buckets, dict):
        buckets.clear()
    for attr in ("_news_recent_total", "_news_recent_not_modified", "_news_recent_delta_calls"):
        if hasattr(app.state, attr):
            setattr(app.state, attr, 0)
    yield
