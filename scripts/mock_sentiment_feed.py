"""
감성 목 피드: 일정 간격으로 랜덤 감성 티크를 POST하여 인제션/스트림을 점검합니다.

예)
  python scripts/mock_sentiment_feed.py --base http://localhost:8000 --api-key <키> --symbol XRPUSDT --interval 2
"""
from __future__ import annotations
import argparse
import time
import random
import httpx


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    p.add_argument("--api-key", default=None)
    p.add_argument("--symbol", default="XRPUSDT")
    p.add_argument("--interval", type=float, default=2.0, help="초 단위 전송 간격")
    p.add_argument("--provider", default="mock-feed")
    args = p.parse_args()

    headers = {"content-type": "application/json"}
    if args.api_key:
        headers["x-api-key"] = args.api_key

    base = args.base.rstrip("/")
    sym = args.symbol

    print(f"Start mock feed → {base} symbol={sym} interval={args.interval}s")
    with httpx.Client(timeout=10.0) as client:
        while True:
            now_ms = int(time.time() * 1000)
            body = {
                "symbol": sym,
                "ts": now_ms,
                "score": round(random.uniform(-1, 1), 3),
                "polarity": random.choice(["pos", "neg", "neu"]),
                "provider": args.provider,
                "meta": {"src": "mock"},
            }
            try:
                r = client.post(f"{base}/api/sentiment/tick", headers=headers, json=body)
                print(now_ms, r.status_code)
            except Exception as e:
                print("ERR:", e)
            time.sleep(max(0.1, args.interval))


if __name__ == "__main__":
    main()
