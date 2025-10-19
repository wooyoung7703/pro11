#!/usr/bin/env python3
"""
Bootstrap data and train a bottom model against a local backend.
- Starts 1y OHLCV backfill
- Polls for progress for a bounded time
- Backfills features to a target
- Sweeps bottom-label params if needed
- Triggers a synchronous training run (store=true)

Env:
  API_BASE (default: http://localhost:8010)
  API_KEY  (default: dev-key)
"""
import os, sys, time, json
from typing import Any, Dict, Tuple
import requests

BASE = os.getenv("API_BASE", "http://localhost:8010").rstrip('/')
HEADERS = {"X-API-Key": os.getenv("API_KEY", "dev-key")}


def _get(path: str, **kw) -> Tuple[int, Any]:
    r = requests.get(BASE + path, headers=HEADERS, timeout=60, **kw)
    try:
        d = r.json()
    except Exception:
        d = {"_text": r.text[:500]}
    return r.status_code, d


def _post(path: str, **kw) -> Tuple[int, Any]:
    r = requests.post(BASE + path, headers=HEADERS, timeout=120, **kw)
    try:
        d = r.json()
    except Exception:
        d = {"_text": r.text[:500]}
    return r.status_code, d


def jprint(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def start_year_backfill():
    sc, d = _post('/api/ohlcv/backfill/year/start', params={'overwrite': 'true', 'purge': 'false'})
    print('year_backfill/start ->', sc)
    jprint(d)
    return sc, d


def poll_backfill_until(threshold_bars: int = 5000, timeout_sec: int = 900):
    print('\nPolling backfill status...')
    start = time.time()
    last = 0
    while True:
        sc, d = _get('/api/ohlcv/backfill/year/status')
        st = d.get('status') if isinstance(d, dict) else None
        loaded = int(d.get('loaded_bars') or 0) if isinstance(d, dict) else 0
        pct = float(d.get('percent') or 0.0)
        # Human-friendly print without f-string braces causing shell issues
        print('status:', st, 'loaded=', loaded, 'percent=', round(pct, 1))
        if loaded >= threshold_bars or st in ('success', 'error', 'partial'):
            return sc, d
        if time.time() - start > timeout_sec:
            print('timeout waiting for backfill progress; proceeding best-effort')
            return sc, d
        time.sleep(5)


def ensure_features(target: int = 6000):
    print('\nBackfilling features to', target)
    for attempt in range(12):
        sc, d = _post('/admin/features/backfill', params={'target': target})
        print('features/backfill ->', sc, d.get('status'))
        if d.get('status') == 'ok':
            jprint(d)
            return sc, d
        time.sleep(5)
    return sc, d


def ensure_features_recent(target: int = 8000):
    """Backfill features from the most recent tail (DESC candles then compute chronologically).

    This helps align feature windows with the newest OHLCV for better label coverage in bootstrap.
    """
    print('\nBackfilling features (recent tail) to', target)
    for attempt in range(12):
        sc, d = _post('/admin/features/backfill', params={'target': target, 'recent': 'true'})
        print('features/backfill?recent=true ->', sc, d.get('status'))
        if isinstance(d, dict) and d.get('status') == 'ok':
            jprint(d)
            return sc, d
        time.sleep(5)
    return sc, d


def bottom_preview(limit: int = 5000, L=None, D=None, R=None):
    params: Dict[str, Any] = {'limit': limit}
    if L is not None: params['lookahead'] = L
    if D is not None: params['drawdown'] = D
    if R is not None: params['rebound'] = R
    return _get('/api/training/bottom/preview', params=params)


def sweep_bottom_params():
    print('\nSweeping bottom parameters...')
    # Broadened grid: include more lenient thresholds to increase positives
    lookaheads = [30, 40, 45, 60]
    drawdowns = [0.002, 0.003, 0.004, 0.005, 0.0075, 0.01, 0.015]
    rebounds = [0.001, 0.002, 0.003, 0.004, 0.006, 0.008]
    rows = []
    for l in lookaheads:
        for dd in drawdowns:
            for rb in rebounds:
                sc, d = bottom_preview(5000, l, dd, rb)
                st = d.get('status') if isinstance(d, dict) else None
                have = d.get('have') if isinstance(d, dict) else None
                pr = d.get('pos_ratio') if isinstance(d, dict) else None
                print('  L=', l, 'D=', dd, 'R=', rb, '->', sc, st, 'have=', have, 'pos=', pr)
                if sc == 200 and isinstance(d, dict) and st in ('ok','insufficient_labels'):
                    have_i = d.get('have') or 0
                    req_i = d.get('required') or 150
                    rows.append({
                        'L': l, 'D': dd, 'R': rb,
                        'have': have_i,
                        'req': req_i,
                        'delta': have_i - req_i,
                        'pos_ratio': pr
                    })
    if not rows:
        return None
    cands = [r for r in rows if r['have'] >= r['req'] and (r['pos_ratio'] is None or 0.08 <= r['pos_ratio'] <= 0.4)]
    if cands:
        cands = sorted(cands, key=lambda r: (r['delta'], abs((r['pos_ratio'] or 0.25) - 0.2)))
        best = cands[0]
    else:
        best = sorted(rows, key=lambda r: (abs(r['delta']), r['delta'] < 0))[0]
    print('Best params:')
    jprint(best)
    return int(best['L']), float(best['D']), float(best['R'])


def trigger_training_wait_store(L=None, D=None, R=None):
    print('\nTrigger training (wait=true, store=true)')
    # Use a slightly larger limit if available
    params: Dict[str, Any] = {'wait': 'true', 'store': 'true', 'limit': 2000}
    if L is not None:
        params.update({'bottom_lookahead': L, 'bottom_drawdown': D, 'bottom_rebound': R})
    sc, d = _post('/api/training/run', params=params, json={})
    print('training/run ->', sc)
    jprint(d)
    return sc, d


def show_models():
    sc, d = _get('/api/models/summary', params={'name': 'bottom_predictor', 'limit': 6})
    print('\nModels summary ->', sc)
    jprint(d)
    return sc, d


def main():
    start_year_backfill()
    poll_backfill_until(threshold_bars=5000, timeout_sec=900)
    ensure_features(target=6000)
    sc, d = bottom_preview(5000)
    # If preview shows insufficient data/labels, try a tail-based (recent) feature backfill and preview again
    if not (isinstance(d, dict) and d.get('status') == 'ok' and (d.get('have') or 0) >= (d.get('required') or 150)):
        print('\nInitial preview not sufficient; attempting recent tail feature backfill...')
        ensure_features_recent(target=8000)
        sc, d = bottom_preview(5000)
    L = D = R = None
    if not (isinstance(d, dict) and d.get('status') == 'ok' and (d.get('have') or 0) >= (d.get('required') or 150)):
        best = sweep_bottom_params()
        if best:
            L, D, R = best
    trigger_training_wait_store(L, D, R)
    show_models()

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Interrupted')
        sys.exit(130)
