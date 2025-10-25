#!/usr/bin/env python3
import os
import json
import itertools
import sys
import argparse
from typing import List, Tuple, Dict, Any, Optional

try:
    import requests  # type: ignore
except Exception:
    print("requests not installed; please install it first.")
    sys.exit(1)

BASE = os.getenv('API_BASE', 'http://localhost:8000').rstrip('/')
KEY = os.getenv('API_KEY', 'dev-key')
HEADERS = {'X-API-Key': KEY}

# Broad sweep defaults
LOOKAHEADS_DEFAULT = [30, 40, 60]
DRAWDOWNS_DEFAULT = [0.0075, 0.01, 0.015]
REBOUNDS_DEFAULT = [0.004, 0.006, 0.008]
LIMIT_DEFAULT = int(os.getenv('SWEEP_LIMIT', '3000'))


def _round6(x: float) -> float:
    return float(f"{x:.6f}")


def call_preview(params: dict) -> Tuple[int, Dict[str, Any]]:
    url = BASE + '/api/training/bottom/preview'
    r = requests.get(url, headers=HEADERS, params=params, timeout=60)
    try:
        data = r.json()
    except Exception:
        data = {'_text': r.text[:500]}
    return r.status_code, data  # type: ignore[return-value]


def get_setting(key: str):
    url = BASE + f'/admin/settings/{key}'
    r = requests.get(url, headers=HEADERS, timeout=15)
    try:
        data = r.json()
    except Exception:
        data = {'_text': r.text[:300]}
    if isinstance(data, dict) and 'item' in data and isinstance(data['item'], dict):
        return data['item'].get('value')
    return None


def set_setting(key: str, value):
    url = BASE + f'/admin/settings/{key}'
    r = requests.put(url, headers={**HEADERS, 'Content-Type': 'application/json'}, data=json.dumps({'value': value}), timeout=20)
    try:
        data = r.json()
    except Exception:
        data = {'_text': r.text[:300]}
    print('set', key, '->', r.status_code, json.dumps(data, ensure_ascii=False)[:400])
    return r.status_code, data


def choose_best(rows: List[Dict[str, Any]], pos_min: float = 0.08, pos_max: float = 0.4) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    ok = [r for r in rows if r['sc'] == 200 and isinstance(r['d'], dict) and r['d'].get('status') in ('ok', 'insufficient_labels')]
    ok2: List[Dict[str, Any]] = []
    for r in ok:
        d = r['d']
        have = d.get('have') or 0
        req = d.get('required') or 150
        pr = d.get('pos_ratio')
        ok2.append({**r, 'have': have, 'req': req, 'delta': have - req, 'pos_ratio': pr})

    if not ok2:
        raise RuntimeError('No viable parameter combos found')

    # Prefer candidates that meet required count and have a reasonable positive ratio window
    cands = [
        r for r in ok2
        if r['have'] >= r['req'] and (r['pos_ratio'] is None or pos_min <= r['pos_ratio'] <= pos_max)
    ]
    if not cands:
        cands = sorted(ok2, key=lambda r: (abs(r['delta']), r['delta'] < 0))
    else:
        # Among valid cands, choose smallest positive surplus and pos_ratio close to target (~0.2)
        cands = sorted(cands, key=lambda r: (r['delta'], abs((r['pos_ratio'] or ((pos_min + pos_max) / 2)) - 0.2)))

    best = cands[0]
    return best, ok2


def run_training(limit: int, store: bool = True, cv_splits: int = 3, trigger: Optional[str] = None,
                 bottom_lookahead: Optional[int] = None, bottom_drawdown: Optional[float] = None, bottom_rebound: Optional[float] = None) -> Dict[str, Any]:
    params = {'limit': limit, 'store': str(store).lower(), 'cv_splits': cv_splits}
    if trigger:
        params['trigger'] = trigger
    if bottom_lookahead is not None:
        params['bottom_lookahead'] = int(bottom_lookahead)
    if bottom_drawdown is not None:
        params['bottom_drawdown'] = float(bottom_drawdown)
    if bottom_rebound is not None:
        params['bottom_rebound'] = float(bottom_rebound)
    url = BASE + '/api/training/run'
    r = requests.post(url, headers=HEADERS, params=params, timeout=300)
    try:
        data = r.json()
    except Exception:
        data = {'_text': r.text[:500]}
    return {'status_code': r.status_code, 'data': data}


def manual_promote(model_id: int) -> Dict[str, Any]:
    url = BASE + f'/api/models/{model_id}/promote'
    r = requests.post(url, headers=HEADERS, timeout=30)
    try:
        data = r.json()
    except Exception:
        data = {'_text': r.text[:500]}
    return {'status_code': r.status_code, 'data': data}


