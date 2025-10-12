import os
import json
import asyncio
import pytest
import websockets
import httpx

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")
WS_URL = os.getenv("TEST_WS_URL", BASE_URL.replace("http://", "ws://") + "/ws/ohlcv")
SYMBOL = os.getenv("TEST_SYMBOL", "XRPUSDT")
INTERVAL = os.getenv("TEST_INTERVAL", "1m")

pytestmark = pytest.mark.asyncio

async def _fetch_recent(limit: int = 30):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BASE_URL}/api/ohlcv/recent", params={"symbol": SYMBOL, "interval": INTERVAL, "limit": limit})
        r.raise_for_status()
        return r.json()

def _assert_asc(candles):
    if len(candles) <= 1:
        return
    ots = [c["open_time"] for c in candles]
    assert all(ots[i] <= ots[i+1] for i in range(len(ots)-1)), "open_time not ASC"

def _assert_closed_only(candles):
    for c in candles:
        assert c.get("is_closed") is True, "Non-closed candle found in snapshot/append"

def _assert_symbol_interval(payload):
    assert payload["symbol"].upper() == SYMBOL
    assert payload["interval"] == INTERVAL

async def test_ws_snapshot_basic():
    recent = await _fetch_recent(limit=40)
    recent_candles = recent.get("candles", [])
    async with websockets.connect(WS_URL, ping_timeout=10) as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        payload = json.loads(raw)
        assert payload["type"] == "snapshot"
        _assert_symbol_interval(payload)
        candles = payload.get("candles", [])
        _assert_asc(candles)
        _assert_closed_only(candles)
        meta = payload.get("meta", {})
        if candles:
            assert meta.get("earliest_open_time") == candles[0]["open_time"]
            assert meta.get("latest_open_time") == candles[-1]["open_time"]
            assert meta.get("count") == len(candles)
        # 신규 메타 필드 검증
        if "completeness_percent" in meta and meta["completeness_percent"] is not None:
            assert 0 <= meta["completeness_percent"] <= 1
        assert meta.get("largest_gap_bars", 0) >= 0
        assert meta.get("largest_gap_span_ms", 0) >= 0
        # 최근 REST 결과와 끝 부분(교집합) 비교 (약간의 시간차 허용)
        if candles and recent_candles:
            tail_ws = [c["open_time"] for c in candles[-5:]]
            tail_rest = [c["open_time"] for c in recent_candles[-10:]]  # 조금 더 넉넉한 윈도우
            overlap = any(ot in tail_rest for ot in tail_ws)
            assert overlap, "Snapshot tail not overlapping recent REST tail"

async def test_ws_append_sequence_optional():
    # append 이벤트 관찰: ingestion 주기가 짧지 않으면 skip 조건 허용
    async with websockets.connect(WS_URL, ping_timeout=10) as ws:
        first_raw = await asyncio.wait_for(ws.recv(), timeout=5)
        first = json.loads(first_raw)
        assert first["type"] == "snapshot"
        candles = first.get("candles", [])
        last_ot = candles[-1]["open_time"] if candles else None
        append_payload = None
        try:
            for _ in range(5):  # 최대 5회 (약간 대기)
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=15)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                if msg.get("type") == "append" and msg.get("candles"):
                    _assert_symbol_interval(msg)
                    _assert_asc(msg["candles"])
                    _assert_closed_only(msg["candles"])
                    if last_ot is not None:
                        assert msg["candles"][0]["open_time"] > last_ot, "Append not strictly after snapshot tail"
                    append_payload = msg
                    break
            # append 없을 수도 있음 (ingestion 비활성/저유량) → 단순 통과
        finally:
            pass
        if append_payload:
            assert append_payload["count"] == len(append_payload["candles"])  # 일관성 검증
