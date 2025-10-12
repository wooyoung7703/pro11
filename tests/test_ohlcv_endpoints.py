import os
import pytest
import httpx

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")
SYMBOL = os.getenv("TEST_SYMBOL", "XRPUSDT")
INTERVAL = os.getenv("TEST_INTERVAL", "1m")

@pytest.mark.asyncio
async def test_recent_basic_order_and_fields():
    params = {"symbol": SYMBOL, "interval": INTERVAL, "limit": 5}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BASE_URL}/api/ohlcv/recent", params=params)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["symbol"].upper() == SYMBOL
        assert data["interval"] == INTERVAL
        candles = data.get("candles", [])
        # ASC 정렬 검증
        if len(candles) > 1:
            open_times = [c["open_time"] for c in candles]
            assert all(open_times[i] <= open_times[i+1] for i in range(len(open_times)-1)), "open_time not ASC"
        # partial 포함 안 되었는지 (include_open 기본 False 가정)
        for c in candles:
            assert c.get("is_closed") is True

@pytest.mark.asyncio
async def test_recent_include_open_optional():
    # include_open=true 호출 → partial 존재 시 마지막 1개 허용
    params = {"symbol": SYMBOL, "interval": INTERVAL, "limit": 10, "include_open": True}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BASE_URL}/api/ohlcv/recent", params=params)
        assert r.status_code == 200, r.text
        data = r.json()
        candles = data.get("candles", [])
        if candles:
            # 마지막 하나만 partial 가능
            partials = [c for c in candles if not c.get("is_closed")]
            assert len(partials) in (0,1)
            if len(partials) == 1:
                assert candles[-1] is partials[0]

@pytest.mark.asyncio
async def test_meta_endpoint_basic():
    params = {"symbol": SYMBOL, "interval": INTERVAL, "sample_for_gap": 500}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BASE_URL}/api/ohlcv/meta", params=params)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["symbol"].upper() == SYMBOL
        assert data["interval"] == INTERVAL
        # count >= 0
        assert data["count"] >= 0
        # completeness 는 None 또는 0~1
        comp = data.get("completeness_percent")
        if comp is not None:
            assert 0 <= comp <= 1
        # largest_gap_bars 자연수 조건
        assert data.get("largest_gap_bars", 0) >= 0

@pytest.mark.asyncio
async def test_history_cursor_paging():
    async with httpx.AsyncClient(timeout=10) as client:
        # 첫 페이지 (after/before 없이)
        r1 = await client.get(f"{BASE_URL}/api/ohlcv/history", params={"symbol": SYMBOL, "interval": INTERVAL, "limit": 50})
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        candles1 = d1.get("candles", [])
        if len(candles1) >= 2:
            ots = [c["open_time"] for c in candles1]
            assert all(ots[i] <= ots[i+1] for i in range(len(ots)-1))
        next_before = d1.get("next_before_open_time")
        if next_before:
            r2 = await client.get(f"{BASE_URL}/api/ohlcv/history", params={"symbol": SYMBOL, "interval": INTERVAL, "limit": 50, "before_open_time": next_before})
            assert r2.status_code == 200, r2.text
            d2 = r2.json()
            c2 = d2.get("candles", [])
            if c2:
                # 모든 open_time 은 next_before 보다 작아야 함
                assert all(c["open_time"] < next_before for c in c2)

@pytest.mark.asyncio
async def test_recent_limit_enforced():
    params = {"symbol": SYMBOL, "interval": INTERVAL, "limit": 2000}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BASE_URL}/api/ohlcv/recent", params=params)
        assert r.status_code == 200
        data = r.json()
        candles = data.get("candles", [])
        # 서버 측 제한 (예: max 1000) 가정
        assert len(candles) <= 1000
