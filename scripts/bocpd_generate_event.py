import os, json, time, requests

def try_post(bases, path, headers=None, data=None, timeout=20):
    last_err = None
    for b in bases:
        url = b.rstrip('/') + path
        try:
            r = requests.post(url, headers=headers, data=data, timeout=timeout)
            return b.rstrip('/'), r
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError('No base available')

def try_get(bases, path, headers=None, timeout=20):
    last_err = None
    for b in bases:
        url = b.rstrip('/') + path
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            return b.rstrip('/'), r
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError('No base available')

forced = os.getenv('API_BASE')
bases = [forced] if forced else ['http://localhost:8010', 'http://localhost:8000']
key = os.getenv('API_KEY','dev-key')
h_json = {'X-API-Key': key, 'Content-Type': 'application/json'}

# 1) Ensure guard is enabled and low threshold for an easy trigger
cfg = {
    'enabled': True,
    'hazard': float(os.getenv('BOCPD_HAZARD','250') or 250),
    'min_down': float(os.getenv('BOCPD_MIN_DOWN','0.005') or 0.005),
    'cooldown_sec': float(os.getenv('BOCPD_COOLDOWN_SEC','30') or 30),
}
base, r = try_post(bases, '/admin/guard/bocpd/config', headers=h_json, data=json.dumps(cfg), timeout=20)
try: print('base:', base); print('config:', r.status_code, r.json())
except Exception: print('config:', r.status_code, r.text[:200])

# 2) Feed a small series with a clear drop >= min_down
now = time.time() - 5
series = [100.0, 100.1, 100.05, 99.4, 99.3, 99.2]  # ~0.7-0.8% drop from peak
payload = {'prices': series, 'start_ts': now, 'interval_sec': 1.0}
base, r = try_post([base] + [b for b in bases if b != base], '/dev/guard/bocpd/feed', headers=h_json, data=json.dumps(payload), timeout=20)
try: print('feed:', r.status_code, r.json())
except Exception: print('feed:', r.status_code, r.text[:200])

# 3) Read status (should have at least one down_detected event)
base, r = try_get([base] + [b for b in bases if b != base], '/api/guard/bocpd/status', headers={'X-API-Key': key}, timeout=20)
try:
    s = r.json()
except Exception:
    s = {'_text': r.text[:300]}
print('status:', r.status_code)
print(json.dumps({
  'events_sample': s.get('events', [])[-3:],
  'status': s.get('status')
}, ensure_ascii=False, indent=2))
