from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import math

@dataclass
class Params:
    fee_mode: str = "taker"  # taker|maker
    fee_taker_bps: float = 10.0  # 10 bps = 0.1%
    fee_maker_bps: float = 2.0
    slippage_bps: float = 0.0
    base_size_units: float = 1.0
    risk_max_pos_pct: float = 100.0
    si_allow: bool = True
    si_max_legs: int = 3
    si_cooldown: int = 0  # bars
    partial_allow: bool = True
    partial_pct: float = 0.5
    trade_cooldown: int = 0  # bars between trades
    force_close_at_end: bool = True

@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass
class Event:
    ts: int
    type: str  # entry|scale_in|partial_exit|full_exit
    price: float
    qty: float
    fee: float
    pnl: float  # realized PnL for exits (0 for buys)

@dataclass
class Fill:
    ts: int
    side: str  # buy|sell
    price: float
    qty: float
    fee: float

@dataclass
class Trade:
    id: int
    entry_ts: int
    exit_ts: Optional[int] = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    qty: float = 0.0
    realized_pnl: float = 0.0
    fees: float = 0.0
    legs: int = 0

@dataclass
class Summary:
    bars: int
    return_pct: float
    max_dd_pct: float
    cum_fees: float
    net_pnl: float
    counters: Dict[str, int]

@dataclass
class BuyBundle:
    ts_start: int
    ts_end: int
    legs: int
    qty: float
    avg_price: float
    fees: float

