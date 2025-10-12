from __future__ import annotations
import time
import hmac
import hashlib
import aiohttp
from typing import Any, Dict, Optional, Tuple


class BinanceApiError(Exception):
    def __init__(self, status: int, data: Dict[str, Any]):
        self.status = status
        self.data = data or {}
        self.code = self.data.get("code")
        self.msg = self.data.get("msg") or self.data.get("message") or str(self.data)
        super().__init__(f"Binance HTTP {status} code={self.code} msg={self.msg}")


class BinanceSpotClient:
    """Minimal Binance Spot REST client (market orders + price)

    Notes:
    - Quantity is in base asset units (e.g., XRP for XRPUSDT)
    - For testnet, set base_url to https://testnet.binance.vision
    - Caller must ensure symbol formatting (e.g., 'XRPUSDT')
    """

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False) -> None:
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.base_url = "https://testnet.binance.vision" if testnet else "https://api.binance.com"
        self._time_offset_ms: int = 0
        self._last_sync_ts: float = 0.0
        self._exchange_info_cache: Dict[str, Dict[str, Any]] = {}

    async def _sync_time(self) -> None:
        now = time.time()
        # refresh every 5s to minimize clock skew impact
        if now - self._last_sync_ts < 5:
            return
        async with aiohttp.ClientSession() as sess:
            async with sess.get(f"{self.base_url}/api/v3/time", timeout=10) as resp:
                data = await resp.json()
                if resp.status == 200 and isinstance(data, dict) and data.get("serverTime"):
                    server_ms = int(data["serverTime"])
                    local_ms = int(now * 1000)
                    self._time_offset_ms = server_ms - local_ms
                    self._last_sync_ts = now

    async def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, signed: bool = False) -> Any:
        from urllib.parse import urlencode
        params = dict(params or {})
        headers = {"X-MBX-APIKEY": self.api_key}
        if signed:
            # ensure time sync to avoid -1021
            await self._sync_time()
            params.setdefault("timestamp", int(time.time() * 1000) + self._time_offset_ms)
            params.setdefault("recvWindow", 10000)
            # Build query string deterministically and use EXACTLY the same string for request URL
            # Use sorted items to be stable
            items = [(k, params[k]) for k in sorted(params)]
            query = urlencode(items)
            sig = hmac.new(self.api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()
            params["signature"] = sig
        url = f"{self.base_url}{path}"
        async with aiohttp.ClientSession() as sess:
            # For signed requests, attach all params (including signature) to the URL query to avoid
            # any re-encoding/ordering differences between signature and body encoding.
            if signed:
                items_all = [(k, params[k]) for k in sorted(params)]
                url = f"{url}?{urlencode(items_all)}"
                send_params = None
                send_data = None
            else:
                send_params = params if method.upper() == "GET" else None
                send_data = params if method.upper() != "GET" else None
            async with sess.request(method, url, params=send_params, data=send_data, headers=headers, timeout=15) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    # If timestamp error (-1021) or message indicates timestamp skew, resync and retry once
                    code = data.get("code") if isinstance(data, dict) else None
                    msg = (data.get("msg") or data.get("message") or "") if isinstance(data, dict) else ""
                    ts_error = False
                    if code == -1021:
                        ts_error = True
                    else:
                        mlow = str(msg).lower()
                        if "timestamp" in mlow or "server's time" in mlow or "ahead of the server" in mlow:
                            ts_error = True
                    if ts_error and signed:
                        await self._sync_time()
                        # rebuild signature with new timestamp
                        # Rebuild full query and URL
                        params_no_sig = {k: v for k, v in params.items() if k != "signature"}
                        params_no_sig["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
                        items_retry = [(k, params_no_sig[k]) for k in sorted(params_no_sig)]
                        query2 = urlencode(items_retry)
                        sig2 = hmac.new(self.api_secret, query2.encode("utf-8"), hashlib.sha256).hexdigest()
                        url2 = f"{self.base_url}{path}?{query2}&signature={sig2}"
                        async with sess.request(method, url2, headers=headers, timeout=15) as resp2:
                            data2 = await resp2.json()
                            if resp2.status >= 400:
                                raise BinanceApiError(resp2.status, data2 if isinstance(data2, dict) else {"raw": data2})
                            return data2
                    raise BinanceApiError(resp.status, data if isinstance(data, dict) else {"raw": data})
                return data

    async def get_price(self, symbol: str) -> float:
        r = await self._request("GET", "/api/v3/ticker/price", {"symbol": symbol}, signed=False)
        p = r.get("price")
        return float(p) if p is not None else float("nan")

    async def market_order(self, symbol: str, side: str, quantity: float) -> Dict[str, Any]:
        side_u = side.upper()
        if side_u not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        params = {
            "symbol": symbol,
            "side": side_u,
            "type": "MARKET",
            "quantity": f"{quantity:.8f}",
        }
        r = await self._request("POST", "/api/v3/order", params=params, signed=True)
        # Compute avg price if available
        avg_price = None
        if "fills" in r and isinstance(r["fills"], list) and r["fills"]:
            total_qty = 0.0
            total_quote = 0.0
            for f in r["fills"]:
                p = float(f.get("price", 0))
                q = float(f.get("qty", 0))
                total_qty += q
                total_quote += p * q
            if total_qty > 0:
                avg_price = total_quote / total_qty
        # Fallback to cummulativeQuoteQty/executedQty
        if avg_price is None:
            try:
                executed = float(r.get("executedQty") or 0)
                qquote = float(r.get("cummulativeQuoteQty") or 0)
                if executed > 0:
                    avg_price = qquote / executed
            except Exception:
                pass
        return {
            "orderId": r.get("orderId"),
            "clientOrderId": r.get("clientOrderId"),
            "transactTime": r.get("transactTime"),
            "executedQty": float(r.get("executedQty") or 0),
            "cummulativeQuoteQty": float(r.get("cummulativeQuoteQty") or 0),
            "avgPrice": float(avg_price) if avg_price is not None else None,
            "raw": r,
        }

    async def get_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        # cache per symbol
        if symbol in self._exchange_info_cache and time.time() - self._exchange_info_cache[symbol].get("_ts", 0) < 300:
            return self._exchange_info_cache[symbol]
        data = await self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol}, signed=False)
        filters: Dict[str, Any] = {}
        try:
            sym = (data.get("symbols") or [])[0]
            for f in sym.get("filters", []):
                filters[f.get("filterType")] = f
        except Exception:
            pass
        filters["_ts"] = time.time()
        self._exchange_info_cache[symbol] = filters
        return filters

    @staticmethod
    def snap_qty(step_size: float, qty: float) -> float:
        if step_size <= 0:
            return qty
        # floor to step
        steps = int(qty / step_size)
        return round(steps * step_size, 8)

    @staticmethod
    def min_qty_for_notional(price: float, min_notional: float, step_size: float) -> float:
        if price <= 0:
            return 0.0
        raw = (min_notional / price)
        if step_size > 0:
            steps = int((raw + step_size - 1e-12) / step_size)  # ceil
            return round(steps * step_size, 8)
        return raw

    async def my_trades(self, symbol: str, limit: int = 50, start_time_ms: Optional[int] = None, from_id: Optional[int] = None) -> list[dict]:
        """Fetch recent trades for the account on a given symbol.

        Maps to GET /api/v3/myTrades (signed).

        Args:
          symbol: e.g., 'XRPUSDT'
          limit: up to Binance limits (typically <= 1000)
          start_time_ms: optional epoch ms to bound
          from_id: optional trade id to start from (exclusive)

        Returns list of trade dicts as returned by Binance.
        """
        params: Dict[str, Any] = {"symbol": symbol, "limit": int(max(1, min(limit, 1000)))}
        if isinstance(start_time_ms, int) and start_time_ms > 0:
            params["startTime"] = int(start_time_ms)
        if isinstance(from_id, int) and from_id >= 0:
            params["fromId"] = int(from_id)
        data = await self._request("GET", "/api/v3/myTrades", params=params, signed=True)
        # Ensure list
        if not isinstance(data, list):
            return []
        # Normalize numeric conversions best-effort
        out = []
        for t in data:
            try:
                d = dict(t)
                # common numeric fields
                for k in ("price", "qty", "quoteQty", "commission"):
                    if k in d and d[k] is not None:
                        try:
                            d[k] = float(d[k])
                        except Exception:
                            pass
                out.append(d)
            except Exception:
                # skip malformed entries
                pass
        # Binance returns oldest->newest; keep as-is and let caller sort if needed
        return out
