import pytest, asyncio, time
from httpx import AsyncClient
from backend.apps.api.main import app

@pytest.mark.asyncio
async def test_news_recent_since_ts_delta_flow(monkeypatch):
    # 준비: 초기 두 번 호출하여 baseline 확보
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r1 = await ac.get("/api/news/recent?limit=5&summary_only=1")
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["status"] == "ok"
        first_items = data1.get("items") or data1.get("articles") or []
        latest_ts_initial = data1.get("latest_ts")

        # since_ts 미래 시간 사용 -> 빈 delta 기대
        future = (latest_ts_initial or int(time.time())) + 10_000
        r_future = await ac.get(f"/api/news/recent?since_ts={future}&limit=5&summary_only=1")
        assert r_future.status_code == 200
        data_future = r_future.json()
        assert data_future["delta"] is True
        assert data_future["count"] == 0

        # 기존 latest_ts 로 delta 호출 (아직 새 기사가 없으므로 0)
        r_delta_empty = await ac.get(f"/api/news/recent?since_ts={latest_ts_initial}&limit=5&summary_only=1")
        assert r_delta_empty.status_code == 200
        d_empty = r_delta_empty.json()
        assert d_empty["delta"] is True
        assert d_empty["count"] == 0

        # 강제로 새로운 기사 삽입: 뉴스 서비스 poll 한번 실행 (manual refresh endpoint 이용)
        r_refresh = await ac.post("/api/news/refresh")
        assert r_refresh.status_code == 200

        r_delta2 = await ac.get(f"/api/news/recent?since_ts={latest_ts_initial}&limit=5&summary_only=1")
        assert r_delta2.status_code == 200
        d2 = r_delta2.json()
        # 새 기사가 없을 수도 있으니 조건적 확인 (poll stub 랜덤)
        if d2["count"] > 0:
            assert d2["delta"] is True
            # 새 기사 published_ts 가 기존 latest 이상임을 보장
            for it in d2["items"]:
                assert it["published_ts"] >= latest_ts_initial

@pytest.mark.asyncio
async def test_news_recent_summary_only_and_full():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        r_summary = await ac.get("/api/news/recent?limit=3&summary_only=1")
        assert r_summary.status_code == 200
        d_sum = r_summary.json()
        assert d_sum["status"] == "ok"
        if d_sum["count"] > 0:
            # body 는 summary_only=1 이면 존재하지 않거나 None 여야 함
            assert all(('body' not in it or it['body'] is None) for it in d_sum['items'])
            assert any('summary' in it for it in d_sum['items'])

        r_full = await ac.get("/api/news/recent?limit=3&summary_only=0")
        assert r_full.status_code == 200
        d_full = r_full.json()
        if d_full["count"] > 0:
            # body 필드가 포함되어야 함 (있을 수도, None 일 수도 있으나 키는 등장)
            assert any('body' in it for it in d_full['items'])
