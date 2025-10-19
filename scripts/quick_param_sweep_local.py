#!/usr/bin/env python3
import os, json, itertools, sys
try:
    import requests
except Exception:
    print("ERROR: requests not installed. Run: python3 -m pip install requests", file=sys.stderr)
    sys.exit(2)

BASE = os.getenv('API_BASE','http://localhost:8010').rstrip('/')
HEADERS = {'X-API-Key': os.getenv('API_KEY','dev-key')}
LIMIT = int(os.getenv('PREVIEW_LIMIT','5000'))

lookaheads = [30, 40, 60, 90]
drawdowns = [0.005, 0.0075, 0.01, 0.015]
rebounds = [0.002, 0.004, 0.006, 0.008]

rows = []
for L, D, R in itertools.product(lookaheads, drawdowns, rebounds):
    try:
        r = requests.get(BASE + '/api/training/bottom/preview', headers=HEADERS,
                         params={'limit': LIMIT, 'lookahead': L, 'drawdown': D, 'rebound': R}, timeout=20)
        try:
            d = r.json()
        except Exception:
            d = {'_text': r.text[:200]}
    except Exception as e:
        print('ERR', L, D, R, '->', e, file=sys.stderr)
        continue
    st = d.get('status') if isinstance(d, dict) else None
    if st not in ('ok','insufficient_labels'):
        continue
    have = int(d.get('have') or 0)
    req = int(d.get('required') or 150)
    pr = d.get('pos_ratio')
    rows.append({'L':L,'D':D,'R':R,'st':st,'have':have,'req':req,'pos':pr})

if not rows:
    print('No rows; is API up at', BASE, '?', file=sys.stderr)
    sys.exit(2)

# Prefer: have>=req and pos>0
cands = [r for r in rows if r['have']>=r['req'] and (r['pos'] or 0) > 0]
if not cands:
    # fallback: any with pos>0, then by have
    cands = [r for r in rows if (r['pos'] or 0) > 0]
    cands = sorted(cands, key=lambda r: (-r['have'], r['L']))
else:
    cands = sorted(cands, key=lambda r: (r['have']-r['req'], abs((r['pos'] or 0.2)-0.2)))

best = cands[0] if cands else None
print('== Sweep summary (top 12 by have) ==')
for r in sorted(rows, key=lambda r: (-r['have'], r['L']))[:12]:
    print(f"L={r['L']}, D={r['D']:.4f}, R={r['R']:.4f} -> st={r['st']}, have={r['have']}, req={r['req']}, pos={r['pos']}")

print('\n== Recommended ==')
print(json.dumps(best, ensure_ascii=False, indent=2))

# Emit machine-readable
print('BEST_JSON=', json.dumps(best or {}, separators=(',',':')))
