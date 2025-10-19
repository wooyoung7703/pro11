#!/usr/bin/env python3
"""
Auto-bootstrap for local dev: on container startup, orchestrate data backfill,
feature backfill, quick param sweep, and a training run to register a model.

Design goals:
- Safe: only runs when AUTO_BOOTSTRAP_LOCAL=1
- Self-contained: uses Python stdlib (urllib) only
- Resilient: best-effort with timeouts; logs progress to stdout

Environment:
  API_BASE (default: http://localhost:8000)
  API_KEY  (default: dev-key)
  BOOTSTRAP_BACKFILL_BARS (optional, default: 5000)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple


BASE = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("API_KEY", "dev-key")
BACKFILL_BARS_TARGET = int(os.getenv("BOOTSTRAP_BACKFILL_BARS", "5000"))


def _req(method: str, path: str, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None, timeout: int = 30) -> Tuple[int, Any]:
    url = BASE + path
    if params:
        q = urllib.parse.urlencode(params)
        url += ("?" + q)
    data = None
    headers = {"X-API-Key": API_KEY}
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method=method, headers=headers)  # type: ignore[arg-type]
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec: B310 - controlled URL
            raw = resp.read()
            try:
                return resp.getcode() or 0, json.loads(raw.decode("utf-8"))
            except Exception:
                return resp.getcode() or 0, {"_text": raw.decode("utf-8", "ignore")[:500]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def wait_ready(max_seconds: int = 300) -> bool:
    start = time.time()
    while time.time() - start < max_seconds:
        sc, d = _req("GET", "/admin/features/status")
        if sc == 200 and isinstance(d, dict) and d.get("status") == "ok":
            print("[auto] backend ready")
            return True
        time.sleep(1)
    print("[auto] backend not ready within timeout")
    return False


def start_year_backfill():
    sc, d = _req("POST", "/api/ohlcv/backfill/year/start", params={"overwrite": "true", "purge": "false"})
    print("[auto] year/backfill/start ->", sc, str(d)[:200])


def poll_backfill_until(threshold_bars: int = BACKFILL_BARS_TARGET, timeout_sec: int = 2400):
    print("[auto] polling backfill status...")
    start = time.time()
    while True:
        sc, d = _req("GET", "/api/ohlcv/backfill/year/status")
        st = d.get("status") if isinstance(d, dict) else None
        loaded = int(d.get("loaded_bars") or 0) if isinstance(d, dict) else 0
        pct = float(d.get("percent") or 0.0) if isinstance(d, dict) else 0.0
        print(f"[auto] backfill status={st} loaded={loaded} percent={pct:.1f}")
        if loaded >= threshold_bars or st in ("success", "error", "partial", "idle"):
            return
        if time.time() - start > timeout_sec:
            print("[auto] backfill wait timeout; proceeding anyway")
            return
        time.sleep(4)


def _features_backfill_with_retries(target: int, recent: bool = False, attempts: int = 12, delay: float = 4.0) -> bool:
    params: Dict[str, Any] = {"target": target}
    if recent:
        params["recent"] = "true"
    for i in range(1, attempts + 1):
        # ensure features service is healthy before trying
        _req("GET", "/admin/features/status")
        sc, d = _req("POST", "/admin/features/backfill", params=params, timeout=120)
        status = d.get("status") if isinstance(d, dict) else None
        print(f"[auto] features/backfill target={target} recent={recent} -> {sc} {status}")
        if sc == 200 and isinstance(d, dict) and status == "ok":
            return True
        time.sleep(delay)
    return False


def features_backfill_sequence() -> None:
    # progressively larger to promote label coverage
    targets = [6000, 12000, 20000]
    results = []
    for t in targets:
        ok = _features_backfill_with_retries(t, recent=(t==12000))
        results.append((t, ok))
    print("[auto] features_backfill_sequence results:", ", ".join([f"{t}={ok}" for t, ok in results]))


def bottom_preview(limit: int, L: Optional[int] = None, D: Optional[float] = None, R: Optional[float] = None) -> Tuple[int, Dict[str, Any]]:
    params: Dict[str, Any] = {"limit": limit}
    if L is not None:
        params["lookahead"] = L
    if D is not None:
        params["drawdown"] = D
    if R is not None:
        params["rebound"] = R
    sc, d = _req("GET", "/api/training/bottom/preview", params=params)
    return sc, (d if isinstance(d, dict) else {})


def quick_param_sweep() -> Optional[Tuple[int, float, float]]:
    print("[auto] sweeping params...")
    lookaheads = [20, 30, 40, 60, 90]
    # Include lighter drawdowns and smaller rebounds for local data to avoid insufficient_labels
    drawdowns = [0.002, 0.003, 0.004, 0.005, 0.0075, 0.01, 0.015, 0.02]
    rebounds = [0.0005, 0.001, 0.002, 0.003, 0.004, 0.006, 0.008, 0.01]
    rows = []
    for L in lookaheads:
        for D in drawdowns:
            for R in rebounds:
                sc, d = bottom_preview(8000, L, D, R)
                st = d.get("status")
                have = int(d.get("have") or 0)
                req = int(d.get("required") or 150)
                pr = d.get("pos_ratio")
                print(f"[auto] L={L} D={D} R={R} -> {sc} {st} have={have} pos={pr}")
                if sc == 200 and st in ("ok", "insufficient_labels"):
                    rows.append({"L": L, "D": D, "R": R, "have": have, "req": req, "pos": pr})
    if not rows:
        return None
    # Prefer: have>=req and reasonable pos ratio (to avoid single-class)
    def within_pos(p: Optional[float]) -> bool:
        return (p is None) or (0.08 <= p <= 0.4)

    cands = [r for r in rows if r["have"] >= r["req"] and within_pos(r["pos"]) and (r["pos"] or 0) > 0]
    if not cands:
        # fallback: best available by have and closeness to req
        cands = sorted(rows, key=lambda r: (0 if r["have"] >= r["req"] else (r["req"] - r["have"]), abs((r["pos"] or 0.25) - 0.2)))
    else:
        cands = sorted(cands, key=lambda r: (r["have"] - r["req"], abs((r["pos"] or 0.2) - 0.2)))
    best = cands[0] if cands else None
    if not best:
        return None
    print("[auto] best=", json.dumps(best))
    return int(best["L"]), float(best["D"]), float(best["R"])


def train_with(L: int, D: float, R: float) -> Tuple[int, Dict[str, Any]]:
    sc, d = _req(
        "POST",
        "/api/training/run",
        params={
            "wait": "true",
            "store": "true",
            "limit": 2000,
            "bottom_lookahead": L,
            "bottom_drawdown": D,
            "bottom_rebound": R,
        },
        body={},
        timeout=300,
    )
    print("[auto] training/run ->", sc, str(d)[:300])
    return sc, (d if isinstance(d, dict) else {})


def main() -> int:
    if not wait_ready(300):
        return 2
    # If a production model already exists, skip bootstrapping
    sc_m, d_m = _req("GET", "/api/models/summary", params={"name": "bottom_predictor", "limit": 3})
    if sc_m == 200 and isinstance(d_m, dict) and d_m.get("has_model") and d_m.get("production"):
        # Inspect production metrics to decide whether to skip or attempt improvement
        prod = d_m.get("production") or {}
        metrics = prod.get("metrics") or {}
        auc_note = metrics.get("auc_note")
        pos_ratio = metrics.get("positives_ratio") or metrics.get("pos_ratio")
        samples = int(metrics.get("samples") or 0)
        val_samples = int(metrics.get("val_samples") or 0)
        degenerate = False
        try:
            if isinstance(auc_note, str) and auc_note.lower() == "single_class":
                degenerate = True
            if isinstance(pos_ratio, (int, float)) and pos_ratio < 0.05:
                degenerate = True
            if samples and samples < 300:
                degenerate = True
            if val_samples and val_samples < 200:
                degenerate = True
        except Exception:
            pass
        if not degenerate:
            print("[auto] production model already present; skipping bootstrap")
            return 0
        else:
            print("[auto] production exists but looks degenerate; attempting improvement")
    start_year_backfill()
    poll_backfill_until()
    features_backfill_sequence()

    # preview quick check default
    sc, d = bottom_preview(3000)
    ok_preview = sc == 200 and isinstance(d, dict) and d.get("status") == "ok"
    have = int((d or {}).get("have") or 0) if isinstance(d, dict) else 0
    req = int((d or {}).get("required") or 150) if isinstance(d, dict) else 150
    pos = (d or {}).get("pos_ratio") if isinstance(d, dict) else None
    if ok_preview and have >= req and (pos is not None and 0.05 <= pos <= 0.45):
        # Reasonable defaults, proceed with default training
        sc2, d2 = train_with(60, 0.015, 0.006)
        sc3, d3 = _req("GET", "/api/models/summary", params={"name": "bottom_predictor", "limit": 3})
        print("[auto] models/summary ->", sc3, str(d3)[:500])
        return 0 if (sc2 == 200 and isinstance(d2, dict) and d2.get("status") == "ok") else 1
    else:
        # Try to find better params via sweep
        pick = quick_param_sweep()
        if pick:
            L, D, R = pick
            sc2, d2 = train_with(L, D, R)
            sc3, d3 = _req("GET", "/api/models/summary", params={"name": "bottom_predictor", "limit": 3})
            print("[auto] models/summary ->", sc3, str(d3)[:500])
            return 0 if (sc2 == 200 and isinstance(d2, dict) and d2.get("status") == "ok") else 1
        # Fallback: try a permissive known-good local setting
        print("[auto] Sweep found nothing; trying fallback params L=60,D=0.002,R=0.003")
        sc2, d2 = train_with(60, 0.002, 0.003)
        sc3, d3 = _req("GET", "/api/models/summary", params={"name": "bottom_predictor", "limit": 3})
        print("[auto] models/summary ->", sc3, str(d3)[:500])
        return 0 if (sc2 == 200 and isinstance(d2, dict) and d2.get("status") == "ok") else 1
        print("[auto] No viable params found; skipping training")
        return 1


if __name__ == "__main__":
    try:
        rc = main()
        sys.exit(rc)
    except KeyboardInterrupt:
        print("[auto] interrupted")
        sys.exit(130)
