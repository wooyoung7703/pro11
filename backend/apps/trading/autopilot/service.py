from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from .models import (
    AutopilotEvent,
    AutopilotExitPolicy,
    AutopilotMode,
    AutopilotPerformance,
    AutopilotPerformanceBucket,
    AutopilotPosition,
    AutopilotRiskSnapshot,
    AutopilotSignal,
    AutopilotState,
    AutopilotStrategyMeta,
)
from backend.apps.trading.repository.trading_repository import TradingRepository
from backend.apps.trading.repository.autopilot_repository import AutopilotRepository
from backend.apps.risk.service.risk_engine import RiskEngine
from backend.common.config.base_config import load_config


class AutopilotService:
    """Lightweight in-memory autopilot facade until real strategy wiring is added."""

    def __init__(
        self,
        *,
        symbol: str,
        risk_engine: Optional[RiskEngine] = None,
        mode: AutopilotMode = AutopilotMode.PAPER,
    ) -> None:
        now = time.time()
        self._risk = risk_engine
        self._symbol = symbol.upper()
        self._mode = mode
        self._trading_repo = TradingRepository()
        self._cfg = load_config()
        self._performance_windows: Tuple[Tuple[str, float], ...] = (
            ("24h", 24 * 3600.0),
            ("7d", 7 * 24 * 3600.0),
            ("30d", 30 * 24 * 3600.0),
        )
        self._trade_history_limit = 2000
        risk_snapshot = AutopilotRiskSnapshot(
            max_drawdown=float(getattr(risk_engine.limits, "max_drawdown", 0.2)) if risk_engine else 0.2,
            max_notional=float(getattr(risk_engine.limits, "max_notional", 50_000.0)) if risk_engine else 50_000.0,
            drawdown_pct=0.0,
            utilization_pct=0.0,
            cooldown_sec=60,
        )
        self._state = AutopilotState(
            strategy=AutopilotStrategyMeta(
                name="Bottom Hunter",
                version="0.0.0",
                mode=mode,
                enabled=False,
                last_heartbeat=now,
            ),
            position=AutopilotPosition(
                symbol=self._symbol,
                size=0.0,
                avg_price=0.0,
                notional=0.0,
                unrealized_pnl=0.0,
                status="flat",
                updated_ts=now,
            ),
            active_signal=None,
            pending_orders=[],
            health={"queue_depth": 0, "worker": "idle"},
            risk=risk_snapshot,
            exit_policy=self._build_exit_policy(),
        )
        self._performance = AutopilotPerformance(
            updated_ts=now,
            buckets=[
                AutopilotPerformanceBucket(window=label, pnl=0.0, pnl_pct=None, trades=0, win_rate=None, max_drawdown=None)
                for label, _ in self._performance_windows
            ],
        )
        self._event_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        self._auto_repo = AutopilotRepository()
        self._last_snapshot_ts = 0.0

    async def get_state(self) -> AutopilotState:
        self._state.strategy.mode = self._mode
        self._state.strategy.last_heartbeat = time.time()
        self._refresh_position_snapshot()
        self._state.exit_policy = self._build_exit_policy()
        # Best-effort snapshot (throttled to ~5s)
        now = time.time()
        if (now - self._last_snapshot_ts) >= 5.0:
            try:
                await self._auto_repo.insert_state_snapshot(
                    ts=now,
                    strategy=self._state.strategy.model_dump(),
                    position=self._state.position.model_dump(),
                    risk=self._state.risk.model_dump() if self._state.risk else {},
                    exit_policy=self._state.exit_policy.model_dump() if self._state.exit_policy else None,
                    health=self._state.health or {},
                )
                self._last_snapshot_ts = now
            except Exception:
                pass
        return self._state

    async def get_performance(self) -> AutopilotPerformance:
        now = time.time()
        try:
            trades = await self._load_completed_trades()
        except Exception:
            empty_buckets = [
                AutopilotPerformanceBucket(window=label, pnl=0.0, pnl_pct=None, win_rate=None, trades=0, max_drawdown=None)
                for label, _ in self._performance_windows
            ]
            self._performance = AutopilotPerformance(updated_ts=now, buckets=empty_buckets, notes="performance_error")
            return self._performance
        baseline = self._starting_equity()
        buckets: List[AutopilotPerformanceBucket] = []
        for label, window_seconds in self._performance_windows:
            buckets.append(
                self._compute_bucket_metrics(
                    label=label,
                    trades=trades,
                    window_seconds=window_seconds,
                    baseline=baseline,
                    now=now,
                )
            )
        notes = None if trades else "no_trades_yet"
        self._performance = AutopilotPerformance(updated_ts=now, buckets=buckets, notes=notes)
        return self._performance

    async def activate_signal(self, signal: AutopilotSignal) -> None:
        self._state.active_signal = signal
        self._state.strategy.enabled = True
        self._state.strategy.last_heartbeat = signal.emitted_ts or time.time()
        self._refresh_position_snapshot()
        await self._enqueue_event(
            "signal",
            {"signal": signal.model_dump()},
            ts=self._state.strategy.last_heartbeat,
        )

    async def clear_signal(self, reason: Optional[str] = None) -> None:
        self._state.active_signal = None
        self._state.strategy.enabled = False
        self._state.strategy.last_heartbeat = time.time()
        payload: Dict[str, Any] = {}
        if reason:
            payload["reason"] = reason
        await self._enqueue_event(
            "signal_clear",
            payload,
            ts=self._state.strategy.last_heartbeat,
        )
        self._refresh_position_snapshot()

    async def iter_events(self) -> AsyncIterator[str]:
        """Yield newline-delimited JSON events for SSE/WebSocket fallback."""

        while True:
            try:
                raw = await asyncio.wait_for(self._event_queue.get(), timeout=5.0)
                yield raw
            except asyncio.TimeoutError:
                heartbeat_ts = time.time()
                self._state.strategy.last_heartbeat = heartbeat_ts
                self._refresh_position_snapshot()
                evt = AutopilotEvent(
                    type="heartbeat",
                    ts=heartbeat_ts,
                    payload={
                        "enabled": self._state.strategy.enabled,
                        "position_size": self._state.position.size,
                        "utilization_pct": self._state.risk.utilization_pct,
                    },
                )
                yield json.dumps(evt.model_dump())

    async def _enqueue_event(self, event_type: str, payload: Dict[str, Any], *, ts: Optional[float] = None) -> None:
        data = self._serialize_event(event_type, payload, ts=ts)
        try:
            self._event_queue.put_nowait(data)
        except asyncio.QueueFull:
            try:
                _ = self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            await self._event_queue.put(data)
        # Persist event (best-effort)
        try:
            _ts = ts or time.time()
            await self._auto_repo.insert_event(ts=_ts, type=event_type, payload=payload)
        except Exception:
            pass

    def _serialize_event(self, event_type: str, payload: Dict[str, Any], *, ts: Optional[float] = None) -> str:
        evt = AutopilotEvent(type=event_type, ts=ts or time.time(), payload=payload)
        return json.dumps(evt.model_dump())

    def _resolve_fee_rate(self) -> float:
        if not self._risk:
            return 0.0
        fee_mode = getattr(self._risk, "_fee_mode", None)
        if fee_mode is None:
            with contextlib.suppress(Exception):
                self._risk.evaluate_order(self._symbol, 0.0, 0.0)
        fee_mode = getattr(self._risk, "_fee_mode", "taker") or "taker"
        fee_taker = float(getattr(self._risk, "_fee_taker", 0.001) or 0.001)
        fee_maker = float(getattr(self._risk, "_fee_maker", 0.001) or 0.001)
        return fee_taker if fee_mode == "taker" else fee_maker

    def _starting_equity(self) -> Optional[float]:
        if not self._risk:
            return None
        try:
            value = float(self._risk.session.starting_equity)
            return value if value > 0 else None
        except Exception:
            return None

    async def _load_completed_trades(self) -> List[Dict[str, float]]:
        fee_rate = self._resolve_fee_rate()
        orders = await self._trading_repo.fetch_range(self._symbol, limit=self._trade_history_limit)
        if not orders:
            return []
        trades: List[Dict[str, float]] = []
        pos_size = 0.0
        avg_price = 0.0
        current_realized = 0.0
        current_fees = 0.0
        trade_start_ts: Optional[float] = None
        for order in orders:
            side = str(order.get("side") or "").lower()
            try:
                size = float(order.get("size") or 0.0)
                price = float(order.get("price") or 0.0)
            except Exception:
                continue
            ts_raw = order.get("filled_ts") or order.get("created_ts") or 0.0
            try:
                ts = float(ts_raw)
            except Exception:
                ts = 0.0
            if side not in ("buy", "sell") or size <= 0 or price <= 0 or ts <= 0:
                continue
            if side == "buy":
                if pos_size <= 0:
                    trade_start_ts = ts
                    current_realized = 0.0
                    current_fees = 0.0
                    pos_size = 0.0
                    avg_price = 0.0
                current_fees += size * price * fee_rate
                new_size = pos_size + size
                avg_price = ((avg_price * pos_size) + (price * size)) / new_size if new_size else 0.0
                pos_size = new_size
                continue
            if pos_size <= 0:
                continue
            sell_qty = min(size, pos_size)
            current_fees += sell_qty * price * fee_rate
            current_realized += (price - avg_price) * sell_qty
            pos_size -= sell_qty
            if pos_size <= 1e-9:
                net = current_realized - current_fees
                trades.append({
                    "start_ts": trade_start_ts or ts,
                    "end_ts": ts,
                    "net_pnl": net,
                    "gross_pnl": current_realized,
                    "fees": current_fees,
                })
                pos_size = 0.0
                avg_price = 0.0
                current_realized = 0.0
                current_fees = 0.0
                trade_start_ts = None
        return trades

    def _compute_bucket_metrics(
        self,
        *,
        label: str,
        trades: List[Dict[str, float]],
        window_seconds: float,
        baseline: Optional[float],
        now: float,
    ) -> AutopilotPerformanceBucket:
        if window_seconds <= 0:
            relevant = list(trades)
        else:
            cutoff = now - window_seconds
            relevant = [t for t in trades if float(t.get("end_ts") or 0.0) >= cutoff]
        relevant.sort(key=lambda t: float(t.get("end_ts") or 0.0))
        pnl = sum(float(t.get("net_pnl") or 0.0) for t in relevant)
        trade_count = len(relevant)
        wins = sum(1 for t in relevant if float(t.get("net_pnl") or 0.0) > 0)
        win_rate = (wins / trade_count) if trade_count else None
        pnl_pct = (pnl / baseline) if baseline and baseline > 0 and trade_count else None
        max_dd = self._compute_drawdown(relevant, baseline)
        return AutopilotPerformanceBucket(
            window=label,
            pnl=pnl,
            pnl_pct=pnl_pct,
            win_rate=win_rate,
            trades=trade_count,
            max_drawdown=max_dd,
        )

    def _compute_drawdown(
        self,
        trades: List[Dict[str, float]],
        baseline: Optional[float],
    ) -> Optional[float]:
        if not trades:
            return None
        equity = baseline if baseline and baseline > 0 else 0.0
        peak = equity
        max_drawdown_abs = 0.0
        for trade in trades:
            equity += float(trade.get("net_pnl") or 0.0)
            peak = max(peak, equity)
            max_drawdown_abs = max(max_drawdown_abs, peak - equity)
        if baseline and baseline > 0:
            peak_equity = max(peak, baseline)
            if peak_equity > 0:
                return max_drawdown_abs / peak_equity
            return 0.0
        return max_drawdown_abs if max_drawdown_abs > 0 else 0.0

    def _refresh_position_snapshot(self) -> None:
        if not self._risk:
            return
        position_state = self._state.position
        session = self._risk.session
        limits = self._risk.limits
        pos = self._risk.positions.get(self._symbol)
        now = time.time()
        if pos:
            position_state.size = float(pos.size)
            position_state.avg_price = float(pos.entry_price)
            position_state.notional = abs(position_state.size) * position_state.avg_price
            position_state.unrealized_pnl = float(getattr(pos, "unrealized_pnl", 0.0) or 0.0)
            position_state.status = "long" if position_state.size > 0 else ("short" if position_state.size < 0 else "flat")
        else:
            position_state.size = 0.0
            position_state.avg_price = 0.0
            position_state.notional = 0.0
            position_state.unrealized_pnl = 0.0
            position_state.status = "flat"
        position_state.realized_pnl = float(getattr(session, "cumulative_pnl", 0.0) or 0.0)
        position_state.updated_ts = now
        self._state.position = position_state

        cooldown = self._state.risk.cooldown_sec if self._state.risk else 60
        drawdown_pct = 0.0
        if getattr(session, "peak_equity", 0.0):
            try:
                drawdown_pct = max(0.0, (session.peak_equity - session.current_equity) / session.peak_equity)
            except Exception:
                drawdown_pct = 0.0
        utilization = 0.0
        if getattr(limits, "max_notional", 0.0):
            try:
                utilization = min(1.0, position_state.notional / limits.max_notional)
            except Exception:
                utilization = 0.0
        self._state.risk = AutopilotRiskSnapshot(
            max_drawdown=float(getattr(limits, "max_drawdown", 0.0) or 0.0),
            max_notional=float(getattr(limits, "max_notional", 0.0) or 0.0),
            drawdown_pct=drawdown_pct,
            utilization_pct=utilization,
            cooldown_sec=int(cooldown) if cooldown is not None else None,
        )

    def is_paper_mode(self) -> bool:
        return self._mode == AutopilotMode.PAPER

    @property
    def strategy_mode(self) -> AutopilotMode:
        return self._mode

    def _build_exit_policy(self) -> AutopilotExitPolicy:
        take_profit = 0.0
        stop_loss = 0.0
        try:
            take_profit = float(getattr(self._cfg, "low_buy_take_profit_pct", 0.0) or 0.0)
        except Exception:
            take_profit = 0.0
        try:
            stop_loss = float(getattr(self._cfg, "low_buy_stop_loss_pct", 0.0) or 0.0)
        except Exception:
            stop_loss = 0.0
        return AutopilotExitPolicy(take_profit_pct=take_profit, stop_loss_pct=stop_loss)
