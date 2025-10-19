#!/usr/bin/env python3
"""
Sweep bottom preview for limit=12000, pick params with pos_ratio in [0.10, 0.30] (closest to 0.20),
then run synchronous training with store=true. Promote only if thresholds pass server-side gates.

Env:
  - API_BASE (default http://localhost:8010)
  - API_KEY  (default dev-key)
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests  # type: ignore
except Exception as e:  # noqa: BLE001
    print("ERROR: requests is required. Install with: python3 -m pip install requests", file=sys.stderr)
    raise

BASE = os.getenv("API_BASE", "http://localhost:8010").rstrip("/")
HEADERS = {"X-API-Key": os.getenv("API_KEY", "dev-key"), "Content-Type": "application/json"}


def call_preview(params: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    # Larger limit can take longer; allow generous timeout
    r = requests.get(f"{BASE}/api/training/bottom/preview", headers=HEADERS, params=params, timeout=120)
    try:
        data = r.json()
    except Exception:
        data = {"_text": r.text[:200]}
    return r.status_code, data


def call_train(payload: Dict[str, Any], params: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    r = requests.post(f"{BASE}/api/training/run", headers=HEADERS, data=json.dumps(payload), params=params, timeout=600)
    try:
        data = r.json()
    except Exception:
        data = {"_text": r.text[:400]}
    return r.status_code, data


def main() -> int:
    lookaheads = [30, 40, 60]
    # Include slightly milder drawdown to widen feasible space
    drawdowns = [0.005, 0.0075, 0.01, 0.015]
    rebounds = [0.004, 0.006, 0.008]

    # 1) sweep
    rows: List[Dict[str, Any]] = []
    for L in lookaheads:
        for D in drawdowns:
            for R in rebounds:
                sc, d = call_preview({"limit": 12000, "lookahead": L, "drawdown": D, "rebound": R})
                rows.append({"L": L, "D": D, "R": R, "sc": sc, "d": d})

    usable: List[Dict[str, Any]] = []
    for r in rows:
        if r["sc"] != 200 or not isinstance(r["d"], dict):
            continue
        st = r["d"].get("status")
        if st not in ("ok", "insufficient_labels"):
            continue
        d = r["d"]
        have = int(d.get("have") or 0)
        req = int(d.get("required") or 150)
        pr = d.get("pos_ratio")
        usable.append({**r, "have": have, "req": req, "delta": have - req, "pos_ratio": pr})

    if not usable:
        print("No usable preview results. Ensure backend is up and features cover 12k recent snapshots.")
        return 2

    # prefer within band and meeting required
    candidates = [
        r for r in usable if r["have"] >= r["req"] and (r["pos_ratio"] is None or 0.10 <= r["pos_ratio"] <= 0.30)
    ]
    if candidates:
        candidates.sort(key=lambda r: (r["delta"], abs((r["pos_ratio"] or 0.20) - 0.20)))
    else:
        candidates = sorted(usable, key=lambda r: (abs(r["delta"]), r["delta"] < 0))

    best = candidates[0]

    print("== Selected params ==")
    print(json.dumps(best, ensure_ascii=False, indent=2))

    # 2) train synchronously with store=true and bottom params
    params = {"wait": "true", "store": "true"}
    payload = {
        "mode": "bottom",
        "limit": 12000,
        "bottom_lookahead": best["L"],
        "bottom_drawdown": best["D"],
        "bottom_rebound": best["R"],
    }
    sc, data = call_train(payload, params)
    print("== Training response ==")
    print(json.dumps({"status": sc, "data": data}, ensure_ascii=False, indent=2))

    # Expect the server to annotate promotion eligibility and gate it server-side
    return 0 if sc == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
