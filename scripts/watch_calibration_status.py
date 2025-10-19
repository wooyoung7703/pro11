#!/usr/bin/env python3
import os
import sys
import time
import json
from typing import Any, Dict

import requests


API_BASE = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("API_KEY", "dev-key")
INTERVAL = int(os.getenv("WATCH_INTERVAL", "60"))


def fetch_status() -> Dict[str, Any]:
    r = requests.get(f"{API_BASE}/api/monitor/calibration/status", headers={"X-API-Key": API_KEY}, timeout=20)
    try:
        return r.json()
    except Exception:
        return {"status": r.status_code, "_text": r.text[:300]}


def fmt(v: Any) -> str:
    try:
        if isinstance(v, float):
            return f"{v:.4f}"
        return str(v)
    except Exception:
        return str(v)


def print_snapshot(data: Dict[str, Any]) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    last = data.get("last_snapshot") or {}
    enabled = data.get("enabled")
    labeler = data.get("labeler_enabled")
    live_ece = last.get("live_ece")
    prod_ece = last.get("prod_ece")
    delta = last.get("delta")
    abs_drift = last.get("abs_drift")
    abs_streak = data.get("abs_streak")
    sample_count = last.get("sample_count")
    min_samples = data.get("min_samples")
    recommend = data.get("recommend_retrain")
    reasons = ",".join(data.get("recommend_retrain_reasons") or [])
    print(
        f"[{ts}] enabled={enabled} labeler={labeler} live_ece={fmt(live_ece)} prod_ece={fmt(prod_ece)} "
        f"delta={fmt(delta)} abs_drift={abs_drift} streak={abs_streak} samples={sample_count}/{min_samples} "
        f"recommend={recommend} reasons={reasons}"
    )


def main() -> int:
    once = "--once" in sys.argv
    try:
        while True:
            data = fetch_status()
            print_snapshot(data)
            if once:
                return 0
            time.sleep(max(5, INTERVAL))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