class Engine:
    def __init__(self, params: Params):
        self.p = params

    def _fee(self, notional: float) -> float:
        bps = self.p.fee_taker_bps if self.p.fee_mode == "taker" else self.p.fee_maker_bps
        return notional * (bps / 10000.0)

    def _slip_price(self, price: float, side: str) -> float:
        adj = price * (self.p.slippage_bps / 10000.0)
        return price + adj if side == "buy" else price - adj

    def run(self, candles: List[Candle]) -> Dict[str, Any]:
        if not candles:
            return {"summary": None, "events": [], "trades": []}
        equity = 10_000.0
        peak = equity
        max_dd = 0.0
        pos_qty = 0.0
        avg_price = 0.0
        events: List[Event] = []
        trades: List[Trade] = []
        trade_id = 0
        open_trade: Optional[Trade] = None
        counters = {"buys": 0, "partials": 0, "full_exits": 0, "trades": 0}
        si_legs = 0
        last_si_bar = -math.inf

        for i, c in enumerate(candles):
            price = c.close
            want_long = i > 0 and price > candles[i-1].close

            if want_long:
                if pos_qty == 0:
                    qty = self.p.base_size_units
                    fill_price = self._slip_price(price, "buy")
                    fee = self._fee(fill_price * qty)
                    pos_qty += qty
                    avg_price = fill_price
                    events.append(Event(c.ts, "entry", fill_price, qty, fee, 0.0))
                    counters["buys"] += 1
                    trade_id += 1
                    open_trade = Trade(trade_id, c.ts, entry_price=fill_price, qty=qty, legs=1)
                    trades.append(open_trade)
                else:
                    if self.p.si_allow and (open_trade.legs if open_trade else 0) < self.p.si_max_legs and (i - last_si_bar) >= self.p.si_cooldown:
                        qty = self.p.base_size_units
                        fill_price = self._slip_price(price, "buy")
                        fee = self._fee(fill_price * qty)
                        new_notional = avg_price * pos_qty + fill_price * qty
                        pos_qty += qty
                        avg_price = new_notional / pos_qty
                        events.append(Event(c.ts, "scale_in", fill_price, qty, fee, 0.0))
                        counters["buys"] += 1
                        last_si_bar = i
                        if open_trade:
                            open_trade.qty = pos_qty
                            open_trade.legs += 1
                            open_trade.fees += fee
            else:
                if pos_qty > 0:
                    # partial then full
                    if self.p.partial_allow and pos_qty > self.p.base_size_units:
                        qty = max(self.p.base_size_units, round(pos_qty * self.p.partial_pct, 8))
                        if qty < pos_qty:
                            fill_price = self._slip_price(price, "sell")
                            fee = self._fee(fill_price * qty)
                            realized = (fill_price - avg_price) * qty - fee
                            pos_qty -= qty
                            events.append(Event(c.ts, "partial_exit", fill_price, qty, fee, realized))
                            counters["partials"] += 1
                            if open_trade:
                                open_trade.realized_pnl += realized
                                open_trade.fees += fee
                    if pos_qty > 0:
                        qty = pos_qty
                        fill_price = self._slip_price(price, "sell")
                        fee = self._fee(fill_price * qty)
                        realized = (fill_price - avg_price) * qty - fee
                        pos_qty = 0.0
                        events.append(Event(c.ts, "full_exit", fill_price, qty, fee, realized))
                        counters["full_exits"] += 1
                        counters["trades"] += 1
                        if open_trade:
                            open_trade.exit_ts = c.ts
                            open_trade.exit_price = fill_price
                            open_trade.realized_pnl += realized
                            open_trade.fees += fee

            # equity and drawdown update
            unrealized = (price - avg_price) * pos_qty if pos_qty > 0 else 0.0
            realized = sum(e.pnl for e in events)
            equity = 10_000.0 + realized + unrealized
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)

        if self.p.force_close_at_end and pos_qty > 0:
            c = candles[-1]
            price = c.close
            qty = pos_qty
            fill_price = self._slip_price(price, "sell")
            fee = self._fee(fill_price * qty)
            realized = (fill_price - avg_price) * qty - fee
            pos_qty = 0.0
            events.append(Event(c.ts, "full_exit", fill_price, qty, fee, realized))
            counters["full_exits"] += 1
            counters["trades"] += 1
            if open_trade:
                open_trade.exit_ts = c.ts
                open_trade.exit_price = fill_price
                open_trade.realized_pnl += realized
                open_trade.fees += fee

        # Now compute summary and buy bundles inside run()
        cum_fees = sum(e.fee for e in events)
        realized_total = sum(e.pnl for e in events)
        net_pnl = realized_total
        ret = (equity - 10_000.0) / 10_000.0 * 100.0

        # Group contiguous buys (entry/scale_in) until any exit
        buy_bundles: List[BuyBundle] = []
        legs = 0
        qty_acc = 0.0
        notional_acc = 0.0
        fee_acc = 0.0
        ts_start: Optional[int] = None
        ts_end: Optional[int] = None

        def flush_bundle():
            nonlocal legs, qty_acc, notional_acc, fee_acc, ts_start, ts_end
            if legs > 0 and qty_acc > 0 and ts_start is not None and ts_end is not None:
                avg = notional_acc / qty_acc if qty_acc else 0.0
                buy_bundles.append(BuyBundle(ts_start, ts_end, legs, qty_acc, avg, fee_acc))
            legs = 0
            qty_acc = 0.0
            notional_acc = 0.0
            fee_acc = 0.0
            ts_start = None
            ts_end = None

        for ev in events:
            if ev.type in ("entry", "scale_in"):
                legs += 1
                qty_acc += ev.qty
                notional_acc += ev.price * ev.qty
                fee_acc += ev.fee
                if ts_start is None:
                    ts_start = ev.ts
                ts_end = ev.ts
            elif ev.type in ("partial_exit", "full_exit"):
                flush_bundle()
        # flush if window ended with buys and no exit
        flush_bundle()

        summary = Summary(
            bars=len(candles),
            return_pct=ret,
            max_dd_pct=max_dd * 100.0,
            cum_fees=cum_fees,
            net_pnl=net_pnl,
            counters=counters,
        )
        return {
            "summary": summary.__dict__,
            "events": [e.__dict__ for e in events],
            "trades": [t.__dict__ for t in trades],
            "buy_bundles": [b.__dict__ for b in buy_bundles],
        }

    def run_replay(self, candles: List[Candle], fills: List[Fill]) -> Dict[str, Any]:
        # Replay real fills into events; classify buys into entry/scale_in by position, sells into partial/full by remaining qty
        if not candles:
            return {"summary": None, "events": [], "trades": []}
        fills_sorted = sorted(fills, key=lambda f: f.ts)
        price_by_ts = {c.ts: c.close for c in candles}

        events: List[Event] = []
        trades: List[Trade] = []
        counters = {"buys": 0, "partials": 0, "full_exits": 0, "trades": 0}
        equity = 10_000.0
        peak = equity
        max_dd = 0.0

        pos_qty = 0.0
        avg_price = 0.0
        trade_id = 0
        open_trade: Optional[Trade] = None

        for f in fills_sorted:
            if f.side == "buy":
                # entry vs scale-in
                if pos_qty == 0.0:
                    trade_id += 1
                    open_trade = Trade(trade_id, f.ts, entry_price=f.price, qty=f.qty, legs=1)
                    trades.append(open_trade)
                    avg_price = f.price
                else:
                    # update avg price with new buy
                    new_notional = avg_price * pos_qty + f.price * f.qty
                    if pos_qty + f.qty > 0:
                        avg_price = new_notional / (pos_qty + f.qty)
                    if open_trade:
                        open_trade.legs += 1
                        open_trade.qty = pos_qty + f.qty
                        open_trade.fees += f.fee
                pos_qty += f.qty
                events.append(Event(f.ts, "entry" if (open_trade and open_trade.legs == 1) else "scale_in", f.price, f.qty, f.fee, 0.0))
                counters["buys"] += 1
            else:
                # sell: partial or full
                qty = f.qty
                realized = (f.price - avg_price) * qty - f.fee
                pos_qty -= qty
                if pos_qty > 0:
                    events.append(Event(f.ts, "partial_exit", f.price, qty, f.fee, realized))
                    counters["partials"] += 1
                    if open_trade:
                        open_trade.realized_pnl += realized
                        open_trade.fees += f.fee
                else:
                    events.append(Event(f.ts, "full_exit", f.price, qty, f.fee, realized))
                    counters["full_exits"] += 1
                    counters["trades"] += 1
                    if open_trade:
                        open_trade.exit_ts = f.ts
                        open_trade.exit_price = f.price
                        open_trade.realized_pnl += realized
                        open_trade.fees += f.fee

            # mark-to-market at nearest or same-ts candle if available
            ts = f.ts
            px = price_by_ts.get(ts, None)
            if px is None:
                # fallback: use last candle before ts
                prevs = [c for c in candles if c.ts <= ts]
                if prevs:
                    px = prevs[-1].close
            unreal = (px - avg_price) * pos_qty if (px is not None and pos_qty > 0) else 0.0
            realized_total = sum(e.pnl for e in events)
            equity = 10_000.0 + realized_total + unreal
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak)

        # Optionally force close at end using last candle
        if self.p.force_close_at_end and pos_qty > 0:
            last_c = candles[-1]
            qty = pos_qty
            fee = self._fee(last_c.close * qty)
            realized = (last_c.close - avg_price) * qty - fee
            pos_qty = 0.0
            events.append(Event(last_c.ts, "full_exit", last_c.close, qty, fee, realized))
            counters["full_exits"] += 1
            counters["trades"] += 1
            if open_trade:
                open_trade.exit_ts = last_c.ts
                open_trade.exit_price = last_c.close
                open_trade.realized_pnl += realized
                open_trade.fees += fee

        # Summary + bundles
        cum_fees = sum(e.fee for e in events)
        realized_total = sum(e.pnl for e in events)
        net_pnl = realized_total
        ret = (equity - 10_000.0) / 10_000.0 * 100.0

        # Bundles from events
        buy_bundles: List[BuyBundle] = []
        legs = 0
        qty_acc = 0.0
        notional_acc = 0.0
        fee_acc = 0.0
        ts_start: Optional[int] = None
        ts_end: Optional[int] = None

        def flush_bundle():
            nonlocal legs, qty_acc, notional_acc, fee_acc, ts_start, ts_end
            if legs > 0 and qty_acc > 0 and ts_start is not None and ts_end is not None:
                avg = notional_acc / qty_acc
                buy_bundles.append(BuyBundle(ts_start, ts_end, legs, qty_acc, avg, fee_acc))
            legs = 0
            qty_acc = 0.0
            notional_acc = 0.0
            fee_acc = 0.0
            ts_start = None
            ts_end = None

        for ev in events:
            if ev.type in ("entry", "scale_in"):
                legs += 1
                qty_acc += ev.qty
                notional_acc += ev.price * ev.qty
                fee_acc += ev.fee
                ts_start = ev.ts if ts_start is None else ts_start
                ts_end = ev.ts
            elif ev.type in ("partial_exit", "full_exit"):
                flush_bundle()
        flush_bundle()

        summary = Summary(
            bars=len(candles),
            return_pct=ret,
            max_dd_pct=max_dd * 100.0,
            cum_fees=cum_fees,
            net_pnl=net_pnl,
            counters=counters,
        )
        return {
            "summary": summary.__dict__,
            "events": [e.__dict__ for e in events],
            "trades": [t.__dict__ for t in trades],
            "buy_bundles": [b.__dict__ for b in buy_bundles],
        }
