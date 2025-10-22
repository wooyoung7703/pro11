import json
import asyncio
import os
import sys

# Ensure dev-friendly auth bypass if present in code
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("ALLOW_PUBLIC_STATUS", "1")

import httpx  # type: ignore
from asgi_lifespan import LifespanManager  # type: ignore
# Ensure project root on sys.path so `backend` package resolves when running directly
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from backend.apps.api.main import app  # type: ignore

async def main():
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # quick smoke: effective_threshold
            thr = await client.get("/api/inference/effective_threshold")
            print("effective_threshold:", thr.status_code, thr.text[:120])
            # target: trading orders since reset for XRPUSDT
            params = {"source": "db", "since_reset": "true", "symbol": "XRPUSDT", "limit": 5}
            resp = await client.get("/api/trading/orders", params=params)
            print("orders_status:", resp.status_code)
            try:
                data = resp.json()
            except Exception:
                print("orders_raw:", resp.text[:400])
                return
            summary = {
                "source": data.get("source"),
                "limit": data.get("limit"),
                "since_reset": data.get("since_reset"),
                "symbol": data.get("symbol"),
                "orders_count": len(data.get("orders", [])),
            }
            print("orders_summary:", json.dumps(summary, ensure_ascii=False))

if __name__ == "__main__":
    asyncio.run(main())
