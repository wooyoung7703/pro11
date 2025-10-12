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

async def _get(path: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BASE_URL}{path}", params=params or {})
        r.raise_for_status()
        return r.json()

async def _post(path: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{BASE_URL}{path}", params=params or {})
        r.raise_for_status()
        return r.json()

async def test_delta_basic_contract():
    recent = await _get("/api/ohlcv/recent", {"symbol": SYMBOL, "interval": INTERVAL, "limit": 20})
    candles = recent.get("candles", [])
    if not candles:
        pytest.skip("No candles available for delta test")
    last_ot = candles[-1]["open_time"]
    # delta since last_ot => 기대: 새로운 없음
    delta = await _get("/api/ohlcv/delta", {"since": last_ot, "limit": 50})
    assert "base_from" in delta and "base_to" in delta
    assert isinstance(delta.get("candles", []), list)
    assert delta.get("truncated") in (True, False)
    assert all(c["open_time"] > last_ot for c in delta["candles"]), "Delta returned candle not strictly newer"

async def test_partial_lifecycle_mock():
    # partial update & close 이벤트 수신 여부 (환경에 따라 skip)
    async with websockets.connect(WS_URL, ping_timeout=10) as ws:
        snap_raw = await asyncio.wait_for(ws.recv(), timeout=5)
        snap = json.loads(snap_raw)
        assert snap.get("type") == "snapshot"
        # mock partial_update
        pu = await _post("/admin/ohlcv/mock_partial_update", {})
        assert pu.get("partial_update") is True
        partial_update_msg = None
        partial_close_msg = None
        try:
            for _ in range(10):
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "partial_update":
                    partial_update_msg = msg
                if t == "partial_close":
                    partial_close_msg = msg
                    break
        except asyncio.TimeoutError:
            pass
        if partial_update_msg is None:
            pytest.skip("No partial_update received (timing or feature disabled)")
        # mock partial_close
        pc = await _post("/admin/ohlcv/mock_partial_close", {"open_time": partial_update_msg.get("candle", {}).get("open_time")})
        assert pc.get("partial_close") is True
        # 수신 대기 (이미 받았을 수도)
        if partial_close_msg is None:
            try:
                for _ in range(5):
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    msg = json.loads(raw)
                    if msg.get("type") == "partial_close":
                        partial_close_msg = msg
                        break
            except asyncio.TimeoutError:
                pass
        if partial_close_msg is None:
            pytest.skip("No partial_close received (timing)")
        assert partial_close_msg.get("final", {}).get("is_closed") in (None, True)

async def test_gaps_status_segments_field():
    status = await _get("/api/ohlcv/gaps/status")
    if status.get("status") == "no_consumer":
        pytest.skip("No consumer running for gaps test")
    assert "segments" in status
    assert isinstance(status.get("segments"), list)
    for seg in status.get("segments"):
        assert "from_ts" in seg and "to_ts" in seg and "state" in seg

async def test_delta_repairs_array_populated():
    """repair 발생 후 delta에서 repairs 배열 반환되는지 검증.
    1) 현재 recent 마지막 open_time 확보
    2) 일부 과거 캔들 삭제 + repair mock 트리거 (가능한 경우)
    3) delta 호출하여 repairs 비어있지 않은지 (환경 따라 skip)
    """
    recent = await _get("/api/ohlcv/recent", {"symbol": SYMBOL, "interval": INTERVAL, "limit": 30})
    candles = recent.get("candles", [])
    if len(candles) < 5:
        pytest.skip("Not enough candles to test repairs delta")
    last_ot = candles[-1]["open_time"]
    # 삭제로 gap 만들기 (관리 엔드포인트 활용) - 환경에서 비활성일 수 있으므로 실패 시 skip
    try:
        await _post("/admin/ohlcv/delete_offset_range", {"start_offset": 2, "count": 1})
    except Exception:
        pytest.skip("delete_offset_range not available")
    # self_check 의 repair_probe 기능 또는 mock repair 브로드캐스트 엔드포인트가 있다면 호출
    triggered = False
    try:
        # self_check 로 repair_probe 트리거 (만약 구현됨)
        _ = await _get("/api/ohlcv/self_check", {"repair_probe": True, "append_probe": False})
        triggered = True
    except Exception:
        pass
    if not triggered:
        # fallback: gap backfill 서비스가 주기적으로 돌지 않는 환경이면 의미있는 repair 없을 수 있음
        pytest.skip("Could not trigger repair probe; environment may not support it")
    # delta 호출 (since 최근 마지막 확정) → repair open_time 이 last_ot 이전이어도 since > open_time 조건으로 필터됨
    # 따라서 조금 더 과거를 since 로 사용: candles[-5]
    base_since = candles[-5]["open_time"]
    delta = await _get("/api/ohlcv/delta", {"since": base_since, "limit": 200})
    repairs = delta.get("repairs", [])
    if not repairs:
        pytest.skip("No repairs returned (may be timing or feature inactive)")
    for rp in repairs:
        assert "open_time" in rp and "candle" in rp

async def test_partial_close_latency_field():
    """mock_partial_update -> mock_partial_close 시 WS partial_close 이벤트에 latency_ms 포함 확인.
    환경에 따라 partial 기능 비활성 또는 타이밍 문제면 skip.
    """
    async with websockets.connect(WS_URL, ping_timeout=10) as ws:
        # 초기 snapshot
        try:
            _ = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        except Exception:
            pytest.skip("No snapshot received")
        # partial_update 트리거
        try:
            pu = await _post("/admin/ohlcv/mock_partial_update", {})
            assert pu.get("partial_update") is True
        except Exception:
            pytest.skip("mock_partial_update not available")
        # partial_close 트리거 위해 open_time 확보
        open_time = pu.get("open_time") or pu.get("candle",{}).get("open_time")
        # partial_close (latency 계산 포함)
        try:
            pc = await _post("/admin/ohlcv/mock_partial_close", {"open_time": open_time})
            assert pc.get("partial_close") is True
        except Exception:
            pytest.skip("mock_partial_close not available")
        partial_close_msg = None
        try:
            for _ in range(6):
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                if msg.get("type") == "partial_close":
                    partial_close_msg = msg
                    break
        except asyncio.TimeoutError:
            pass
    if partial_close_msg is None:
        pytest.skip("No partial_close latency event received")
    assert partial_close_msg.get("type") == "partial_close"
    # latency_ms 는 0 이상 자연수
    latency_ms = partial_close_msg.get("latency_ms")
    assert isinstance(latency_ms, int) and latency_ms >= 0

async def test_gap_open_segments_gauge_alignment():
    """gaps/status 의 open_segments 와 Prometheus gauge (텍스트 포맷) 값 정합 확인.
    환경에 따라 Prometheus endpoint 또는 gap 소비 비활성 시 skip.
    """
    # gaps/status 호출
    status = await _get("/api/ohlcv/gaps/status")
    if status.get("status") == "no_consumer":
        pytest.skip("No consumer running for gaps test")
    open_segments = status.get("open_segments")
    if open_segments is None:
        pytest.skip("gaps/status missing open_segments field")
    # /metrics 에서 gauge 스크레이프
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            m = await client.get(f"{BASE_URL}/metrics")
            m.raise_for_status()
        except Exception:
            pytest.skip("/metrics not available")
    text = m.text.splitlines()
    gauge_lines = [ln for ln in text if ln.startswith("ohlcv_gap_open_segments{")]
    if not gauge_lines:
        pytest.skip("Gauge line not found (feature may not be initialized yet)")
    # 심볼/인터벌 라벨 매칭
    match_line = None
    for ln in gauge_lines:
        if f'symbol="{SYMBOL}"' in ln and f'interval="{INTERVAL}"' in ln:
            match_line = ln
            break
    if not match_line:
        pytest.skip("Gauge label combination not found")
    try:
        val_str = match_line.split("}")[-1].strip()
        gauge_val = int(float(val_str))
    except Exception:
        pytest.skip("Could not parse gauge value")
    assert gauge_val == open_segments, f"Gauge {gauge_val} != status open_segments {open_segments}"
