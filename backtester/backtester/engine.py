from dataclasses import dataclass, field
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

class Engine:
    def __init__(self, params: Params):
        self.p = params

    def _fee(self, notional: float) -> float:
        bps = self.p.fee_taker_bps if self.p.fee_mode == "taker" else self.p.fee_maker_bps
        return notional * (bps / 10000.0)

    def _slip_price(self, price: float, side: str) -> float:
        # side: buy|sell
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
        last_entry_bar = -math.inf
        last_si_bar = -math.inf

        for i, c in enumerate(candles):
            price = c.close
            # toy strategy: momentum flip on close-to-close
            if i > 0:
                prev = candles[i-1].close
                want_long = price > prev
            else:
                want_long = False

            # Entry or scale-in
            if want_long:
                if pos_qty == 0:
                    # entry
                    qty = self.p.base_size_units
                    fill_price = self._slip_price(price, "buy")
                    fee = self._fee(fill_price * qty)
                    pos_qty += qty
                    avg_price = fill_price
                    events.append(Event(c.ts, "entry", fill_price, qty, fee, 0.0))
                    counters["buys"] += 1
                    si_legs = 1
                    trade_id += 1
                    open_trade = Trade(trade_id, c.ts, entry_price=fill_price, qty=qty, legs=1)
                    trades.append(open_trade)
                    last_entry_bar = i
                else:
                    # scale-in if allowed and legs not exceeded and cooldown
                    if self.p.si_allow and si_legs < self.p.si_max_legs and (i - last_si_bar) >= self.p.si_cooldown:
                        qty = self.p.base_size_units
                        fill_price = self._slip_price(price, "buy")
                        fee = self._fee(fill_price * qty)
                        new_notional = avg_price * pos_qty + fill_price * qty
                        pos_qty += qty
                        avg_price = new_notional / pos_qty
                        events.append(Event(c.ts, "scale_in", fill_price, qty, fee, 0.0))
                        counters["buys"] += 1
                        si_legs += 1
                        last_si_bar = i
                        if open_trade:
                            open_trade.qty = pos_qty
                            open_trade.legs = si_legs
                            open_trade.fees += fee
            else:
                # Exit logic: partial then full when momentum flips down
                if pos_qty > 0:
                    qty = pos_qty
                    if self.p.partial_allow and pos_qty > self.p.base_size_units:
                        qty = pos_qty * self.p.partial_pct
                        qty = max(self.p.base_size_units, round(qty, 8))
                        if qty < pos_qty:
                            # partial
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
                        # full exit of remaining
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

            # equity update at close (mark-to-market)
            unrealized = 0.0
            if pos_qty > 0:
                unrealized = (price - avg_price) * pos_qty
            fees_cum = sum(e.fee for e in events)
            net = sum(e.pnl for e in events) + unrealized - 0.0  # pnl already net of fee; we track fees separately for reporting
            equity = 10_000.0 + net
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        # force close at end
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

            # update equity one last time
            fees_cum = sum(e.fee for e in events)
            net = sum(e.pnl for e in events)
            equity = 10_000.0 + net
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        fees_cum = sum(e.fee for e in events)
        realized = sum(e.pnl for e in events)
        net_pnl = realized  # unrealized is zero at end if forced closed; else we ignore unrealized in net_pnl
        ret = (equity - 10_000.0) / 10_000.0 * 100.0
        summary = Summary(
            bars=len(candles),
            return_pct=ret,
            max_dd_pct=max_dd * 100.0,
            cum_fees=fees_cum,
            net_pnl=net_pnl,
            counters=counters,
        )
        return {
            "summary": summary.__dict__,
            "events": [e.__dict__ for e in events],
            "trades": [t.__dict__ for t in trades],
        }
