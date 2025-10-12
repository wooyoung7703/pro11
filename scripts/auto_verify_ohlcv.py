#!/usr/bin/env python
"""통합 자동 검증 스크립트 (REST + WS + 메트릭)
Usage: python scripts/auto_verify_ohlcv.py
"""
import os, json, asyncio, time, math
import httpx, websockets

BASE = os.getenv("BASE_URL", "http://localhost:8000")
WS = BASE.replace("http://", "ws://") + "/ws/ohlcv"
SYMBOL = os.getenv("SYMBOL", "XRPUSDT")
INTERVAL = os.getenv("INTERVAL", "1m")
TIMEOUT = 8
APPEND_WAIT = int(os.getenv("APPEND_WAIT", "45"))  # seconds
REPORT = {"steps": []}

def step(name, ok, detail=None, data=None):
    REPORT["steps"].append({"name": name, "ok": ok, "detail": detail, "data": data})
    print(f"[{name}] {'OK' if ok else 'FAIL'} - {detail or ''}")

async def fetch_json(path, params=None):
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(BASE + path, params=params)
        r.raise_for_status()
        return r.json()

async def verify_recent():
    js = await fetch_json("/api/ohlcv/recent", params={"symbol": SYMBOL, "interval": INTERVAL, "limit": 10, "include_open": True})
    candles = js.get("candles", [])
    asc = all(candles[i]["open_time"] < candles[i+1]["open_time"] for i in range(len(candles)-1))
    all_closed = all(c.get("is_closed") for c in candles)
    step("recent", asc and all_closed, detail=f"asc={asc} all_closed={all_closed} count={len(candles)}", data={"tail": [c.get("open_time") for c in candles[-5:]]})
    return candles

async def verify_history(after_reference):
    js = await fetch_json("/api/ohlcv/history", params={"after_open_time": after_reference, "limit": 5})
    candles = js.get("candles", [])
    asc = all(candles[i]["open_time"] < candles[i+1]["open_time"] for i in range(len(candles)-1))
    monotonic = (not candles) or (candles[0]["open_time"] > after_reference)
    step("history_after", asc and monotonic, detail=f"asc={asc} monotonic_after={monotonic} count={len(candles)}", data={"first": candles[0]["open_time"] if candles else None})

async def verify_meta():
    js = await fetch_json("/api/ohlcv/meta")
    latest = js.get("latest_open_time")
    count = js.get("count")
    cpl = js.get("completeness_percent")
    gap = js.get("largest_gap_bars")
    ok = latest is not None and count >= 0 and (cpl is None or 0 <= cpl <= 1) and gap >= 0
    step("meta", ok, detail=f"latest={latest} count={count} completeness={cpl} gap_bars={gap}")
    return latest

async def verify_ws_snapshot_and_append(ref_last_open):
    append_received = None
    async with websockets.connect(WS) as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        snap = json.loads(raw)
        candles = snap.get("candles", [])
        asc = all(candles[i]["open_time"] < candles[i+1]["open_time"] for i in range(len(candles)-1))
        closed_only = all(c.get("is_closed") for c in candles)
        step("ws_snapshot", asc and closed_only, detail=f"asc={asc} closed_only={closed_only} count={len(candles)}")
        snap_last = candles[-1]["open_time"] if candles else None
        start = time.time()
        while time.time() - start < APPEND_WAIT:
            try:
                msg_raw = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue
            msg = json.loads(msg_raw)
            if msg.get("type") == "append" and msg.get("candles"):
                c = msg["candles"]
                asc2 = all(c[i]["open_time"] < c[i+1]["open_time"] for i in range(len(c)-1))
                strictly_after = snap_last is None or c[0]["open_time"] > snap_last
                append_received = {
                    "count": len(c),
                    "first_open_time": c[0]["open_time"],
                    "last_open_time": c[-1]["open_time"],
                    "asc": asc2,
                    "strictly_after_snapshot": strictly_after,
                }
                step("ws_append", asc2 and strictly_after, detail=f"count={len(c)} after_snapshot={strictly_after}")
                break
    if append_received is None:
        step("ws_append", False, detail="no_append_within_wait", data={"wait_seconds": APPEND_WAIT})

async def main():
    recent = await verify_recent()
    ref_after = recent[-1]["open_time"] if recent else 0
    await verify_history(ref_after)
    latest = await verify_meta()
    await verify_ws_snapshot_and_append(ref_after)
    print("=== JSON REPORT ===")
    print(json.dumps(REPORT, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
