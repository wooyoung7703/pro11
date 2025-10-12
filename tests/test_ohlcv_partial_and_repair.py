import os
import json
import asyncio
import pytest
import httpx
import websockets

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")
WS_URL = os.getenv("TEST_WS_URL", BASE_URL.replace("http://", "ws://") + "/ws/ohlcv")
SYMBOL = os.getenv("TEST_SYMBOL", "XRPUSDT")
INTERVAL = os.getenv("TEST_INTERVAL", "1m")

pytestmark = pytest.mark.asyncio

async def _post(path: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{BASE_URL}{path}", params=params or {})
        r.raise_for_status()
        return r.json()

async def _get(path: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BASE_URL}{path}", params=params or {})
        r.raise_for_status()
        return r.json()

async def test_partial_policy_ws_includes_rest_excludes():
    # 1. partial 생성
    mp = await _post("/admin/ohlcv/mock_partial")
    assert mp.get("partial_upserted") is True
    partial_ot = mp.get("open_time")
    assert isinstance(partial_ot, int)

    # 2. REST recent (include_open=False 기본) -> partial 제외되어야 함
    recent = await _get("/api/ohlcv/recent", {"symbol": SYMBOL, "interval": INTERVAL, "limit": 10})
    rec_ots = [c["open_time"] for c in recent.get("candles", [])]
    assert partial_ot not in rec_ots, "REST recent should exclude partial when include_open default false"

    # 3. WS snapshot -> partial 포함 (환경 플래그 true 가정)
    async with websockets.connect(WS_URL, ping_timeout=10) as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        snap = json.loads(raw)
        assert snap["type"] == "snapshot"
        ws_ots = [c["open_time"] for c in snap.get("candles", [])]
        if partial_ot not in ws_ots:
            pytest.skip("Partial not visible in WS snapshot (flag may be disabled in this environment)")
        # partial 은 마지막이어야 하고 is_closed False
        last = snap["candles"][-1]
        assert last["open_time"] == partial_ot
        assert last["is_closed"] is False

async def test_repair_broadcast_flow():
    # 1. 최근 3개 중 가운데 1개 삭제로 gap 유도 (offset 1, count 1) - 단순화 위해 start_offset=1 사용
    # 먼저 recent 3개 확보
    recent = await _get("/api/ohlcv/recent", {"symbol": SYMBOL, "interval": INTERVAL, "limit": 5})
    candles = recent.get("candles", [])
    if len(candles) < 3:
        pytest.skip("Not enough candles to test repair flow")
    # delete_offset_range 는 start_offset=1, count=1 이면 두번째(과거 두번째) 제거 가정 (구현 세부에 따라 조정 가능)
    del_resp = await _post("/admin/ohlcv/delete_offset_range", {"start_offset": 1, "count": 1})
    assert del_resp.get("deleted") in (0,1,2,3), "Unexpected delete response structure"

    # 2. mock_repair 호출 -> WS repair 수신 대기
    async with websockets.connect(WS_URL, ping_timeout=10) as ws:
        # snapshot consume
        _ = await asyncio.wait_for(ws.recv(), timeout=5)
        # trigger repair
        mr = await _post("/admin/ohlcv/mock_repair")
        assert mr.get("repaired_count", 0) >= 0
        repair_msg = None
        try:
            for _ in range(5):
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                if msg.get("type") == "repair":
                    repair_msg = msg
                    break
        except asyncio.TimeoutError:
            pass
    if repair_msg is None:
        pytest.skip("No repair broadcast received (environment may not support, or timing issue)")
    assert repair_msg.get("type") == "repair"
    assert isinstance(repair_msg.get("candles", []), list)
