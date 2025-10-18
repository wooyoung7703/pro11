from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AutopilotMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class AutopilotStrategyMeta(BaseModel):
    name: str = Field(default="Autopilot")
    version: str = Field(default="0.0.1")
    mode: AutopilotMode = Field(default=AutopilotMode.PAPER)
    enabled: bool = Field(default=False)
    last_heartbeat: float = Field(default=0.0, description="Unix timestamp")
    threshold: Optional[float] = None


class AutopilotPosition(BaseModel):
    symbol: str
    size: float
    avg_price: float
    notional: float
    unrealized_pnl: float
    realized_pnl: float = 0.0
    status: str = "flat"
    updated_ts: float = 0.0


class AutopilotRiskSnapshot(BaseModel):
    max_drawdown: Optional[float] = None
    max_notional: Optional[float] = None
    drawdown_pct: Optional[float] = None
    utilization_pct: Optional[float] = None
    cooldown_sec: Optional[int] = None


class AutopilotSignal(BaseModel):
    kind: str
    symbol: str
    confidence: float
    reason: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)
    emitted_ts: float = 0.0


class AutopilotExitPolicy(BaseModel):
    take_profit_pct: float = 0.0
    stop_loss_pct: float = 0.0


class AutopilotState(BaseModel):
    strategy: AutopilotStrategyMeta
    position: AutopilotPosition
    active_signal: Optional[AutopilotSignal] = None
    pending_orders: List[Dict[str, Any]] = Field(default_factory=list)
    health: Dict[str, Any] = Field(default_factory=dict)
    risk: AutopilotRiskSnapshot = Field(default_factory=AutopilotRiskSnapshot)
    exit_policy: Optional[AutopilotExitPolicy] = None


class AutopilotPerformanceBucket(BaseModel):
    window: str
    pnl: float
    pnl_pct: Optional[float] = None
    win_rate: Optional[float] = None
    trades: int = 0
    max_drawdown: Optional[float] = None


class AutopilotPerformance(BaseModel):
    updated_ts: float
    buckets: List[AutopilotPerformanceBucket]
    notes: Optional[str] = None


class AutopilotEvent(BaseModel):
    type: str
    ts: float
    payload: Dict[str, Any] = Field(default_factory=dict)