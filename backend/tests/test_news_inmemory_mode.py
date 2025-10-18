import os, time, pytest
from httpx import AsyncClient, ASGITransport
from backend.apps.api.main import app


def make_client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")

@pytest.mark.asyncio
async def test_inmemory_news_flow(monkeypatch):
    # Enable in-memory mode
    os.environ['NEWS_TEST_INMEMORY'] = '1'
    async with make_client() as ac:
        # Initial full fetch (no data yet)
        r0 = await ac.get('/api/news/recent?limit=5&summary_only=1')
        assert r0.status_code == 200
        d0 = r0.json()
        assert d0['count'] == 0

        # Manually insert synthetic articles via service (bypassing DB)
        svc = getattr(app.state, 'news_service')
        # build fake articles
        now = int(time.time())
        articles = []
        for i in range(3):
            articles.append({
                'source': 'test_feed',
                'symbol': svc.symbol,
                'title': f'InMem Article {i}',
                'body': 'body text' if i % 2 == 0 else None,
                'url': None,
                'published_ts': now + i,
                'ingested_ts': now + i,
                'sentiment': 0.1 if i==0 else None,
                'lang': 'en',
                'hash': f'inmem-{now}-{i}'
            })
        inserted = await svc.repo.insert_articles(articles)  # type: ignore
        assert inserted == 3

        # Full fetch again should return 3
        r1 = await ac.get('/api/news/recent?limit=5&summary_only=1')
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1['count'] == 3
        latest_ts = d1['latest_ts']

        # Delta fetch with since_ts = latest_ts (no new items)
        r_delta_empty = await ac.get(f'/api/news/recent?limit=5&summary_only=1&since_ts={latest_ts}')
        assert r_delta_empty.status_code == 200
        d_empty = r_delta_empty.json()
        assert d_empty['delta'] is True
        assert d_empty['count'] == 0

        # Insert one more article (newer)
        new_article = [{
            'source': 'test_feed',
            'symbol': svc.symbol,
            'title': 'InMem New Article',
            'body': None,
            'url': None,
            'published_ts': latest_ts + 5,
            'ingested_ts': latest_ts + 5,
            'sentiment': None,
            'lang': 'en',
            'hash': f'inmem-{now}-new'
        }]
        inserted2 = await svc.repo.insert_articles(new_article)  # type: ignore
        assert inserted2 == 1

        # Delta fetch again should pick 1 new item
        r_delta_new = await ac.get(f'/api/news/recent?limit=5&summary_only=1&since_ts={latest_ts}')
        assert r_delta_new.status_code == 200
        d_new = r_delta_new.json()
        assert d_new['count'] == 1
        assert d_new['delta'] is True

        # Metrics scrape to ensure new gauges present
        m = await ac.get('/metrics')
        assert m.status_code == 200
        txt = m.text
        # Presence checks (values might be zero)
        assert 'news_recent_delta_empty_streak' in txt
        assert 'news_recent_seconds_since_new_article' in txt
        assert 'news_recent_payload_delta_avg_bytes' in txt
        assert 'news_recent_delta_payload_saving_ratio' in txt
