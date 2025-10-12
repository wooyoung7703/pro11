from __future__ import annotations
import time
from dataclasses import dataclass, field
import logging
import os
from typing import List, Dict, Any, Optional
from backend.apps.risk.service.risk_engine import RiskEngine
from backend.apps.trading.repository.trading_repository import TradingRepository
from backend.common.config.base_config import load_config
try:  # optional import
    from backend.apps.trading.adapters.binance_spot import BinanceSpotClient  # type: ignore
except Exception:  # pragma: no cover
    BinanceSpotClient = None  # type: ignore

@dataclass
class Order:
    id: int
    symbol: str
    side: str  # buy|sell
    size: float  # positive quantity
    price: float  # submitted price (market fill uses current)
    type: str  # market
    status: str = "open"  # open|filled|rejected
    created_ts: float = field(default_factory=time.time)
    filled_ts: float | None = None
    reason: str | None = None

class TradingService:
    """Lightweight in-memory simulated trading layer.

    - Only market orders supported
    - Immediate or reject based on risk_engine evaluation
    - Not persisted (ephemeral)
    - For real integration, replace with exchange adapter implementing submit/cancel/fill streams
    """
    def __init__(self, risk: RiskEngine):
        self._risk = risk
        self._orders: List[Order] = []
        self._next_id = 1
        self._repo = TradingRepository()
        # Optional exchange client
        self._cfg = load_config()
        self._use_exchange = bool(os.getenv("EXCHANGE_TRADING_ENABLED", "0").lower() in {"1","true","yes","on"})
        # Runtime safety guard: require explicit override to use real exchange on mainnet
        if self._use_exchange and self._cfg.exchange == "binance" and not getattr(self._cfg, "binance_testnet", False):
            allow = os.getenv("SAFETY_ALLOW_REAL_TRADING", "0").lower() in {"1","true","yes","on"}
            if not allow:
                logging.getLogger("trading").warning(
                    "[safety] Real trading blocked: EXCHANGE_TRADING_ENABLED=true, BINANCE_TESTNET=false, "
                    "but SAFETY_ALLOW_REAL_TRADING is not set. Forcing simulated mode.")
                self._use_exchange = False
        self._binance: Any | None = None
        if self._use_exchange and self._cfg.exchange == "binance" and self._cfg.binance_api_key and self._cfg.binance_api_secret and BinanceSpotClient:
            try:
                self._binance = BinanceSpotClient(self._cfg.binance_api_key, self._cfg.binance_api_secret, testnet=self._cfg.binance_testnet)
            except Exception:
                self._binance = None

    def list_orders(self, limit: int = 100) -> List[Dict[str, Any]]:
        # Prefer in-memory, but if empty (e.g., after restart), fetch from DB
        if self._orders:
            return [o.__dict__ for o in sorted(self._orders, key=lambda x: x.id, reverse=True)[:limit]]
        # best-effort DB fallback
        import asyncio
        try:
            return asyncio.get_event_loop().run_until_complete(self._repo.fetch_recent(limit))  # type: ignore
        except Exception:
            return []

    def get_positions(self) -> List[Dict[str, Any]]:
        out = []
        for p in self._risk.positions.values():
            out.append({"symbol": p.symbol, "size": p.size, "entry_price": p.entry_price})
        return out

    async def list_orders_exchange(self, *, symbol: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """List recent orders directly from the exchange when enabled.

        For Binance Spot, we use the account trades (myTrades) as the source of truth
        for executed fills. These are mapped to our order schema best-effort.

        Falls back to DB-backed list when exchange client is not available.
        """
        # Guard: exchange enabled and client exists
        if not self._binance or not self._use_exchange:
            # Fall back to persistent DB via existing list_orders path
            return self.list_orders(limit=limit)
        sym = (symbol or self._cfg.symbol).upper()
        try:
            trades = await self._binance.my_trades(sym, limit=int(limit))  # type: ignore[attr-defined]
        except Exception:
            return self.list_orders(limit=limit)
        # Map trades to order-like rows (filled only)
        out: List[Dict[str, Any]] = []
        # Assign synthetic incremental ids distinct from DB ids; use tradeId or orderId
        for t in trades:
            try:
                side = ("BUY" if t.get("isBuyer") else "SELL") if t.get("isBuyer") is not None else None
                side = (side or "").lower()
                price = float(t.get("price") or 0)
                qty = float(t.get("qty") or 0)
                ts_ms = int(t.get("time") or 0)
                order_id = t.get("orderId") or t.get("id") or t.get("tradeId")
                out.append({
                    "id": int(order_id) if isinstance(order_id, int) else None,
                    "symbol": sym,
                    "side": side or ("buy" if (t.get("isBuyer") is True) else ("sell" if (t.get("isBuyer") is False) else None)),
                    "size": qty,
                    "price": price,
                    "status": "filled",
                    # created_ts/filled_ts: map to trade time (same for spot fills)
                    "created_ts": ts_ms / 1000.0 if ts_ms else None,
                    "filled_ts": ts_ms / 1000.0 if ts_ms else None,
                    # include raw fields for reference
                    "reason": "[binance_my_trades]",
                    "exchange": "binance",
                })
            except Exception:
                pass
        # Newest first for UI
        out.sort(key=lambda x: (x.get("filled_ts") or x.get("created_ts") or 0), reverse=True)
        return out[: int(limit)]

    async def submit_market(self, symbol: str, side: str, size: float, price: float, atr: float | None = None, reason: Optional[str] = None) -> Dict[str, Any]:
        if side not in ("buy", "sell"):
            return {"status": "error", "error": "invalid_side"}
        if size <= 0:
            return {"status": "error", "error": "invalid_size"}
        intended_size = size if side == "buy" else -size
        eval_res = self._risk.evaluate_order(symbol, price, intended_size, atr=atr)
        order = Order(id=self._next_id, symbol=symbol, side=side, size=size, price=price, type="market")
        if reason:
            order.reason = reason
        self._next_id += 1
        if not eval_res.get("allowed"):
            order.status = "rejected"
            # append evaluator reasons to provided reason if any
            eval_reasons = ",".join(eval_res.get("reasons") or [])
            order.reason = (order.reason + ("," if order.reason and eval_reasons else "") + eval_reasons) if (order.reason or eval_reasons) else order.reason
            self._orders.append(order)
            # persist rejected order as well for audit
            try:
                await self._repo.insert_order(
                    symbol=symbol,
                    side=side,
                    size=size,
                    price=price,
                    status=order.status,
                    created_ts=order.created_ts,
                    filled_ts=order.filled_ts,
                    reason=order.reason,
                )
            except Exception:
                pass
            return {"status": "rejected", "order": order.__dict__, "reasons": eval_res.get("reasons")}
        # If real-exchange mode enabled, attempt to submit real market order; else simulate
        size_delta = size if side == "buy" else -size
        fill = None
        if self._binance and self._cfg.binance_account_type == "spot":
            try:
                # For spot, validate quantity against symbol filters (prefer MARKET_LOT_SIZE for market orders)
                filters = await self._binance.get_symbol_filters(symbol)
                lot = (filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE") or {})
                step = float(lot.get("stepSize") or 0.0)
                min_qty = float(lot.get("minQty") or 0.0)
                # NOTIONAL (newer) or MIN_NOTIONAL (older)
                notional_f = (filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {})
                min_notional = float(notional_f.get("minNotional") or 0.0)
                # Use live price for min notional computation
                try:
                    price_now = await self._binance.get_price(symbol)
                except Exception:
                    price_now = price
                # snap and enforce minimums
                from backend.apps.trading.adapters.binance_spot import BinanceSpotClient as _B
                qty_use = _B.snap_qty(step, size)
                # ensure >= min_qty
                if qty_use < min_qty:
                    # if price allows, bump up to min_qty; else reject
                    qty_use = min_qty
                # ensure min notional
                if min_notional > 0:
                    need = _B.min_qty_for_notional(price_now, min_notional, step)
                    if qty_use < need:
                        qty_use = need
                if qty_use <= 0:
                    order.status = "rejected"
                    order.reason = (order.reason or "") + " [binance_reject:qty_invalid]"
                    self._orders.append(order)
                    try:
                        await self._repo.insert_order(symbol=symbol, side=side, size=size, price=price, status=order.status, created_ts=order.created_ts, filled_ts=order.filled_ts, reason=order.reason)
                    except Exception:
                        pass
                    return {"status": "rejected", "order": order.__dict__, "reasons": ["qty_invalid_min_filters"]}
                # submit order with snapped qty
                ex_res = await self._binance.market_order(symbol, side, quantity=qty_use)
                avg_px = float(ex_res.get("avgPrice") or 0) or price
                # apply fill to risk engine using actual average price
                fill = await self._risk.simulate_fill(symbol, avg_px, qty_use if side == "buy" else -qty_use)
                order.price = avg_px
                order.size = qty_use
                order.status = "filled"
                order.filled_ts = time.time()
                order.reason = (order.reason or "") + " [binance]"
            except Exception as e:
                # Do not simulate a fill on hard exchange error; return rejected so user can adjust
                order.status = "rejected"
                cls = getattr(e, "__class__", type(e)).__name__
                msg = getattr(e, "msg", None) or str(e)
                order.reason = (order.reason or "") + f" [binance_error:{cls}] {msg}"
                self._orders.append(order)
                try:
                    await self._repo.insert_order(symbol=symbol, side=side, size=size, price=price, status=order.status, created_ts=order.created_ts, filled_ts=order.filled_ts, reason=order.reason)
                except Exception:
                    pass
                return {"status": "rejected", "order": order.__dict__, "reasons": ["exchange_error"]}
        else:
            # simulation
            fill = await self._risk.simulate_fill(symbol, price, size_delta)
            order.status = "filled"
            order.filled_ts = time.time()
        self._orders.append(order)
        # Persist filled order
        try:
            await self._repo.insert_order(
                symbol=symbol,
                side=side,
                size=size,
                price=price,
                status=order.status,
                created_ts=order.created_ts,
                filled_ts=order.filled_ts,
                reason=order.reason,
            )
        except Exception:
            pass
        return {"status": "filled", "order": order.__dict__, "position": fill}

    async def delete_order(self, order_id: int) -> bool:
        """Delete order from in-memory cache and persistent store.

        Returns True if deleted from either location (DB authoritative)."""
        # Remove from memory first
        removed = False
        try:
            for i, o in enumerate(self._orders):
                if o.id == order_id:
                    del self._orders[i]
                    removed = True
                    break
        except Exception:
            pass
        # Delete from DB
        try:
            ok = await self._repo.delete_order(int(order_id))
            return bool(ok or removed)
        except Exception:
            return removed

_TRADING_SINGLETON: TradingService | None = None

def get_trading_service(risk: RiskEngine) -> TradingService:
    global _TRADING_SINGLETON
    if _TRADING_SINGLETON is None:
        _TRADING_SINGLETON = TradingService(risk)
        # Initialize next_id from DB so IDs continue monotonically across restarts
        import asyncio
        try:
            max_id = asyncio.get_event_loop().run_until_complete(_TRADING_SINGLETON._repo.get_max_id())  # type: ignore
            _TRADING_SINGLETON._next_id = int(max_id) + 1
        except Exception:
            pass
    return _TRADING_SINGLETON