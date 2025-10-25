#!/usr/bin/env python3
"""
Lightweight API smoke test for the backend.

- Reads API_BASE (default http://localhost:8000)
- Reads API_KEY (default dev-key)
- Exits non-zero if a critical check fails; prints concise PASS/FAIL lines.

Usage:
  python scripts/verify_api_smoke.py
"""
from __future__ import annotations
import os
import sys
import json
import time
from typing import Any, Dict, Optional

try:
    import requests  # type: ignore
except Exception:
    print("This script requires 'requests'. Install with: pip install requests", file=sys.stderr)
    sys.exit(2)

BASE = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("API_KEY", "dev-key")
H = {"X-API-Key": API_KEY}

FAIL = 0

def pf(ok: bool, label: str, detail: str = ""):
    global FAIL
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}{(' - ' + detail) if detail else ''}")
    if not ok:
        FAIL = 1


def get(path: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None):
    h = dict(H)
    if headers:
        h.update(headers)
    return requests.get(BASE + path, params=params, headers=h, timeout=10)


def post(path: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None):
    h = dict(H)
    if headers:
        h.update(headers)
    return requests.post(BASE + path, params=params, headers=h, timeout=15)


def expect_json(r: requests.Response) -> Optional[Dict[str, Any]]:
    try:
        return r.json()
    except Exception:
        return None


def check_ready():
    r = get("/ready")
    ok = r.status_code in (200, 503)
    pf(ok, "/ready reachable", f"code={r.status_code}")


def check_metrics():
    r = get("/metrics")
    ok = r.status_code == 200 and r.text.startswith("# HELP")
    pf(ok, "/metrics", f"code={r.status_code}")


def check_prod_metrics():
    r = get("/api/models/production/metrics")
    data = expect_json(r)
    ok = r.status_code == 200 and isinstance(data, dict) and "status" in data
    pf(ok, "/api/models/production/metrics", f"code={r.status_code}, status={data and data.get('status')}")
    if ok and data.get("status") == "ok":
        model = data.get("model") or {}
        v = model.get("version")
        pf(bool(v), "prod.version present", str(v))
        pf(isinstance(model.get("metrics"), dict), "prod.metrics is object")


def check_prod_calibration():
    r = get("/api/models/production/calibration")
    data = expect_json(r)
    ok = r.status_code == 200 and isinstance(data, dict) and "status" in data
    pf(ok, "/api/models/production/calibration", f"code={r.status_code}, status={data and data.get('status')}")
    if ok and data.get("status") == "ok":
        calib = data.get("calibration") or {}
        # reliability_bins optional; if present, ensure it's a list
        rb = calib.get("reliability_bins")
        pf(rb is None or isinstance(rb, list), "calibration.reliability_bins[] valid")


def check_inference_predict():
    r = get("/api/inference/predict", params={"debug": 1})
    data = expect_json(r)
    ok = r.status_code == 200 and isinstance(data, dict) and "status" in data
    pf(ok, "/api/inference/predict?debug=1", f"code={r.status_code}, status={data and data.get('status')}")
    if ok and data.get("status") == "ok":
        p = data.get("probability")
        pf(isinstance(p, (int, float)) and 0 <= float(p) <= 1, "probability in [0,1]", str(p))
        pf("model_version" in data, "model_version present", str(data.get("model_version")))
        pf(isinstance(data.get("used_production"), bool), "used_production is boolean")


def check_features_status_and_compute():
    r = get("/admin/features/status")
    data = expect_json(r)
    ok = r.status_code == 200 and isinstance(data, dict) and data.get("status") == "ok"
    pf(ok, "/admin/features/status", f"code={r.status_code}")
    # Try compute-now once; if DB pool isn't ready, skip without failing the smoke
    try:
        dbs = get("/admin/db/status")
        dbj = expect_json(dbs) or {}
        db_ready = bool(dbs.status_code == 200 and isinstance(dbj, dict) and dbj.get("has_pool"))
    except Exception:
        db_ready = False
    if not db_ready:
        # Consider it a soft-skip to avoid hard failing in environments without DB
        pf(True, "/admin/features/compute-now (skipped)", "db_pool_unavailable")
        return
    r2 = post("/admin/features/compute-now")
    d2 = expect_json(r2)
    pf(r2.status_code == 200 and isinstance(d2, dict) and d2.get("status") == "ok", "/admin/features/compute-now", f"code={r2.status_code}")


def check_live_calibration():
    r = get("/api/inference/calibration/live", params={"window_seconds": 3600, "bins": 10})
    data = expect_json(r)
    ok = r.status_code == 200 and isinstance(data, dict) and "status" in data
    pf(ok, "/api/inference/calibration/live", f"code={r.status_code}, status={data and data.get('status')}")
    if ok and data.get("status") == "ok":
        # If ok, verify fields
        pf("ece" in data, "live.ece present")
        pf("brier" in data, "live.brier present")
        rb = data.get("reliability_bins")
        pf(isinstance(rb, list), "live.reliability_bins[] present")


def main():
    print(f"Base={BASE}  API_KEY={'(set)' if API_KEY else '(missing)'}")
    check_ready()
    check_metrics()
    check_prod_metrics()
    check_prod_calibration()
    check_inference_predict()
    check_features_status_and_compute()
    check_live_calibration()
    # Optional: show summary advice on no_data situations
    if FAIL:
        print("\nOne or more checks failed. If predict returned 'no_data', try:\n"
              " - POST /admin/features/compute-now\n"
              " - Ensure OHLCV is ingesting and feature scheduler is running\n"
              " - For live calibration, run /api/inference/labeler/run?force=true&min_age_seconds=60", file=sys.stderr)
    sys.exit(FAIL)

if __name__ == "__main__":
    main()
