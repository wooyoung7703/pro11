#!/usr/bin/env python3
"""
Simple ML trading signal smoke test.

Reads API_BASE (default http://localhost:8000) and API_KEY (default dev-key).
Performs:
 - GET  /admin/features/status
 - POST /api/trading/ml/preview
 - POST /api/trading/ml/trigger (auto_execute=false)
 - POST /api/trading/ml/trigger (auto_execute=true, size=0.1)

Exits non-zero if HTTP errors occur.
"""
from __future__ import annotations
import os
import sys
import json

try:
    import requests  # type: ignore
except Exception:
    print("This script requires 'requests'. Install with: pip install requests", file=sys.stderr)
    sys.exit(2)

BASE = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("API_KEY", "dev-key")
H = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

def call(method: str, path: str, **kwargs):
    url = BASE + path
    r = requests.request(method, url, headers=H, timeout=20, **kwargs)
    try:
        data = r.json()
    except Exception:
        data = {"_text": r.text[:200]}
    print(method, path, "->", r.status_code, json.dumps(data)[:500])
    return r, data

def main() -> int:
    print(f"Base={BASE}  API_KEY={'(set)' if API_KEY else '(missing)'}")
    rc = 0
    try:
        r, _ = call("GET", "/admin/features/status")
        if r.status_code != 200:
            rc = 1
        r, _ = call("POST", "/api/trading/ml/preview", json={"debug": False})
        if r.status_code != 200:
            rc = 1
        r, _ = call("POST", "/api/trading/ml/trigger", json={"auto_execute": False, "reason": "ml_smoke"})
        if r.status_code != 200:
            rc = 1
        r, _ = call("POST", "/api/trading/ml/trigger", json={"auto_execute": True, "size": 0.1, "reason": "ml_smoke"})
        if r.status_code != 200:
            rc = 1
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {e}", file=sys.stderr)
        rc = 1
    return rc

if __name__ == "__main__":
    sys.exit(main())
