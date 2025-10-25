import os, json, requests

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
h = {'X-API-Key': key, 'Content-Type': 'application/json'}

# 1) Apply config (enable + defaults)
payload = {
    'enabled': True,
    'hazard': float(os.getenv('BOCPD_HAZARD','250') or 250),
    'min_down': float(os.getenv('BOCPD_MIN_DOWN','0.005') or 0.005),
    'cooldown_sec': float(os.getenv('BOCPD_COOLDOWN_SEC','300') or 300),
}
base, r = try_post(bases, '/admin/guard/bocpd/config', headers=h, data=json.dumps(payload), timeout=20)
try:
    data = r.json()
except Exception:
    data = {'_text': r.text[:300]}
print('base:', base)
print('config:', r.status_code, json.dumps(data, ensure_ascii=False))

# 2) Read status
base2, r = try_get([base] + [b for b in bases if b != base], '/api/guard/bocpd/status', headers={'X-API-Key': key}, timeout=20)
try:
    s = r.json()
except Exception:
    s = {'_text': r.text[:300]}
print('status:', r.status_code)
print(json.dumps(s, ensure_ascii=False, indent=2))