def build_grid(args) -> Tuple[List[int], List[float], List[float]]:
    if args.micro:
        # Fetch current settings; fall back to defaults if missing
        cur_L = get_setting('bottom.lookahead') or LOOKAHEADS_DEFAULT[1]
        cur_D = get_setting('bottom.drawdown') or DRAWDOWNS_DEFAULT[1]
        cur_R = get_setting('bottom.rebound') or REBOUNDS_DEFAULT[1]

        try:
            cur_L = int(cur_L)
            cur_D = float(cur_D)
            cur_R = float(cur_R)
        except Exception:
            cur_L, cur_D, cur_R = LOOKAHEADS_DEFAULT[1], DRAWDOWNS_DEFAULT[1], REBOUNDS_DEFAULT[1]

        Ls = sorted(set([max(10, int(cur_L + d)) for d in (-10, -5, 0, 5, 10)]))
        Ds = sorted(set([_round6(max(0.001, cur_D * f)) for f in (0.9, 1.0, 1.1)]))
        Rs = sorted(set([_round6(max(0.001, cur_R * f)) for f in (0.9, 1.0, 1.1)]))
    else:
        Ls, Ds, Rs = LOOKAHEADS_DEFAULT, DRAWDOWNS_DEFAULT, REBOUNDS_DEFAULT
    return Ls, Ds, Rs


def main():
    parser = argparse.ArgumentParser(description='Sweep bottom params, apply best, and optionally train/promote.')
    parser.add_argument('--micro', action='store_true', help='Do a micro fine-tune sweep around current settings')
    parser.add_argument('--limit', type=int, default=LIMIT_DEFAULT, help='Preview/training limit')
    parser.add_argument('--train', action='store_true', help='Trigger training after applying best params')
    parser.add_argument('--cv-splits', type=int, default=3, help='CV splits for training')
    parser.add_argument('--promote', choices=['auto', 'manual', 'skip'], default='auto', help='Promotion mode after training')
    parser.add_argument('--trigger', type=str, default='manual_fine_tune', help='Training trigger label')
    parser.add_argument('--pos-min', type=float, default=0.08, help='Minimum acceptable positive ratio in preview selection')
    parser.add_argument('--pos-max', type=float, default=0.4, help='Maximum acceptable positive ratio in preview selection')
    args = parser.parse_args()

    Ls, Ds, Rs = build_grid(args)

    rows: List[Dict[str, Any]] = []
    for L, D, R in itertools.product(Ls, Ds, Rs):
        sc, d = call_preview({'limit': args.limit, 'lookahead': L, 'drawdown': D, 'rebound': R})
        rows.append({'L': L, 'D': D, 'R': R, 'sc': sc, 'd': d})

    try:
        best, ok2 = choose_best(rows, pos_min=float(args.pos_min), pos_max=float(args.pos_max))
    except RuntimeError as e:
        print(str(e))
        print(json.dumps({'rows_sample': rows[:3]}, ensure_ascii=False, indent=2))
        return 0

    print('== Sweep summary ==')
    for r in sorted(ok2, key=lambda r: (r['req'] - r['have'] if r['have'] < r['req'] else r['have'] - r['req']))[:12]:
        print(f"L={r['L']}, D={r['D']}, R={r['R']} -> have={r['have']}, req={r['req']}, pos_ratio={r['pos_ratio']}")

    print('\n== Recommended ==')
    print(json.dumps(best, ensure_ascii=False, indent=2))

    # Persist via admin settings
    set_setting('bottom.lookahead', best['L'])
    set_setting('bottom.drawdown', best['D'])
    set_setting('bottom.rebound', best['R'])

    if not args.train:
        print('Done. (Applied params only)')
        return 0

    print('\n== Training run ==')
    train_res = run_training(limit=max(args.limit, 2000), store=True, cv_splits=args.cv_splits, trigger=args.trigger,
                             bottom_lookahead=int(best['L']), bottom_drawdown=float(best['D']), bottom_rebound=float(best['R']))
    print(json.dumps(train_res, ensure_ascii=False, indent=2)[:2000])

    data = train_res.get('data') or {}
    promotion = (data or {}).get('promotion') if isinstance(data, dict) else None
    model_id = (data or {}).get('model_id') if isinstance(data, dict) else None

    if args.promote == 'manual' and model_id and (not (promotion or {}).get('promoted')):
        print('\n== Manual promote ==')
        prom = manual_promote(int(model_id))
        print(json.dumps(prom, ensure_ascii=False, indent=2))

    print('Done.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
