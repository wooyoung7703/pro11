#!/usr/bin/env python3
import os
import sys
import json
from typing import Any, Dict, Optional

try:
    import requests  # type: ignore
except Exception:
    print("requests not installed; please install it first.")
    sys.exit(1)

BASE = os.getenv('API_BASE', 'http://localhost:8000').rstrip('/')
KEY = os.getenv('API_KEY', 'dev-key')
HEADERS = {'X-API-Key': KEY}

def jget(path: str, timeout: int = 30) -> Dict[str, Any]:
    r = requests.get(BASE + path, headers=HEADERS, timeout=timeout)
    try:
        return {'status_code': r.status_code, 'data': r.json()}
    except Exception:
        return {'status_code': r.status_code, 'data': {'_text': r.text[:800]}}

def jpost(path: str, payload: Optional[Dict[str, Any]] = None, timeout: int = 30) -> Dict[str, Any]:
    headers = dict(HEADERS)
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    r = requests.post(BASE + path, headers=headers, data=(json.dumps(payload) if payload is not None else None), timeout=timeout)
    try:
        return {'status_code': r.status_code, 'data': r.json()}
    except Exception:
        return {'status_code': r.status_code, 'data': {'_text': r.text[:800]}}

def main():
    # 1) read thresholds
    before = jget('/admin/inference/thresholds')
    print('before:', json.dumps(before, ensure_ascii=False))
    # 2) set override (default 0.92 or env INFER_SET_THRESHOLD)
    thr = float(os.getenv('INFER_SET_THRESHOLD', '0.92'))
    setres = jpost('/admin/inference/auto/threshold', {'threshold_override': thr})
    print('set:', json.dumps(setres, ensure_ascii=False))
    # 3) read thresholds and settings snapshot
    after = jget('/admin/inference/thresholds')
    settings = jget('/admin/inference/settings')
    print('after:', json.dumps(after, ensure_ascii=False))
    print('settings:', json.dumps(settings, ensure_ascii=False))
    # basic check
    ok = (after.get('status_code') == 200)
    sys.exit(0 if ok else 1)

if __name__ == '__main__':
    sys.exit(main())
