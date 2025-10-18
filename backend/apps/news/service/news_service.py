from __future__ import annotations
import hashlib, time, logging, random, asyncio, os
from typing import List, Dict, Any, Optional, Protocol, Iterable
from contextlib import suppress
from backend.apps.news.repository.news_repository import NewsRepository

logger = logging.getLogger(__name__)

class FetchResult(Dict[str, Any]):
    pass

class NewsFetcher(Protocol):
    async def fetch(self) -> Iterable[Dict[str, Any]]:  # returns iterable of article dicts
        ...

## SyntheticFetcher (legacy) removed: only real RSS feeds are supported now.

class RssFetcher:
    """Fetch articles from one RSS feed URL using feedparser.

    Minimal normalization; body/content omitted (can be enriched later).
    """
    def __init__(self, url: str, source_name: str | None = None, symbol: str | None = None):
        self.url = url
        self.source_name = source_name or self._derive_source(url)
        self.symbol = symbol
        self.user_agent = os.getenv(
            "NEWS_RSS_USER_AGENT",
            "pro11-news-bot/1.0 (+https://github.com/wooyoung7703/pro11)"
        )

    def _derive_source(self, url: str) -> str:
        try:
            from urllib.parse import urlparse
            netloc = urlparse(url).netloc or "rss"
            return netloc.replace(':','_')
        except Exception:
            return "rss"

    async def fetch(self) -> Iterable[Dict[str, Any]]:
        loop = asyncio.get_event_loop()
        # feedparser is sync; run in thread
        def _parse_feed():
            import feedparser as _feedparser
            import urllib.request
            headers = {"User-Agent": self.user_agent}
            req = urllib.request.Request(self.url, headers=headers)
            with urllib.request.urlopen(req, timeout=float(os.getenv("NEWS_RSS_TIMEOUT", "10"))) as resp:
                data = resp.read()
            return _feedparser.parse(data)

        feed = await loop.run_in_executor(None, _parse_feed)
        out: List[Dict[str, Any]] = []
        entries = getattr(feed, 'entries', []) or []
        for e in entries:
            title = getattr(e, 'title', None) or getattr(e, 'id', None)
            link = getattr(e, 'link', None)
            published_ts = None
            # Try published_parsed → struct_time
            with suppress(Exception):
                st = getattr(e, 'published_parsed', None) or getattr(e, 'updated_parsed', None)
                if st:
                    published_ts = int(time.mktime(st))
            if not published_ts:
                published_ts = int(time.time())
            ingested_ts = int(time.time())
            # Try to extract a lightweight summary/content to store as body (so UI can show something on expand)
            summary = None
            with suppress(Exception):
                summary = getattr(e, 'summary', None) or getattr(e, 'description', None)
            # Some feeds provide content list
            if (not summary) and hasattr(e, 'content'):
                try:
                    c0 = e.content[0]
                    summary = getattr(c0, 'value', None) or getattr(c0, 'content', None)
                except Exception:
                    pass
            # Optional: strip very long HTML tags crudely (keep simple to avoid heavy deps)
            if summary and '<' in summary and '>' in summary:
                import re as _re
                # remove tags, collapse whitespace
                txt = _re.sub(r'<[^>]+>', ' ', summary)
                txt = _re.sub(r'\s+', ' ', txt).strip()
                if txt:
                    summary = txt
            base_hash = f"{link}|{published_ts}|{title}" if title else f"{link}|{published_ts}"
            h = hashlib.sha256(base_hash.encode()).hexdigest()[:48]
            out.append({
                "source": self.source_name,
                "symbol": self.symbol,
                "title": title or "(no-title)",
                # Store summary (if any) as body so expansion shows content even without full scraping
                "body": summary,
                "url": link,
                "published_ts": published_ts,
                "ingested_ts": ingested_ts,
                "hash": h,
                "sentiment": None,
                "lang": getattr(e, 'language', None) or 'en',
            })
        return out
DEFAULT_RSS_FEEDS: tuple[str, ...] = (
    "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
    "https://cointelegraph.com/rss",
    "https://cryptonews.com/news/feed/",
)

