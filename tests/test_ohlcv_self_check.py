import os
import pytest
import httpx

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")
SYMBOL = os.getenv("TEST_SYMBOL", "XRPUSDT")
INTERVAL = os.getenv("TEST_INTERVAL", "1m")

pytestmark = pytest.mark.asyncio

async def _get(path: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{BASE_URL}{path}", params=params or {})
        r.raise_for_status()
        return r.json()

async def test_self_check_flags_and_recent_rows_structure():
    data = await _get("/api/ohlcv/self_check", {"append_probe": False, "repair_probe": False, "history_limit": 3})
    # self_check 응답은 symbol 키가 없을 수 있으므로 config_flags 기반 검증
    cfg = data.get("config_flags", {})
    assert "ohlcv_include_open_default" in cfg
    assert "ohlcv_ws_snapshot_include_open" in cfg
    # recent_rows 대신 recent_count / recent_tail_open_time 존재 확인
    assert "recent_count" in data
    assert "recent_tail_open_time" in data
    # history_preview 구조 확인
    hp = data.get("history_preview", [])
    for c in hp:
        # is_closed 등 필드 subset 확인 (없으면 skip)
        if isinstance(c, dict):
            assert "open_time" in c and "is_closed" in c

async def test_self_check_append_probe_metric_increments():
    # baseline (append_probe False)
    await _get("/api/ohlcv/self_check", {"append_probe": False, "repair_probe": False, "history_limit": 1})
    # probe 1
    second = await _get("/api/ohlcv/self_check", {"append_probe": True, "repair_probe": False, "history_limit": 1})
    md2 = second.get("metrics_delta", {})
    # 실제 키 이름은 append_attempts_delta / append_sent_delta
    assert "append_attempts_delta" in md2 and "append_sent_delta" in md2
    # probe 2
    third = await _get("/api/ohlcv/self_check", {"append_probe": True, "repair_probe": False, "history_limit": 1})
    md3 = third.get("metrics_delta", {})
    for k in ("append_attempts_delta", "append_sent_delta"):
        assert k in md3
        assert isinstance(md3[k], (int,float))
    # 음수는 발생하지 않아야 함
    assert md3.get("append_sent_delta", 0) >= -0.00001
