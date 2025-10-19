from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List
import asyncio
import math
import time
import os

@dataclass
class PositionState:
    symbol: str
    size: float = 0.0  # positive long, negative short
    entry_price: float = 0.0
    unrealized_pnl: float = 0.0

@dataclass
class RiskLimits:
    max_notional: float
    max_daily_loss: float
    max_drawdown: float
    atr_multiple: float

@dataclass
class RiskSessionState:
    starting_equity: float
    peak_equity: float
    current_equity: float
    cumulative_pnl: float = 0.0
    last_reset_ts: float = field(default_factory=time.time)

class RiskEngine:
    def __init__(self, limits: RiskLimits, session_key: str = "default_session", repository_factory=None):
        self.limits = limits
        self.session_key = session_key
        self.positions: Dict[str, PositionState] = {}
        # Default starting equity can be overridden via env RISK_STARTING_EQUITY
        try:
            _v = os.getenv("RISK_STARTING_EQUITY", "10000")
            if isinstance(_v, str) and '#' in _v:
                _v = _v.split('#', 1)[0].strip()
            _start_eq = float(_v)
        except Exception:
            _start_eq = 10000.0
        self.session = RiskSessionState(starting_equity=_start_eq, peak_equity=_start_eq, current_equity=_start_eq)
        self._repo_factory = repository_factory  # lazy to avoid circular import at module load
        # Fee configuration (loaded lazily to avoid config import at module import time)
        self._fee_mode: str | None = None
        self._fee_taker: float | None = None
        self._fee_maker: float | None = None

    async def load(self):
        if self._repo_factory is None:
            from backend.apps.risk.repository.risk_repository import RiskRepository  # inline import
            repo = RiskRepository(self.session_key)
        else:
            repo = self._repo_factory(self.session_key)
        state = await repo.load_state()
        if state:
            self.session.starting_equity = float(state["starting_equity"])
            self.session.peak_equity = float(state["peak_equity"])
            self.session.current_equity = float(state["current_equity"])
            self.session.cumulative_pnl = float(state["cumulative_pnl"])
            self.session.last_reset_ts = float(state["last_reset_ts"])
        rows = await repo.load_positions()
        for r in rows:
            self.positions[r["symbol"]] = PositionState(symbol=r["symbol"], size=float(r["size"]), entry_price=float(r["entry_price"]))
        self._repo = repo  # cache for later saves

    async def _persist(self):
        # If repository not ready (e.g., DB was down at startup), attempt lazy init
        if not hasattr(self, "_repo") or self._repo is None:
            try:
                from backend.apps.risk.repository.risk_repository import RiskRepository  # inline to avoid cycle
                self._repo = RiskRepository(self.session_key)
            except Exception:
                return
        repo = self._repo
        await repo.save_state(
            starting_equity=self.session.starting_equity,
            peak_equity=self.session.peak_equity,
            current_equity=self.session.current_equity,
            cumulative_pnl=self.session.cumulative_pnl,
            last_reset_ts=int(self.session.last_reset_ts),
        )
        # Save each position
        for p in self.positions.values():
            await repo.save_position(p.symbol, p.size, p.entry_price)

    async def reset_equity(self, starting_equity: float, *, reset_pnl: bool = True, touch_peak: bool = True) -> None:
        """Reset session equity baseline.

        - starting_equity: new baseline starting equity
        - reset_pnl: when True, cumulative_pnl is set to 0
        - touch_peak: when True, peak/current are aligned to starting
        """
        try:
            starting_equity = float(starting_equity)
        except Exception:
            raise ValueError("invalid_starting_equity")
        if not math.isfinite(starting_equity) or starting_equity <= 0:
            raise ValueError("invalid_starting_equity")
        self.session.starting_equity = starting_equity
        if touch_peak:
            self.session.peak_equity = starting_equity
            self.session.current_equity = starting_equity
        if reset_pnl:
            self.session.cumulative_pnl = 0.0
        self.session.last_reset_ts = time.time()
        await self._persist()

    def _calc_atr_guard(self, atr: float | None, current_price: float, intended_size: float) -> tuple[bool, str | None]:
        if atr is None or atr <= 0:
            return True, None
        notional = abs(intended_size) * current_price
        # crude guard: disallow if notional / atr > limit ratio (atr_multiple * 1000 heuristic)
        if notional / atr > self.limits.atr_multiple * 1000:
            return False, "atr_exceeded"
        return True, None

    def _calc_drawdown_guard(self) -> tuple[bool, str | None]:
        dd = (self.session.peak_equity - self.session.current_equity) / self.session.peak_equity
        if dd > self.limits.max_drawdown:
            return False, "drawdown_limit"
        return True, None

    def _calc_daily_loss_guard(self) -> tuple[bool, str | None]:
        if self.session.current_equity < self.session.starting_equity - self.limits.max_daily_loss:
            return False, "daily_loss_limit"
        return True, None

    def _calc_notional_guard(self, current_price: float, intended_size: float) -> tuple[bool, str | None]:
        notional = abs(intended_size * current_price)
        if notional > self.limits.max_notional:
            return False, "notional_limit"
        return True, None

    def evaluate_order(self, symbol: str, current_price: float, intended_size: float, atr: float | None = None) -> Dict[str, Any]:
        checks: List[str] = []
        allowed = True
        reasons: List[str] = []

        for fn in [
            lambda: self._calc_notional_guard(current_price, intended_size),
            lambda: self._calc_drawdown_guard(),
            lambda: self._calc_daily_loss_guard(),
            lambda: self._calc_atr_guard(atr, current_price, intended_size),
        ]:
            ok, reason = fn()
            checks.append(reason or "ok")
            if not ok:
                allowed = False
                reasons.append(reason or "unknown")

        return {
            "allowed": allowed,
            "reasons": reasons,
            "checks": checks,
        }

    async def simulate_fill(self, symbol: str, price: float, size_delta: float) -> Dict[str, Any]:
        # Lazy load fee config on first use
        if self._fee_mode is None:
            try:
                from backend.common.config.base_config import load_config  # inline import to avoid cycles
                cfg = load_config()
                self._fee_mode = (cfg.trading_fee_mode or "taker").lower()
                self._fee_taker = float(getattr(cfg, 'trading_fee_taker', 0.001))
                self._fee_maker = float(getattr(cfg, 'trading_fee_maker', 0.001))
            except Exception:
                self._fee_mode = "taker"; self._fee_taker = 0.001; self._fee_maker = 0.001
        pos = self.positions.get(symbol)
        if pos is None:
            pos = PositionState(symbol=symbol)
            self.positions[symbol] = pos

        fee_rate = (self._fee_taker if self._fee_mode == 'taker' else self._fee_maker) or 0.0

        size_before = pos.size

        # Case 1: No existing position -> open new position (or no-op if size_delta==0)
        if size_before == 0:
            if size_delta != 0:
                pos.entry_price = price
                open_fee = abs(size_delta) * price * fee_rate
                self.session.current_equity -= open_fee
                pos.size = size_delta
            # else: keep as is
            await self._persist()
            return {"symbol": symbol, "size": pos.size, "entry_price": pos.entry_price, "equity": self.session.current_equity}

        # Case 2: Same direction -> scale-in (increase position)
        if size_before * size_delta > 0:
            add_fee = abs(size_delta) * price * fee_rate
            self.session.current_equity -= add_fee
            new_size = size_before + size_delta
            # signed-size weighted average entry
            pos.entry_price = (pos.entry_price * size_before + price * size_delta) / new_size if new_size != 0 else 0.0
            pos.size = new_size
            await self._persist()
            return {"symbol": symbol, "size": pos.size, "entry_price": pos.entry_price, "equity": self.session.current_equity}

        # Case 3: Opposite direction -> partial/total close, possibly flip
        # Compute closing portion
        close_qty = min(abs(size_before), abs(size_delta))
        # Realized PnL for closing portion (direction-aware)
        direction = 1.0 if size_before > 0 else -1.0
        realized_gross = (price - pos.entry_price) * close_qty * direction
        closing_fee = close_qty * price * fee_rate
        realized_net = realized_gross - closing_fee
        self.session.current_equity += realized_net
        self.session.peak_equity = max(self.session.peak_equity, self.session.current_equity)
        self.session.cumulative_pnl += realized_net

        # New size after applying delta
        new_size = size_before + size_delta

        if new_size == 0:
            # Fully closed, no flip
            pos.size = 0.0
            pos.entry_price = 0.0
            await self._persist()
            return {"symbol": symbol, "size": pos.size, "entry_price": pos.entry_price, "equity": self.session.current_equity}

        if size_before * new_size < 0:
            # Flip occurred: open new position with the remaining over-closed quantity
            open_qty = abs(new_size)
            open_fee = open_qty * price * fee_rate
            self.session.current_equity -= open_fee
            pos.entry_price = price
            pos.size = new_size
            await self._persist()
            return {"symbol": symbol, "size": pos.size, "entry_price": pos.entry_price, "equity": self.session.current_equity}

        # Partial close, still same original direction (magnitude reduced)
        pos.size = new_size
        await self._persist()
        return {"symbol": symbol, "size": pos.size, "entry_price": pos.entry_price, "equity": self.session.current_equity}
