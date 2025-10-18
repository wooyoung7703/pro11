#!/usr/bin/env python3
"""
Quick sweep over /api/training/bottom/preview to recommend conservative bottom-label parameters.

Environment:
  - API_BASE (default: http://localhost:8000)
  - API_KEY  (default: dev-key)

Outputs a short summary and a recommended parameter set based on:
  - having at least the required number of labeled samples
  - keeping pos_ratio modest for stability (prefer ~0.15-0.30)
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests  # type: ignore
except Exception as e:  # noqa: BLE001
    print("ERROR: requests module not available. Install with: python3 -m pip install requests", file=sys.stderr)
    raise


BASE = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
HEADERS = {"X-API-Key": os.getenv("API_KEY", "dev-key")}


def call_preview(params: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    r = requests.get(f"{BASE}/api/training/bottom/preview", headers=HEADERS, params=params, timeout=30)
    try:
        data = r.json()
    except Exception:
        data = {"_text": r.text[:200]}
    return r.status_code, data


def main() -> int:
    lookaheads = [30, 40, 60]
    drawdowns = [0.0075, 0.01, 0.015]
    rebounds = [0.004, 0.006, 0.008]

    rows: List[Dict[str, Any]] = []
    for L in lookaheads:
        for D in drawdowns:
            for R in rebounds:
                sc, d = call_preview({"limit": 3000, "lookahead": L, "drawdown": D, "rebound": R})
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
        print("No usable preview results. Check API is running and API_KEY is valid.")
        return 2

    # Prefer params that meet required label count with modest pos_ratio
    candidates = [
        r for r in usable if r["have"] >= r["req"] and (r["pos_ratio"] is None or 0.10 <= r["pos_ratio"] <= 0.35)
    ]
    if candidates:
        candidates.sort(key=lambda r: (r["delta"], abs((r["pos_ratio"] or 0.22) - 0.22)))
    else:
        # Fall back to smallest absolute delta (closest to required), then prefer non-negative
        candidates = sorted(usable, key=lambda r: (abs(r["delta"]), r["delta"] < 0))

    best = candidates[0]

    # Print a concise table
    def fr(x: Optional[float]) -> str:
        if x is None:
            return "-"
        return f"{x:.4f}"

    print("== Bottom preview sweep (top by closeness to required) ==")
    for r in sorted(usable, key=lambda r: (r["req"] - r["have"] if r["have"] < r["req"] else r["have"] - r["req"]))[:12]:
        print(
            f"L={r['L']:>2}, D={r['D']:.4f}, R={r['R']:.4f} -> have={r['have']}, req={r['req']}, pos_ratio={fr(r['pos_ratio'])}"
        )

    print("\n== Recommended params ==")
    rec = {
        "lookahead": best["L"],
        "drawdown": best["D"],
        "rebound": best["R"],
        "have": best["have"],
        "required": best["req"],
        "pos_ratio": best["pos_ratio"],
    }
    print(json.dumps(rec, ensure_ascii=False, indent=2))

    # Export as machine-readable line for callers
    print("RECOMMENDED_JSON=", json.dumps(rec, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
