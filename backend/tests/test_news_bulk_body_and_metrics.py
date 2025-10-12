import pytest
from httpx import AsyncClient
from backend.apps.api.main import app

@pytest.mark.asyncio
async def test_news_bulk_body_endpoint_and_metrics(monkeypatch):
    async with AsyncClient(app=app, base_url="http://test") as ac:
        # Ensure some articles exist
        r_seed = await ac.get("/api/news/recent?limit=5&summary_only=1")
        assert r_seed.status_code == 200
        data = r_seed.json()
        ids = [str(it['id']) for it in data.get('items', [])[:3] if 'id' in it]
        if not ids:
            # Try forcing a refresh
            await ac.post('/api/news/refresh')
            r_seed2 = await ac.get("/api/news/recent?limit=5&summary_only=1")
            data = r_seed2.json()
            ids = [str(it['id']) for it in data.get('items', [])[:3] if 'id' in it]
        ids_param = ','.join(ids)
        r_bulk = await ac.get(f"/api/news/body?ids={ids_param}")
        assert r_bulk.status_code == 200
        body_items = r_bulk.json().get('items')
        assert isinstance(body_items, list)
        # Each result should have id and body (body may be None if not stored, but key should exist)
        for it in body_items:
            assert 'id' in it
            assert 'body' in it
        # Empty ids request
        r_empty = await ac.get("/api/news/body?ids=")
        assert r_empty.status_code == 200
        assert r_empty.json().get('items') == []
        # Metrics presence (rely on previous calls instrumenting news_recent metrics)
        m = await ac.get('/metrics')
        assert m.status_code == 200
        txt = m.text
        # Ensure existing metrics (sanity)
        assert 'news_recent_requests_total' in txt
        assert 'news_recent_rows_returned_total' in txt