export type AutopilotMode = 'paper' | 'live';

export interface AutopilotStrategyMeta {
  name: string;
  version: string;
  mode: AutopilotMode;
  enabled: boolean;
  last_heartbeat: number;
  threshold?: number | null;
}

export interface AutopilotPosition {
  symbol: string;
  size: number;
  avg_price: number;
  notional: number;
  unrealized_pnl: number;
  realized_pnl: number;
  status: string;
  updated_ts: number;
}

export interface AutopilotRiskSnapshot {
  max_drawdown?: number | null;
  max_notional?: number | null;
  drawdown_pct?: number | null;
  utilization_pct?: number | null;
  cooldown_sec?: number | null;
}

export interface AutopilotSignal {
  kind: string;
  symbol: string;
  confidence: number;
  reason?: string | null;
  extra?: Record<string, unknown>;
  emitted_ts: number;
}

export interface AutopilotExitPolicy {
  take_profit_pct: number;
  stop_loss_pct: number;
}

export interface AutopilotState {
  strategy: AutopilotStrategyMeta;
  position: AutopilotPosition;
  active_signal?: AutopilotSignal | null;
  pending_orders: Record<string, unknown>[];
  health: Record<string, unknown>;
  risk: AutopilotRiskSnapshot;
  exit_policy?: AutopilotExitPolicy | null;
}

export interface AutopilotPerformanceBucket {
  window: string;
  pnl: number;
  pnl_pct?: number | null;
  win_rate?: number | null;
  trades: number;
  max_drawdown?: number | null;
}

export interface AutopilotPerformance {
  updated_ts: number;
  buckets: AutopilotPerformanceBucket[];
  notes?: string | null;
}

export interface AutopilotEvent {
  type: string;
  ts: number;
  payload: Record<string, unknown>;
}
