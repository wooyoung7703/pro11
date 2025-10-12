"""
간단한 감성 API 스모크 테스트 스크립트.
- 목적: 로컬 백엔드가 기동된 상태에서 감성 인제션/조회 경로를 빠르게 점검
- 전제: httpx 설치됨(pyproject dev deps), 백엔드 API_KEY/.env 구성 완료

사용법(예):
  python scripts/smoke_sentiment_api.py --base http://localhost:8000 --api-key <키> --symbol XRPUSDT
"""
from __future__ import annotations
import argparse
import time
import random
import httpx


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--api-key", required=False, default=None)
    p.add_argument("--symbol", default="XRPUSDT")
    args = p.parse_args()

    headers = {}
    if args.api_key:
        headers["x-api-key"] = args.api_key

    base = args.base.rstrip("/")
    sym = args.symbol

    # 1) tick ingest
    now_ms = int(time.time() * 1000)
    body = {
        "symbol": sym,
        "ts": now_ms,
        "score": round(random.uniform(-1, 1), 3),
        "polarity": random.choice(["pos", "neg", "neu"]),
        "provider": "smoke",
        "meta": {"source": "smoke-test"},
    }
    r = httpx.post(f"{base}/api/sentiment/tick", headers=headers, json=body, timeout=10.0)
    print("POST /api/sentiment/tick:", r.status_code, r.text[:200])
    r.raise_for_status()

    # 2) latest
    r2 = httpx.get(f"{base}/api/sentiment/latest", headers=headers, params={"symbol": sym, "windows": "5m,15m,60m"}, timeout=10.0)
    print("GET /api/sentiment/latest:", r2.status_code, r2.text[:200])
    r2.raise_for_status()

    # 3) history (최근 5분)
    end_ms = now_ms
    start_ms = end_ms - 5 * 60 * 1000
    r3 = httpx.get(
        f"{base}/api/sentiment/history",
        headers=headers,
        params={"symbol": sym, "start": start_ms, "end": end_ms, "step": "1m", "agg": "mean"},
        timeout=10.0,
    )
    print("GET /api/sentiment/history:", r3.status_code, (r3.text[:200] + ("..." if len(r3.text) > 200 else "")))
    r3.raise_for_status()

    print("성공: 감성 API 스모크 테스트 통과")


if __name__ == "__main__":
    main()
