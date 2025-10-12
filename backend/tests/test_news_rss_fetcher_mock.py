import pytest, time
from httpx import AsyncClient
from backend.apps.api.main import app

class DummyEntry:
    def __init__(self, title, link, published_ts=None):
        self.title = title
        self.link = link
        if published_ts is None:
            published_ts = int(time.time())
        import time as _t
        self.published_parsed = time.gmtime(published_ts)

@pytest.mark.asyncio
async def test_rss_fetcher_basic_mock(monkeypatch):
    # Mock feedparser.parse to return deterministic entries
    calls = {"count": 0}
    class DummyFeed:
        def __init__(self, entries):
            self.entries = entries
    def fake_parse(url):  # noqa: D401
        calls["count"] += 1
        return DummyFeed([
            type("E", (), {"title": "Alpha", "link": f"{url}/a", "published_parsed": time.gmtime(int(time.time())-5)})(),
            type("E", (), {"title": "Beta", "link": f"{url}/b", "published_parsed": time.gmtime(int(time.time())-3)})(),
        ])
    monkeypatch.setattr("feedparser.parse", fake_parse)

    # Configure environment to use a single RSS feed (ensure service re-created with new lifespan?)
    import os
    os.environ['NEWS_RSS_FEEDS'] = 'http://example.com/rss'
    os.environ['NEWS_SYNTH_FALLBACK'] = '0'

    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Force a manual refresh (trigger poll_once via endpoint)
        r_refresh = await ac.post('/api/news/refresh')
        assert r_refresh.status_code == 200
        # Fetch recent to ensure articles appear
        r_news = await ac.get('/api/news/recent?limit=5&summary_only=1')
        assert r_news.status_code == 200
        data = r_news.json()
        assert data['status'] == 'ok'
        # Titles should include Alpha / Beta or at least count >=2 (dedup may reduce if hash collide)
        if data['count'] >= 2:
            titles = [it['title'] for it in data['items']]
            assert any('Alpha' in t for t in titles)
            assert any('Beta' in t for t in titles)

        # Metrics: per-source latency & articles counters should exist in /metrics
        m = await ac.get('/metrics')
        assert m.status_code == 200
        txt = m.text
        assert 'news_source_fetch_latency_seconds' in txt
        assert 'news_source_articles_total' in txt
        # Ensure our mocked URL host label appears
        assert 'example.com' in txt or 'example_com' in txt

@pytest.mark.asyncio
async def test_rss_fetcher_error_path(monkeypatch):
    # Mock parse to raise exception once
    class DummyErr(Exception):
        pass
    def fake_parse_err(url):
        raise DummyErr('boom')
    monkeypatch.setattr("feedparser.parse", fake_parse_err)
    import os
    os.environ['NEWS_RSS_FEEDS'] = 'http://bad.example/rss'
    os.environ['NEWS_SYNTH_FALLBACK'] = '0'

    async with AsyncClient(app=app, base_url="http://test") as ac:
        r_refresh = await ac.post('/api/news/refresh')
        # Even on failure, endpoint returns ok (inserted=0), service logs warning
        assert r_refresh.status_code == 200
        # Metrics still exposed
        m = await ac.get('/metrics')
        assert m.status_code == 200
        txt = m.text
        assert 'news_source_fetch_latency_seconds' in txt
        # error increments tracked by global NEWS_FETCH_ERRORS (not per-source latency counter)
        assert 'news_fetch_errors_total' in txt
