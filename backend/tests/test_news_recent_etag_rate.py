import pytest, time
from httpx import AsyncClient, ASGITransport
from backend.apps.api.main import app
import re


def make_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")

@pytest.mark.asyncio
async def test_news_recent_etag_304_and_payload_metrics(monkeypatch):
    async with make_client() as ac:
        r1 = await ac.get("/api/news/recent?limit=5&summary_only=1")
        assert r1.status_code == 200
        etag = r1.json().get('etag')
        assert etag
        # second call with If-None-Match should return 304
        r2 = await ac.get("/api/news/recent?limit=5&summary_only=1", headers={"If-None-Match": etag})
        # ETag cache hit may return 304 or 200 depending if poll inserted new article; allow both but assert logic
        assert r2.status_code in (200, 304)
        if r2.status_code == 304:
            # ensure empty body
            assert r2.text == ''
        else:
            # new data changed etag
            assert r2.json().get('etag') != etag
        # metrics scrape to ensure counters exist and increment at least once
        m = await ac.get('/metrics')
        assert m.status_code == 200
        txt = m.text
        # ensure metric names present
        assert 'news_recent_requests_total' in txt
        assert 'news_recent_payload_bytes_total' in txt
        assert 'news_recent_not_modified_total' in txt  # may be zero
        # Basic regex to extract not modified counter (allow absence of sample if no 304 yet)
        nm_match = re.search(r'^news_recent_not_modified_total\s+(\d+)', txt, re.MULTILINE)
        assert nm_match is not None

@pytest.mark.asyncio
async def test_news_recent_rate_limit_full_and_delta(monkeypatch):
    # tighten limits for test speed
    import os
    os.environ['NEWS_FULL_RATE_LIMIT'] = '3'
    os.environ['NEWS_DELTA_RATE_LIMIT'] = '6'
    async with make_client() as ac:
        # FULL calls (no since_ts)
        codes_full = []
        for _ in range(5):
            r = await ac.get("/api/news/recent?limit=2&summary_only=1")
            codes_full.append(r.status_code)
            if r.status_code == 200:
                latest_ts = r.json().get('latest_ts')
        # Expect first 3 ok then 429
        assert codes_full.count(200) <= 3
        assert 429 in codes_full
        # DELTA calls using last latest_ts (if present)
        if 'latest_ts' in locals() and latest_ts is not None:
            codes_delta = []
            for _ in range(8):
                r = await ac.get(f"/api/news/recent?limit=2&summary_only=1&since_ts={latest_ts}")
                codes_delta.append(r.status_code)
            # Allow >3 up to 6 before 429
            assert codes_delta.count(200) <= 6
            assert 429 in codes_delta
        # metrics check for rate limited counter label modes
        m = await ac.get('/metrics')
        assert m.status_code == 200
        txt = m.text
        assert 'news_recent_rate_limited_total' in txt
        # modes full/delta appear at least once in metrics exposition (even if zero value)
        assert 'mode="full"' in txt
        assert 'mode="delta"' in txt
