#!/usr/bin/env python3
import os
import json
import sys

try:
    import requests  # type: ignore
except Exception:
    print("requests not installed; please install it first.")
    sys.exit(1)

BASE = os.getenv('API_BASE', 'http://localhost:8000').rstrip('/')
KEY = os.getenv('API_KEY', 'dev-key')
HEADERS = {'X-API-Key': KEY}

def main():
    url = BASE + '/api/training/jobs'
    r = requests.get(url, headers=HEADERS, timeout=45)
    try:
        data = r.json()
    except Exception:
        print('non-JSON response:', r.text[:400])
        return 1
    if isinstance(data, list):
        print(json.dumps(data[:3], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    sys.exit(main())
