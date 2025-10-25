#!/usr/bin/env python3
import os, json, requests
base = os.getenv('API_BASE','http://localhost:8000').rstrip('/')
key = os.getenv('API_KEY','dev-key')
h = {'X-API-Key': key, 'Content-Type': 'application/json'}

def main():
    payload = {
        'a': {'model': os.getenv('BASELINE_A','xgb'), 'limit': int(os.getenv('BASELINE_LIMIT','1000'))},
        'b': {'model': os.getenv('BASELINE_B','lgbm'), 'limit': int(os.getenv('BASELINE_LIMIT','1000'))},
    }
    r = requests.post(base + '/api/training/baseline/compare', headers=h, data=json.dumps(payload), timeout=180)
    try:
        data = r.json()
    except Exception:
        data = {'_text': r.text[:1000]}
    out = {
        'status_code': r.status_code,
        'status': data.get('status'),
        'a_model': (data.get('a') or {}).get('model'),
        'b_model': (data.get('b') or {}).get('model'),
        'delta': data.get('delta'),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
