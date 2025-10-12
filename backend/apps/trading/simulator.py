from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
import math
import random


@dataclass
class SimConfig:
    base_units: float = 1.0
    fee_rate: float = 0.001  # taker as default
    threshold: float = 0.5
    cooldown_sec: float = 0.0
    # scale-in
    allow_scale_in: bool = True
    si_ratio: float = 0.5
    si_max_legs: int = 3
    si_min_price_move: float = 0.0  # relative drop from anchor/entry
    si_cooldown_sec: float = 0.0
    # exit policy
    exit_require_net_profit: bool = True
    exit_slice_seconds: float = 0.0  # unused in sim (instant fill)


def _fees(amount: float, fee_rate: float) -> float:
    return max(0.0, amount) * max(0.0, fee_rate)


def generate_mock_series(
    n: int = 300,
    start_price: float = 1.0,
    drift: float = 0.0,
    vol: float = 0.01,
    prob_noise: float = 0.05,
    seed: Optional[int] = 42,
) -> Tuple[List[float], List[float]]:
    """
    Generate a simple geometric random walk price series and a probability series
    loosely correlated with recent momentum.
    """
    rnd = random.Random(seed)
    prices: List[float] = []
    probs: List[float] = []
    px = float(start_price)
    prev = px
    for i in range(n):
        # log-return with drift + noise
        ret = drift + vol * rnd.gauss(0.0, 1.0)
        px = max(1e-9, px * math.exp(ret))
        prices.append(px)
        # simple momentum proxy for probability
        mom = (px - prev) / prev if prev > 0 else 0.0
        p = 0.5 + 0.5 * math.tanh(4.0 * mom) + (prob_noise * rnd.uniform(-1.0, 1.0))
        probs.append(min(1.0, max(0.0, p)))
        prev = px
    return prices, probs


def simulate_trading(
    prices: List[float],
    probs: List[float],
    cfg: SimConfig,
) -> Dict[str, Any]:
    n = min(len(prices), len(probs))
    if n <= 1:
        return {"status": "error", "error": "insufficient_points"}

    # State
    cash = 10_000.0
    units = 0.0
    avg_entry = 0.0
    last_trade_idx = -10_000
    last_si_idx = -10_000
    legs_used = 0
    anchor_price: Optional[float] = None

    # Tracking
    equity_series: List[float] = []
    peak = cash
    max_dd = 0.0
    fees_cum = 0.0
    events: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    validations: List[str] = []

    trade_entry_idx = -1
    trade_bought_qty = 0.0
    trade_buy_fees = 0.0
    trade_buy_count = 0
    trade_realized = 0.0

    def close_trade(i: int):
        nonlocal cash, units, avg_entry, trade_entry_idx, trade_bought_qty, trade_buy_fees, trade_buy_count, trade_realized, fees_cum, legs_used, last_si_idx, anchor_price
        if units <= 0 or trade_entry_idx < 0:
            return
        px = prices[i]
        gross = units * px
        fee = _fees(gross, cfg.fee_rate)
        net = gross - fee
        # net-profit-only guard
        if cfg.exit_require_net_profit:
            # approximate invested cost = avg_entry * qty + buy fees
            invested = (avg_entry * units) + trade_buy_fees
            pnl = (trade_realized + net) - invested
            if pnl < 0:
                validations.append("exit_blocked_net_profit_guard")
                return
        cash += net
        fees_cum += fee
        # record trade
        invested = (avg_entry * trade_bought_qty) + trade_buy_fees
        pnl = (trade_realized + net) - invested
        roi = (pnl / invested) if invested > 0 else 0.0
        trades.append({
            "entry_idx": trade_entry_idx,
            "exit_idx": i,
            "buy_count": trade_buy_count,
            "qty": trade_bought_qty,
            "avg_entry": avg_entry,
            "exit_price": px,
            "fees": (fees_cum),
            "invested": invested,
            "pnl": pnl,
            "roi": roi,
        })
        events.append({"i": i, "kind": "exit", "side": "sell", "qty": units, "price": px, "fee": fee})
        # reset position
        units = 0.0
        avg_entry = 0.0
        legs_used = 0
        last_si_idx = i
        anchor_price = None
        trade_entry_idx = -1
        trade_bought_qty = 0.0
        trade_buy_fees = 0.0
        trade_buy_count = 0
        trade_realized = 0.0

    for i in range(n):
        px = prices[i]
        p = probs[i]
        # Entry
        if units <= 0:
            cool_ok = (i - last_trade_idx) >= int(max(0.0, cfg.cooldown_sec))
            if p >= cfg.threshold and cool_ok and cfg.base_units > 0:
                qty = cfg.base_units
                cost = qty * px
                fee = _fees(cost, cfg.fee_rate)
                total = cost + fee
                if total > cash:
                    validations.append("entry_insufficient_cash")
                else:
                    cash -= total
                    fees_cum += fee
                    units += qty
                    avg_entry = px
                    trade_entry_idx = i
                    trade_bought_qty = qty
                    trade_buy_fees = fee
                    trade_buy_count = 1
                    last_trade_idx = i
                    legs_used = 0
                    last_si_idx = i
                    anchor_price = px
                    events.append({"i": i, "kind": "buy", "side": "buy", "qty": qty, "price": px, "fee": fee})
        else:
            # Exit condition
            if p < cfg.threshold:
                close_trade(i)
            else:
                # Consider scale-in
                if cfg.allow_scale_in and legs_used < cfg.si_max_legs:
                    si_cd_ok = (i - last_si_idx) >= int(max(0.0, cfg.si_cooldown_sec)) or (legs_used == 0)
                    ref = anchor_price if (anchor_price and anchor_price > 0) else avg_entry
                    gate_ok = True
                    if cfg.si_min_price_move > 0 and ref and ref > 0:
                        drop = (ref - px) / ref
                        gate_ok = drop >= cfg.si_min_price_move
                    if si_cd_ok and gate_ok:
                        add_q = max(0.0, cfg.base_units * cfg.si_ratio)
                        if add_q > 0:
                            cost = add_q * px
                            fee = _fees(cost, cfg.fee_rate)
                            total = cost + fee
                            if total <= cash:
                                cash -= total
                                fees_cum += fee
                                new_units = units + add_q
                                avg_entry = ((avg_entry * units) + (px * add_q)) / new_units
                                units = new_units
                                trade_bought_qty += add_q
                                trade_buy_fees += fee
                                trade_buy_count += 1
                                legs_used += 1
                                last_si_idx = i
                                anchor_price = px
                                events.append({"i": i, "kind": "scale-in", "side": "buy", "qty": add_q, "price": px, "fee": fee})
                            else:
                                validations.append("scalein_insufficient_cash")

        # MTM equity
        eq = cash + units * px
        equity_series.append(eq)
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # close at end if profitable and allowed
    if units > 0:
        last_idx = n - 1
        # Try close (guard enforced inside)
        close_trade(last_idx)

    result = {
        "status": "ok",
        "events": events,
        "trades": trades,
        "equity": equity_series,
        "fees": fees_cum,
        "returnPct": (equity_series[-1] - 10_000.0) / 10_000.0 if equity_series else 0.0,
        "maxDrawdown": max_dd,
        "validations": validations,
    }
    result["ok"] = len([v for v in validations if v.startswith("exit_blocked_") is False]) == len(validations)
    return result
