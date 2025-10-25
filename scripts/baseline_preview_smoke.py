#!/usr/bin/env python3
import os, json, requests

base = os.getenv('API_BASE','http://localhost:8000').rstrip('/')
key = os.getenv('API_KEY','dev-key')
h = {'X-API-Key': key, 'Content-Type':'application/json'}

payload = {
  'model': os.getenv('BASELINE_MODEL','xgb'),
  # Optional overrides
  # 'threshold': 0.5,
  # 'limit': 1200,
  # 'lookahead': 30,
  # 'drawdown': 0.0075,
  # 'rebound': 0.004,
}

r = requests.post(base + '/api/training/baseline/preview', headers=h, data=json.dumps(payload), timeout=120)
try:
    data = r.json()
except Exception:
    data = {'_text': r.text[:500]}
print('status:', r.status_code)
# Print compact metrics summary
md = data.get('metrics') or {}
summary = {
    'status': data.get('status'),
    'model': data.get('model'),
    'val_samples': md.get('val_samples'),
    'auc': md.get('auc'),
    'pr_auc': md.get('pr_auc'),
    'accuracy': md.get('accuracy'),
    'brier': md.get('brier'),
    'ece': md.get('ece'),
    'note': md.get('auc_note'),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
