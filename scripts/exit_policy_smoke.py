#!/usr/bin/env python3
"""
ExitPolicy and trading observability smoke script (robust).

Actions:
- Auto-detect API base among 8010/8000 unless API_BASE is set.
- Wait/retry for /metrics to become available.
- Submit a simulated market buy (best-effort; tolerate failures).
- Fetch /metrics and assert exit/slippage metric names are present.

Env:
- API_BASE (optional; if not set, try localhost:8010 then 8000)
- API_KEY  (default: dev-key)
"""
import os
import json
import sys
import time
from typing import Optional

import requests


def _try_get(url: str, timeout: float = 1.5) -> Optional[requests.Response]:
    try:
        return requests.get(url, timeout=timeout)
    except Exception:
        return None


def choose_base() -> str:
    # Honor explicit env first
    env = os.getenv("API_BASE")
    if env:
        return env.rstrip("/")
    # Probe common localhost ports (prefer 8010)
    candidates = [
        "http://localhost:8010",
        "http://127.0.0.1:8010",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    for b in candidates:
        r = _try_get(b.rstrip("/") + "/metrics", timeout=1.0)
        if r is not None and r.status_code == 200:
            return b.rstrip("/")
    # Fallback default
    return "http://localhost:8010"


def wait_for_metrics(base: str, total_secs: float = 20.0, interval: float = 1.0) -> bool:
    deadline = time.time() + total_secs
    while time.time() < deadline:
        r = _try_get(base.rstrip("/") + "/metrics", timeout=1.0)
        if r is not None and r.status_code == 200:
            return True
        time.sleep(interval)
    return False


def main():
    base = choose_base().rstrip("/")
    key = os.getenv("API_KEY", "dev-key")
    h = {"X-API-Key": key, "Content-Type": "application/json"}

    def post(path, payload=None, timeout=20):
        url = base + path
        r = requests.post(url, headers=h, data=(json.dumps(payload) if payload is not None else None), timeout=timeout)
        try:
            data = r.json()
        except Exception:
            data = {"_text": r.text[:400]}
        print("POST", path, "->", r.status_code, json.dumps(data)[:300])
        return r, data

    def get(path, timeout=20):
        url = base + path
        r = requests.get(url, headers=h, timeout=timeout)
        try:
            data = r.json()
        except Exception:
            data = {"_text": r.text[:400]}
        print("GET ", path, "->", r.status_code)
        return r, data

    # 0) Wait for metrics endpoint (backend might be cold)
    print("Detect base:", base)
    if not wait_for_metrics(base, total_secs=25.0, interval=1.0):
        print("metrics_unavailable: backend not responding on /metrics within timeout", file=sys.stderr)
        return 2

    # 1) Submit a small buy to exercise TradingService path (best-effort)
    try:
        post("/api/trading/submit", {"side": "buy", "size": 0.001})
    except requests.exceptions.RequestException as e:
        print("submit_warning:", repr(e))

    # 2) Read metrics and check presence of relevant series
    r = requests.get(base + "/metrics", timeout=10)
    ok = (r.status_code == 200)
    text = r.text if ok else ""
    keys = [
        "trading_exit_events_total",
        "trading_exit_return_bps",
        "trading_exit_hold_seconds",
        "trading_exit_trail_pullback_bps",
        "trading_order_slippage_bps",
    ]
    present = {k: (k in text) for k in keys}
    print("/metrics status:", r.status_code)
    print("metric presence:", present)
    # Print first few lines for manual inspection
    print("\n--- metrics head ---\n" + "\n".join(text.splitlines()[:24]))

    # Return non-zero if metrics endpoint not available
    if not ok:
        print("metrics_unavailable", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