def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class NewsService:
    def __init__(self, sources: Optional[List[str]] = None, symbol: str | None = None, rss_feeds: Optional[List[str]] = None):
        self.symbol = symbol
        self.repo = NewsRepository()
        self.last_poll_ts: float | None = None
        self.last_ingested_ts: float | None = None
        self.running = False
        self.error_count = 0
        self.fetch_runs = 0
        self.articles_ingested = 0
        # Synthetic fallback removed: force real sources only
        self.synthetic_enabled = False  # legacy env NEWS_SYNTH_FALLBACK ignored
        self.fetchers: List[NewsFetcher] = []
        # Per-source backoff tracking
        self._src_err_streak: dict[str, int] = {}
        self._src_next_allowed: dict[str, float] = {}
        self._backoff_min = float(os.getenv("NEWS_SRC_BACKOFF_MIN", "5"))  # seconds
        self._backoff_max = float(os.getenv("NEWS_SRC_BACKOFF_MAX", "600"))  # cap 10m default
        self._fetch_cap = int(os.getenv("NEWS_FETCH_MAX_PER_SOURCE", "0"))  # 0 == unlimited
        self.disabled_sources: set[str] = set()
        # Rolling dedup ratio tracking: source -> deque[(raw, unique)]
        from collections import deque
        # dedup history now stores tuples: (raw, kept, ts)
        self._dedup_history: dict[str, deque[tuple[int,int,float]]] = {}
        self._dedup_window = int(os.getenv("NEWS_DEDUP_ROLLING_WINDOW", "20"))  # number of polls
        # Persistent hash buffer config
        self._hash_persist_path = os.getenv("NEWS_HASH_BUFFER_PATH", "news_hash_buffer.txt")
        self._hash_persist_limit = int(os.getenv("NEWS_HASH_BUFFER_LIMIT", "5000"))
        self._recent_hashes: list[str] = []
        # Last computed health scores cache
        self._last_health_scores: dict[str, float] = {}
        self._last_health_components: dict[str, dict[str, float]] = {}
        self._health_state: dict[str, bool] = {}  # True=healthy (>=threshold), False=unhealthy
        self._health_threshold = float(os.getenv("NEWS_HEALTH_THRESHOLD", "0.3"))
        # Stall detection threshold (seconds since last ingest per source)
        self._stall_threshold = float(os.getenv("NEWS_SOURCE_STALL_THRESHOLD_SEC", "900"))
        self._stall_state: dict[str, bool] = {}  # True=currently stalled
        # Per-source last ingest ts tracking (for stall detection)
        self._src_last_ingest: dict[str, float] = {}
        self._default_feeds_active = False
        # Dedup anomaly detection config & state
        self._dedup_min_ratio = float(os.getenv("NEWS_DEDUP_MIN_RATIO", "0.15"))  # below this considered low
        self._dedup_ratio_streak_req = int(os.getenv("NEWS_DEDUP_RATIO_STREAK", "3"))  # consecutive polls
        self._dedup_vol_window = int(os.getenv("NEWS_DEDUP_VOLATILITY_WINDOW", "10"))  # polls
        self._dedup_vol_threshold = float(os.getenv("NEWS_DEDUP_VOLATILITY_THRESHOLD", "0.25"))  # stddev threshold
        self._dedup_low_ratio_streak: dict[str, int] = {}
        self._dedup_anomaly_state: dict[str, bool] = {}  # True when anomaly active
        self._dedup_vol_buffers: dict[str, list[float]] = {}  # recent ratios for volatility
        self._dedup_last_ratio: dict[str, float] = {}
        # Load persisted hashes (best-effort)
        try:
            if os.path.exists(self._hash_persist_path):
                with open(self._hash_persist_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        h = line.strip()
                        if h:
                            self._recent_hashes.append(h)
                            if len(self._recent_hashes) >= self._hash_persist_limit:
                                break
        except Exception:
            pass
        self._recent_hashes_set = set(self._recent_hashes)
        # record metric for preload size (best-effort)
        try:
            from backend.apps.api.main import NEWS_DEDUP_HASH_PRELOAD
            NEWS_DEDUP_HASH_PRELOAD.set(len(self._recent_hashes))
        except Exception:
            pass
        rss_env = rss_feeds or self._resolve_feed_urls()
        # Add RSS fetchers first (real sources)
        for url in rss_env:
            self.fetchers.append(RssFetcher(url=url, symbol=symbol))
        # No synthetic fallback: warn if empty
        if not self.fetchers:
            logger.warning("NewsService started with 0 RSS feeds configured (no synthetic fallback). Add feeds via env NEWS_RSS_FEEDS or runtime API.")

        self.sources = [getattr(f, 'source_name', getattr(f, 'source', 'unknown')) for f in self.fetchers]
        self._rss_env_signature = self._feeds_signature(rss_env)
        # --- Debug / verbose tracking structures ---
        self._dbg_last_error = {}
        self._dbg_last_error_ts = {}
        self._dbg_last_success_ts = {}
        self._dbg_last_fetch_latency = {}
        self._dbg_attempt_ts = {}
        self._dbg_err_streak = {}

    def _feeds_signature(self, feeds: Iterable[str]) -> str:
        return ",".join(sorted(feeds))

    def _parse_rss_env(self) -> List[str]:
        raw = os.getenv("NEWS_RSS_FEEDS", "").strip()
        if not raw:
            return []
        parts = [p.strip() for p in raw.split(',') if p.strip()]
        return parts

    def _resolve_feed_urls(self) -> List[str]:
        feeds = self._parse_rss_env()
        if not feeds and _env_flag("NEWS_ENABLE_DEFAULT_FEEDS", True):
            feeds = list(DEFAULT_RSS_FEEDS)
            if not getattr(self, "_default_feeds_active", False):
                logger.info(
                    "NewsService using built-in default RSS feeds; set NEWS_RSS_FEEDS to override or NEWS_ENABLE_DEFAULT_FEEDS=0 to disable"
                )
            self._default_feeds_active = True
        else:
            self._default_feeds_active = False
        return feeds

    def _reload_fetchers_from_env_if_needed(self) -> None:
        feeds = self._resolve_feed_urls()
        signature = self._feeds_signature(feeds)
        if getattr(self, "_rss_env_signature", None) == signature:
            return
        self.fetchers = [RssFetcher(url=url, symbol=self.symbol) for url in feeds]
        self.sources = [getattr(f, 'source_name', getattr(f, 'source', 'unknown')) for f in self.fetchers]
        self._rss_env_signature = signature
        if not self.fetchers:
            logger.warning("NewsService reloaded with 0 RSS feeds (env NEWS_RSS_FEEDS empty)")

    async def poll_once(self) -> int:
        self._reload_fetchers_from_env_if_needed()
        self.last_poll_ts = time.time()
        # Empty DB dedup preload bypass (one-time check)
        if not getattr(self, '_dedup_preload_checked', False):
            try:
                db_rows = await self.repo.row_count()  # type: ignore[attr-defined]
            except Exception:
                db_rows = -1
            if db_rows == 0 and len(self._recent_hashes_set) > 0:
                logger.warning(
                    "dedup_preload_ignored_empty_db preload_size=%d", len(self._recent_hashes_set)
                )
                # Clear so first real poll can insert fresh rows
                self._recent_hashes_set.clear()
                self._recent_hashes.clear()
            self._dedup_preload_checked = True
        collected: List[Dict[str, Any]] = []
        # temp structures for dedup stats
        raw_counts: dict[str, int] = {}
        unique_counts: dict[str, int] = {}
        # start dedup set seeded with persisted recent hashes
        seen_hashes: set[str] = set(self._recent_hashes_set)
        per_source_latency: dict[str, float] = {}
        per_source_errors: dict[str, int] = {}
        for fetcher in self.fetchers:
            t0 = time.perf_counter()
            source_label = getattr(fetcher, 'source_name', getattr(fetcher, 'source', 'unknown'))
            # Backoff skip check
            now = time.time()
            self._dbg_attempt_ts[source_label] = now
            if source_label in self.disabled_sources:
                try:
                    from backend.apps.api.main import NEWS_SOURCE_DISABLED_SKIPS
                    NEWS_SOURCE_DISABLED_SKIPS.labels(source=source_label).inc()
                except Exception:
                    pass
                continue
            next_allowed = self._src_next_allowed.get(source_label)
            if next_allowed and now < next_allowed:
                # observe zero-latency skip via latency histogram (optional) but skip fetch
                try:
                    from backend.apps.api.main import NEWS_SOURCE_BACKOFF_SKIPS
                    NEWS_SOURCE_BACKOFF_SKIPS.labels(source=source_label).inc()
                except Exception:
                    pass
                continue
            try:
                items = await fetcher.fetch()
                # Items may be generator or list
                yielded = 0
                # Apply size cap if configured
                if self._fetch_cap and hasattr(items, '__len__'):
                    try:
                        if len(items) > self._fetch_cap:  # type: ignore[arg-type]
                            try:
                                from backend.apps.api.main import NEWS_SOURCE_FETCH_TRUNCATED
                                NEWS_SOURCE_FETCH_TRUNCATED.labels(source=source_label).inc()
                            except Exception:
                                pass
                            if isinstance(items, list):
                                items = items[:self._fetch_cap]
                            else:
                                # generic iterable -> slice manually
                                items = list(items)[:self._fetch_cap]
                    except Exception:
                        pass
                for it in items:
                    # Ensure mandatory keys
                    if 'published_ts' not in it:
                        it['published_ts'] = int(time.time())
                    if 'hash' not in it:
                        base = f"{it.get('url')}|{it.get('published_ts')}|{it.get('title')}"
                        it['hash'] = hashlib.sha256(base.encode()).hexdigest()[:48]
                    raw_counts[source_label] = raw_counts.get(source_label, 0) + 1
                    hval = it['hash']
                    if hval not in seen_hashes:
                        if 'ingested_ts' not in it or not it['ingested_ts']:
                            it['ingested_ts'] = int(time.time())
                        seen_hashes.add(hval)
                        collected.append(it)
                        unique_counts[source_label] = unique_counts.get(source_label, 0) + 1
                        yielded += 1
                # per-source article count metric (pre-dedup)
                try:  # defer import; avoid circular
                    from backend.apps.api.main import NEWS_SOURCE_FETCH_ARTICLES
                    NEWS_SOURCE_FETCH_ARTICLES.labels(source=source_label).inc(raw_counts.get(source_label,0))
                except Exception:
                    pass
                # success path resets backoff streak
                if source_label in self._src_err_streak:
                    self._src_err_streak[source_label] = 0
                    self._src_next_allowed[source_label] = 0.0
                # debug success
                self._dbg_last_success_ts[source_label] = time.time()
                self._dbg_last_error.pop(source_label, None)
                self._dbg_last_error_ts.pop(source_label, None)
                self._dbg_err_streak[source_label] = 0
            except Exception as e:  # noqa: BLE001
                logger.warning("news_fetcher_error source=%s err=%s", source_label, e)
                self.error_count += 1
                per_source_errors[source_label] = per_source_errors.get(source_label,0) + 1
                # per-source fetch error counter
                try:
                    from backend.apps.api.main import NEWS_SOURCE_FETCH_ERRORS
                    NEWS_SOURCE_FETCH_ERRORS.labels(source=source_label).inc()
                except Exception:
                    pass
                # backoff update
                streak = self._src_err_streak.get(source_label, 0) + 1
                self._src_err_streak[source_label] = streak
                self._dbg_err_streak[source_label] = streak
                self._dbg_last_error[source_label] = repr(e)[:300]
                self._dbg_last_error_ts[source_label] = time.time()
                backoff = min(self._backoff_min * (2 ** (streak - 1)), self._backoff_max)
                self._src_next_allowed[source_label] = time.time() + backoff
                try:
                    from backend.apps.api.main import Gauge as _Gauge
                    # define lazily if not exists (idempotent). We'll attach attribute on module for reuse
                    from backend.apps.api import main as _main_mod
                    if not hasattr(_main_mod, 'NEWS_SOURCE_BACKOFF_SECONDS'):
                        _main_mod.NEWS_SOURCE_BACKOFF_SECONDS = _Gauge(
                            'news_source_backoff_seconds',
                            'Current backoff remaining seconds before next fetch attempt (approx, updated on error)',
                            ['source']
                        )
                    rem = max(0.0, self._src_next_allowed[source_label] - time.time())
                    _main_mod.NEWS_SOURCE_BACKOFF_SECONDS.labels(source=source_label).set(rem)
                except Exception:
                    pass
            finally:
                elapsed = time.perf_counter() - t0
                per_source_latency[source_label] = elapsed
                self._dbg_last_fetch_latency[source_label] = elapsed
                try:
                    from backend.apps.api.main import NEWS_SOURCE_FETCH_LATENCY
                    NEWS_SOURCE_FETCH_LATENCY.labels(source=source_label).observe(elapsed)
                except Exception:
                    pass
        inserted = 0
        if collected:
            try:
                inserted = await self.repo.insert_articles(collected)
                if inserted:
                    self.last_ingested_ts = time.time()
                    self.articles_ingested += inserted
                    # Update total gauge & per-source gauge (best-effort)
                    try:
                        from backend.apps.api.main import NEWS_ARTICLES_TOTAL, NEWS_ARTICLES_SOURCE_TOTAL, init_pool as _init_pool
                        pool = await _init_pool()
                        if pool is not None:
                            async with pool.acquire() as _conn:  # type: ignore
                                total_rows = await _conn.fetchval("SELECT COUNT(*) FROM news_articles")
                                if total_rows is not None:
                                    NEWS_ARTICLES_TOTAL.set(int(total_rows))
                                # Per-source counts
                                rows = await _conn.fetch("SELECT source, COUNT(*) AS c FROM news_articles GROUP BY source")
                                for r in rows:
                                    NEWS_ARTICLES_SOURCE_TOTAL.labels(source=r['source']).set(int(r['c']))
                    except Exception:
                        pass
                    # dedup metrics update
                    try:
                        from backend.apps.api.main import (
                            NEWS_SOURCE_DEDUP_DROPPED,
                            NEWS_SOURCE_DEDUP_INSERTS,
                            NEWS_SOURCE_DEDUP_RATIO,
                            NEWS_SOURCE_DEDUP_RATIO_ROLLING,
                        )
                        for src, raw in raw_counts.items():
                            kept = unique_counts.get(src, 0)
                            dropped = raw - kept
                            if dropped > 0:
                                NEWS_SOURCE_DEDUP_DROPPED.labels(source=src).inc(dropped)
                            if kept > 0:
                                NEWS_SOURCE_DEDUP_INSERTS.labels(source=src).inc(kept)
                                # update per-source last ingest timestamp (any kept article counts)
                                self._src_last_ingest[src] = time.time()
                            if raw > 0:
                                NEWS_SOURCE_DEDUP_RATIO.labels(source=src).set(kept / raw)
                                # per-poll latest ratio gauge (for anomaly logic visibility)
                                try:
                                    from backend.apps.api.main import NEWS_SOURCE_DEDUP_RATIO_GAUGE
                                    NEWS_SOURCE_DEDUP_RATIO_GAUGE.labels(source=src).set(kept / raw)
                                except Exception:
                                    pass
                            # rolling window update
                            if raw > 0:
                                if src not in self._dedup_history:
                                    from collections import deque as _dq
                                    self._dedup_history[src] = _dq(maxlen=self._dedup_window)
                                self._dedup_history[src].append((raw, kept, time.time()))
                                total_raw = sum(r for r,_,__ in self._dedup_history[src])
                                total_kept = sum(k for _,k,__ in self._dedup_history[src])
                                if total_raw > 0:
                                    NEWS_SOURCE_DEDUP_RATIO_ROLLING.labels(source=src, window=str(self._dedup_window)).set(total_kept/total_raw)
                            # --- Dedup anomaly tracking (per poll, even if raw>0) ---
                            try:
                                ratio = (kept / raw) if raw > 0 else 0.0
                                self._dedup_last_ratio[src] = ratio
                                # Low ratio streak logic
                                if ratio < self._dedup_min_ratio and raw > 0:
                                    self._dedup_low_ratio_streak[src] = self._dedup_low_ratio_streak.get(src, 0) + 1
                                else:
                                    # reset streak if ratio recovers
                                    if self._dedup_low_ratio_streak.get(src, 0) != 0:
                                        self._dedup_low_ratio_streak[src] = 0
                                # Volatility buffer
                                buf = self._dedup_vol_buffers.setdefault(src, [])
                                buf.append(ratio)
                                if len(buf) > self._dedup_vol_window:
                                    # pop oldest
                                    buf.pop(0)
                                vol_spike = False
                                if len(buf) >= max(5, self._dedup_vol_window//2):
                                    import statistics
                                    if len(set(buf)) > 1:  # avoid zero std edge case
                                        std = statistics.pstdev(buf)
                                    else:
                                        std = 0.0
                                    try:
                                        from backend.apps.api.main import NEWS_SOURCE_DEDUP_VOLATILITY
                                        NEWS_SOURCE_DEDUP_VOLATILITY.labels(source=src).set(std)
                                    except Exception:
                                        pass
                                    if std >= self._dedup_vol_threshold:
                                        vol_spike = True
                                # Determine anomaly activation
                                active = self._dedup_anomaly_state.get(src, False)
                                low_ratio_trigger = self._dedup_low_ratio_streak.get(src,0) >= self._dedup_ratio_streak_req and self._dedup_ratio_streak_req > 0
                                new_active = active
                                anomaly_event: str | None = None
                                if not active and (low_ratio_trigger or vol_spike):
                                    new_active = True
                                    anomaly_event = 'enter'
                                    # finer grained reason event
                                    if vol_spike:
                                        anomaly_event_reason = 'volatility'
                                    elif low_ratio_trigger:
                                        anomaly_event_reason = 'low_ratio'
                                    else:
                                        anomaly_event_reason = 'enter'
                                elif active and (not low_ratio_trigger and not vol_spike):
                                    new_active = False
                                    anomaly_event = 'exit'
                                    anomaly_event_reason = 'exit'
                                if new_active != active:
                                    self._dedup_anomaly_state[src] = new_active
                                    try:
                                        from backend.apps.api.main import NEWS_SOURCE_DEDUP_ANOMALY, NEWS_SOURCE_DEDUP_ANOMALY_EVENTS
                                        NEWS_SOURCE_DEDUP_ANOMALY.labels(source=src).set(1 if new_active else 0)
                                        if anomaly_event:
                                            NEWS_SOURCE_DEDUP_ANOMALY_EVENTS.labels(source=src, event=anomaly_event_reason).inc()
                                    except Exception:
                                        pass
                                else:
                                    # keep gauge state fresh even without transition
                                    try:
                                        from backend.apps.api.main import NEWS_SOURCE_DEDUP_ANOMALY
                                        NEWS_SOURCE_DEDUP_ANOMALY.labels(source=src).set(1 if active else 0)
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                    except Exception:
                        pass
                # raw->insert ratio gauge update (after attempted insert)
                try:
                    from backend.apps.api.main import NEWS_SOURCE_FETCH_ARTICLES, NEWS_INGEST_RAW_RATIO
                    total_raw = 0
                    for metric in NEWS_SOURCE_FETCH_ARTICLES.collect():  # type: ignore
                        for sample in metric.samples:
                            if sample.name == 'news_source_articles_total':
                                total_raw += sample.value or 0
                    if total_raw > 0:
                        NEWS_INGEST_RAW_RATIO.set(self.articles_ingested / total_raw)
                except Exception:
                    pass
                # Persist new hashes (append) best-effort
                try:
                    if collected:
                        new_hashes = [it['hash'] for it in collected if it['hash'] not in self._recent_hashes_set]
                        if new_hashes:
                            self._recent_hashes.extend(new_hashes)
                            for nh in new_hashes:
                                self._recent_hashes_set.add(nh)
                            # trim
                            if len(self._recent_hashes) > self._hash_persist_limit:
                                drop = len(self._recent_hashes) - self._hash_persist_limit
                                if drop > 0:
                                    for old in self._recent_hashes[:drop]:
                                        self._recent_hashes_set.discard(old)
                                    self._recent_hashes = self._recent_hashes[drop:]
                            # write file (overwrite compact list)
                            with open(self._hash_persist_path, 'w', encoding='utf-8') as f:
                                f.write("\n".join(self._recent_hashes))
                except Exception:
                    pass
            except Exception as e:
                logger.warning("news_poll_insert_failed err=%s", e)
                self.error_count += 1
        self.fetch_runs += 1
        # Composite health score emit (best-effort)
        try:
            from backend.apps.api.main import NEWS_SOURCE_HEALTH_SCORE
            # Heuristic weights
            for src in self.sources:
                # Latency score: assume target <2s, degrade to 0 at 10s
                lat = per_source_latency.get(src, 2.0)
                lat_score = 1.0 if lat <= 2 else max(0.0, 1 - (lat - 2) / 8)
                # Stall detection update
                now_ts = time.time()
                last_ing = self._src_last_ingest.get(src)
                stalled = False
                if last_ing is not None:
                    if (now_ts - last_ing) > self._stall_threshold:
                        stalled = True
                # state transition events
                prev_stalled = self._stall_state.get(src)
                if prev_stalled is None:
                    self._stall_state[src] = stalled
                else:
                    if prev_stalled and not stalled:
                        # recover
                        try:
                            from backend.apps.api.main import NEWS_SOURCE_STALL_EVENTS, NEWS_SOURCE_STALLED
                            NEWS_SOURCE_STALL_EVENTS.labels(source=src, event="recover").inc()
                            NEWS_SOURCE_STALLED.labels(source=src).set(0)
                        except Exception:
                            pass
                        self._stall_state[src] = stalled
                    elif (not prev_stalled) and stalled:
                        try:
                            from backend.apps.api.main import NEWS_SOURCE_STALL_EVENTS, NEWS_SOURCE_STALLED
                            NEWS_SOURCE_STALL_EVENTS.labels(source=src, event="stall").inc()
                            NEWS_SOURCE_STALLED.labels(source=src).set(1)
                        except Exception:
                            pass
                        self._stall_state[src] = stalled
                # always set last ingest timestamp gauge if available
                if last_ing is not None:
                    try:
                        from backend.apps.api.main import NEWS_SOURCE_LAST_INGEST_TS
                        NEWS_SOURCE_LAST_INGEST_TS.labels(source=src).set(last_ing)
                    except Exception:
                        pass
                # Error/backoff penalty: if currently in backoff window reduce
                now = time.time()
                next_allowed = self._src_next_allowed.get(src, 0.0)
                backoff_pen = 0.0
                if next_allowed > now:
                    # scale penalty up to 0.7 as remaining backoff increases vs max
                    rem = next_allowed - now
                    backoff_pen = min(0.7, rem / max(1.0, self._backoff_max) * 0.7)
                err_pen = 0.0
                if src in per_source_errors:
                    err_pen = 0.3  # simple flat penalty when error occurred this poll
                stall_pen = 0.0
                if self._stall_state.get(src):
                    stall_pen = 0.5  # strong penalty when stalled
                # Dedup efficiency: prefer some duplication (too low unique ratio may mean feed stalled or all duplicates)
                # Use last rolling ratio if available
                roll = 0.0
                hist = self._dedup_history.get(src)
                if hist:
                    total_raw = sum(r for r,_ in hist)
                    total_kept = sum(k for _,k in hist)
                    if total_raw>0:
                        roll = total_kept/total_raw
                # Dedup score: ideal between 0.4 and 0.9 -> map extremes down
                if roll == 0:
                    dedup_score = 0.3  # maybe stalled
                else:
                    # if roll <0.2 maybe too many dups (score down), if >0.95 maybe suspicious identical (?)
                    if roll < 0.2:
                        dedup_score = max(0.1, roll/0.2 * 0.5)
                    elif roll > 0.95:
                        dedup_score = max(0.4, 1 - (roll-0.95)/0.05 * 0.6)
                    else:
                        dedup_score = 0.9  # healthy band
                # Combine
                base = 0.5*lat_score + 0.25*dedup_score + 0.25  # adjust weights slightly to make room for stall
                penalty = backoff_pen + err_pen + stall_pen
                score = max(0.0, min(1.0, base - penalty))
                NEWS_SOURCE_HEALTH_SCORE.labels(source=src).set(score)
                self._last_health_scores[src] = score
                self._last_health_components[src] = {
                    "latency_seconds": lat,
                    "lat_score": lat_score,
                    "dedup_ratio": roll,
                    "dedup_score": dedup_score,
                    "dedup_low_ratio_streak": self._dedup_low_ratio_streak.get(src, 0),
                    "dedup_anomaly": 1 if self._dedup_anomaly_state.get(src) else 0,
                    "dedup_last_ratio": self._dedup_last_ratio.get(src),
                    "backoff_pen": backoff_pen,
                    "err_pen": err_pen,
                    "stall_pen": stall_pen,
                    "stalled": 1 if self._stall_state.get(src) else 0,
                    "stall_threshold_sec": self._stall_threshold,
                    "since_last_ingest": (time.time() - last_ing) if last_ing else None,
                    "base_pre_penalty": base,
                    "final_score": score,
                }
                # threshold crossing detection
                try:
                    from backend.apps.api.main import NEWS_SOURCE_HEALTH_EVENTS
                    prev = self._health_state.get(src)
                    healthy = score >= self._health_threshold
                    if prev is None:
                        self._health_state[src] = healthy
                    else:
                        if prev and not healthy:
                            NEWS_SOURCE_HEALTH_EVENTS.labels(source=src, event="drop").inc()
                            self._health_state[src] = healthy
                        elif (not prev) and healthy:
                            NEWS_SOURCE_HEALTH_EVENTS.labels(source=src, event="recover").inc()
                            self._health_state[src] = healthy
                except Exception:
                    pass
        except Exception:
            pass
        return inserted

    def health_snapshot(self) -> list[dict[str, any]]:
        out = []
        now = time.time()
        for src in self.sources:
            score = self._last_health_scores.get(src)
            backoff_rem = 0.0
            na = self._src_next_allowed.get(src, 0.0)
            if na > now:
                backoff_rem = max(0.0, na - now)
            comps = self._last_health_components.get(src) or {}
            last_ing = self._src_last_ingest.get(src)
            since_last = (now - last_ing) if last_ing else None
            out.append({
                "source": src,
                "score": score,
                "disabled": src in self.disabled_sources,
                "backoff_remaining": backoff_rem,
                "stalled": bool(self._stall_state.get(src, False)),
                "since_last_ingest": since_last,
                "stall_threshold_sec": self._stall_threshold,
                "dedup_anomaly": bool(self._dedup_anomaly_state.get(src, False)),
                "dedup_low_ratio_streak": self._dedup_low_ratio_streak.get(src,0),
                "dedup_last_ratio": self._dedup_last_ratio.get(src),
                "components": comps,
            })
        return out

    def dedup_history_snapshot(self) -> dict[str, list[dict[str, float]]]:
        """Return per-source recent poll dedup history: list of {ts, raw, kept, ratio}."""
        out: dict[str, list[dict[str, float]]] = {}
        for src, dq in self._dedup_history.items():
            series = []
            for raw, kept, ts in dq:
                ratio = (kept / raw) if raw > 0 else 0.0
                series.append({"ts": ts, "raw": float(raw), "kept": float(kept), "ratio": ratio})
            out[src] = series
        return out

    async def run_loop(self, interval: int = 90):
        if self.running:
            return
        self.running = True
        logger.info("NewsService loop started interval=%s", interval)
        try:
            while self.running:
                try:
                    await self.poll_once()
                except Exception as e:
                    logger.warning("news_poll_failed err=%s", e)
                    self.error_count += 1
                await asyncio.sleep(interval)
        finally:
            self.running = False
            logger.info("NewsService loop stopped")

    def status(self) -> Dict[str, Any]:
        now = time.time()
        lag = None if not self.last_ingested_ts else now - self.last_ingested_ts
        return {
            "running": self.running,
            "last_run_ts": self.last_poll_ts,
            "last_ingested_ts": self.last_ingested_ts,
            "lag_seconds": lag,
            "fetch_runs": self.fetch_runs,
            "fetch_errors": self.error_count,
            "articles_ingested": self.articles_ingested,
            "sources": self.sources,
            "symbol": self.symbol,
            "disabled_sources": sorted(list(self.disabled_sources)),
        }

    # Runtime toggle helpers
    def disable_source(self, source: str) -> bool:
        if source not in self.sources:
            return False
        self.disabled_sources.add(source)
        return True

    def enable_source(self, source: str) -> bool:
        if source in self.disabled_sources:
            self.disabled_sources.remove(source)
            return True
        return False

    def add_rss_feed(self, url: str, symbol: str | None = None) -> str:
        f = RssFetcher(url=url, symbol=symbol)
        self.fetchers.append(f)
        name = getattr(f, 'source_name', 'unknown')
        if name not in self.sources:
            self.sources.append(name)
        return name

    # ------------------------------------------------------------------
    # Bootstrap Backfill
    async def bootstrap_backfill(
        self,
        rounds: int = 3,
        sleep_seconds: float = 5.0,
        recent_days: int = 7,
        min_total: int | None = None,
        per_round_backfill_days: int | None = None,
    ) -> dict[str, any]:
        """Perform multi-round bootstrap ingestion when DB is empty or stale.

        Strategy:
        - Each round performs a normal poll_once() (live fetch) first.
        - Optionally performs an RSS historical backfill limited to `per_round_backfill_days` (or `recent_days` if None).
        - Stops early if `min_total` articles ingested overall (since service start) or new inserts plateau.

        Args:
          rounds: 최대 라운드 수.
          sleep_seconds: 라운드 사이 대기.
          recent_days: backfill 시 사용하는 기본 days 컷오프.
          min_total: 도달하면 조기 종료할 최소 누적 기사 수 (DB 전체 기준이 아닌 service.ingested 증가 관찰).
          per_round_backfill_days: 라운드 별 backfill days (없으면 recent_days 사용).

        Returns: dict with detailed progress metrics.
        """
        if rounds <= 0:
            rounds = 1
        stats: list[dict[str, any]] = []
        start_ingested = self.articles_ingested
        total_new = 0
        plateau_rounds = 0
        last_cumulative = 0
        # Guard to avoid overlapping bootstrap runs
        if getattr(self, '_bootstrap_running', False):
            return {"status": "already-running"}
        self._bootstrap_running = True
        self._bootstrap_started_ts = time.time()
        try:
            for r in range(1, rounds+1):
                round_info: dict[str, any] = {"round": r}
                try:
                    inserted_live = await self.poll_once()
                except Exception as e:  # noqa: BLE001
                    inserted_live = 0
                    round_info['poll_error'] = repr(e)[:300]
                round_info['inserted_live'] = inserted_live
                # Optional historical backfill (reuse simple logic similar to /api/news/backfill)
                backfill_days = per_round_backfill_days or recent_days
                cutoff = int(time.time()) - backfill_days * 86400
                # Lightweight per-source fetch; we dedup via hash same as poll
                inserted_backfill = 0
                per_source = {}
                try:
                    seen: set[str] = set()
                    for f in self.fetchers:
                        sname = getattr(f, 'source_name', getattr(f, 'source', 'unknown'))
                        try:
                            items = await f.fetch()
                        except Exception:
                            per_source[sname] = 0
                            continue
                        collected = []
                        kept = 0
                        for it in items:
                            try:
                                if 'published_ts' not in it:
                                    it['published_ts'] = int(time.time())
                                if it['published_ts'] < cutoff:
                                    continue
                                if 'hash' not in it:
                                    base = f"{it.get('url')}|{it.get('published_ts')}|{it.get('title')}"
                                    it['hash'] = hashlib.sha256(base.encode()).hexdigest()[:48]
                                hval = it['hash']
                                if hval in seen:
                                    continue
                                seen.add(hval)
                                collected.append(it)
                                kept += 1
                            except Exception:
                                continue
                        if collected:
                            try:
                                ins = await self.repo.insert_articles(collected)  # type: ignore
                                inserted_backfill += ins
                            except Exception:
                                pass
                        per_source[sname] = kept
                except Exception as e:  # noqa: BLE001
                    round_info['backfill_error'] = repr(e)[:300]
                round_info['inserted_backfill'] = inserted_backfill
                round_info['backfill_days'] = backfill_days
                round_info['per_source_backfill'] = per_source
                # Cumulative stats
                cumulative = self.articles_ingested - start_ingested
                gained = cumulative - last_cumulative
                round_info['cumulative_new'] = cumulative
                round_info['delta_new'] = gained
                last_cumulative = cumulative
                total_new = cumulative
                stats.append(round_info)
                # Plateau detection (no new articles this round)
                if gained == 0:
                    plateau_rounds += 1
                else:
                    plateau_rounds = 0
                # Early exit conditions
                if min_total and total_new >= min_total:
                    round_info['early_exit'] = 'min_total_reached'
                    break
                if plateau_rounds >= 2:  # two consecutive rounds with 0 new
                    round_info['early_exit'] = 'plateau'
                    break
                if r < rounds:
                    await asyncio.sleep(sleep_seconds)
        finally:
            self._bootstrap_running = False
            self._bootstrap_finished_ts = time.time()
        return {
            "status": "ok",
            "rounds_requested": rounds,
            "rounds_run": len(stats),
            "total_new": total_new,
            "stats": stats,
            "elapsed_sec": (self._bootstrap_finished_ts - self._bootstrap_started_ts) if getattr(self, '_bootstrap_finished_ts', None) else None,
        }
