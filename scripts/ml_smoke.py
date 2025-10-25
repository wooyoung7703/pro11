#!/usr/bin/env python3
import os
import sys
import json
import time
from typing import Any, Dict

try:
    import requests  # type: ignore
except Exception:
    print("requests not installed; please install it first.")
    sys.exit(1)

BASE = os.getenv('API_BASE', 'http://localhost:8000').rstrip('/')
KEY = os.getenv('API_KEY', 'dev-key')
HEADERS = {'X-API-Key': KEY}

def call(method: str, path: str, timeout: int = 30, **kw) -> Dict[str, Any]:
    url = BASE + path
    try:
        r = requests.request(method, url, headers=HEADERS, timeout=timeout, **kw)
        try:
            data = r.json()
        except Exception:
            data = {'_text': r.text[:600]}
        print(method, path, '->', r.status_code, json.dumps(data)[:400])
        return {'status_code': r.status_code, 'data': data}
    except Exception as e:
        print(method, path, '-> exception', str(e))
        return {'status_code': 0, 'data': {'error': str(e)}}

def retry(func, attempts=2, delay=2):
    last = None
    for i in range(attempts):
        last = func()
        sc = last.get('status_code')
        if isinstance(sc, int) and sc >= 200 and sc < 500:
            break
        time.sleep(delay)
    return last

def main():
    retry(lambda: call('GET', '/admin/features/status'))
    retry(lambda: call('GET', '/admin/inference/settings'))
    retry(lambda: call('POST', '/admin/ml/cooldown/reset'))
    retry(lambda: call('POST', '/api/trading/ml/preview', json={'debug': False}))
    retry(lambda: call('POST', '/api/trading/ml/trigger', json={'auto_execute': False, 'reason':'smoke_test'}))
    retry(lambda: call('POST', '/api/trading/ml/trigger', json={'auto_execute': True, 'size': 0.1, 'reason':'smoke_test'}))
    return 0

if __name__ == '__main__':
    sys.exit(main())
