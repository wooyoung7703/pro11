import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import http from '@/lib/http';
import type {
  AutopilotEvent,
  AutopilotPerformance,
  AutopilotPosition,
  AutopilotExitPolicy,
  AutopilotRiskSnapshot,
  AutopilotSignal,
  AutopilotState,
  AutopilotStrategyMeta,
} from '@/types/autopilot';

const MAX_LOG_SIZE = 200;

interface SignalHistoryRecord {
  id?: number;
  signal_type?: string;
  status?: string;
  price?: number;
  params?: Record<string, unknown> | null;
  extra?: Record<string, unknown> | null;
  order_side?: string | null;
  order_size?: number | null;
  order_price?: number | null;
  error?: string | null;
  created_ts?: number | null;
  executed_ts?: number | null;
}

function parseEvent(raw: string): AutopilotEvent | null {
  try {
    const parsed = JSON.parse(raw) as AutopilotEvent;
    if (!parsed || typeof parsed !== 'object') return null;
    if (typeof parsed.type !== 'string') return null;
    if (typeof parsed.ts !== 'number') return null;
    parsed.payload = parsed.payload || {};
    return parsed;
  } catch (_err) {
    return null;
  }
}

export const useAutoTraderStore = defineStore('autoTrader', () => {
  const loading = ref(false);
  const error = ref<string | null>(null);

  const strategy = ref<AutopilotStrategyMeta | null>(null);
  const position = ref<AutopilotPosition | null>(null);
  const activeSignal = ref<AutopilotSignal | null>(null);
  const pendingOrders = ref<Record<string, unknown>[]>([]);
  const health = ref<Record<string, unknown>>({});
  const risk = ref<AutopilotRiskSnapshot | null>(null);
  const performance = ref<AutopilotPerformance | null>(null);
  const events = ref<AutopilotEvent[]>([]);
  const streamConnected = ref(false);
  const exitPolicy = ref<AutopilotExitPolicy | null>(null);
  const historicalSignals = ref<AutopilotSignal[]>([]);

  let source: EventSource | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let lifecycleHooked = false;
  let historyLoaded = false;

  function resetError() {
    error.value = null;
  }

  async function fetchState() {
    resetError();
    try {
      const resp = await http.get('/api/trading/autopilot/state');
      const data = resp.data?.state as AutopilotState | undefined;
      if (!data) throw new Error('invalid_state');
      strategy.value = data.strategy;
      position.value = data.position;
      activeSignal.value = data.active_signal ?? null;
      pendingOrders.value = Array.isArray(data.pending_orders) ? data.pending_orders : [];
      health.value = data.health ?? {};
      risk.value = data.risk ?? null;
      exitPolicy.value = data.exit_policy ?? null;
    } catch (err: any) {
      error.value = err?.__friendlyMessage || err?.message || 'state_fetch_failed';
      throw err;
    }
  }

  async function fetchPerformance() {
    resetError();
    try {
      const resp = await http.get('/api/trading/autopilot/performance');
      const raw: any = resp.data;
      const data: any = (raw && typeof raw === 'object' && raw.performance != null) ? raw.performance : raw;
      if (!data || typeof data !== 'object') {
        // fail-soft: leave previous performance or set null
        performance.value = null;
        return;
      }
      const rows: any[] = Array.isArray(data.buckets) ? data.buckets
        : (Array.isArray(data.rows) ? data.rows
        : (Array.isArray(data.data) ? data.data : []));
      const normBuckets = rows.map((b: any) => ({
        window: b.window ?? b.range ?? b.name ?? '-',
        pnl: typeof b.pnl === 'number' ? b.pnl : (typeof b.profit === 'number' ? b.profit : 0),
        pnl_pct: typeof b.pnl_pct === 'number' ? b.pnl_pct : (typeof b.ret === 'number' ? b.ret : null),
        win_rate: typeof b.win_rate === 'number' ? b.win_rate : (typeof b.winrate === 'number' ? b.winrate : null),
        trades: typeof b.trades === 'number' ? b.trades : (typeof b.count === 'number' ? b.count : 0),
        max_drawdown: typeof b.max_drawdown === 'number' ? b.max_drawdown : null,
      })) as AutopilotPerformance['buckets'];
      const updatedTs = (typeof data.updated_ts === 'number') ? data.updated_ts : Math.floor(Date.now() / 1000);
      performance.value = { updated_ts: updatedTs, buckets: normBuckets, notes: data.notes ?? data.status ?? null };
    } catch (err: any) {
      error.value = err?.__friendlyMessage || err?.message || 'performance_fetch_failed';
      // keep previous performance; don't rethrow to avoid breaking UI
    }
  }

  async function fetchInitial() {
    if (loading.value) return;
    loading.value = true;
    try {
      await Promise.all([fetchState(), fetchPerformance()]);
    } finally {
      loading.value = false;
    }
  }

  function mapHistoryRecord(record: SignalHistoryRecord): AutopilotSignal | null {
    if (!record) return null;
    const extraRaw = (record.extra ?? {}) as Record<string, unknown>;
    const paramsRaw = (record.params ?? {}) as Record<string, unknown>;
    const metricsRaw = (extraRaw.metrics ?? {}) as Record<string, unknown>;
    const emittedCandidates = [record.executed_ts, record.created_ts, metricsRaw.evaluated_at];
    let emitted = 0;
    for (const candidate of emittedCandidates) {
      if (typeof candidate === 'number' && Number.isFinite(candidate)) {
        emitted = candidate;
        break;
      }
    }
    if (!emitted || !Number.isFinite(emitted)) return null;
    let symbolGuess = '';
    if (typeof metricsRaw.symbol === 'string' && metricsRaw.symbol.trim().length) {
      symbolGuess = metricsRaw.symbol;
    } else if (typeof paramsRaw.symbol === 'string' && paramsRaw.symbol.trim().length) {
      symbolGuess = paramsRaw.symbol;
    } else if (position.value?.symbol) {
      symbolGuess = position.value.symbol;
    }
    const evaluationRaw = (extraRaw.evaluation ?? {}) as Record<string, unknown>;
    if (!evaluationRaw.order_response && (record.order_side || record.order_price || record.order_size)) {
      evaluationRaw.order_response = {
        order: {
          side: record.order_side,
          price: record.order_price,
          size: record.order_size,
          created_ts: record.created_ts,
          filled_ts: record.executed_ts,
        },
      };
    }
    const enrichedExtra: Record<string, unknown> = {
      ...extraRaw,
      evaluation: evaluationRaw,
      params: paramsRaw,
      status: record.status,
      signal_id: record.id,
      created_ts: record.created_ts,
      executed_ts: record.executed_ts,
    };
    return {
      kind: typeof record.signal_type === 'string' && record.signal_type.length ? record.signal_type : 'unknown',
      symbol: symbolGuess,
      confidence: 0.5,
      reason: typeof extraRaw.reason === 'string' ? extraRaw.reason : record.status ?? undefined,
      extra: enrichedExtra,
      emitted_ts: emitted,
    } satisfies AutopilotSignal;
  }

  async function fetchSignalHistory(options?: { limit?: number; signalType?: string; force?: boolean }) {
    const limit = options?.limit ?? 150;
    const signalType = options?.signalType ?? 'ml_buy';
    const force = Boolean(options?.force);
    if (historyLoaded && !force) return;
    resetError();
    try {
      let resp = await http.get('/api/trading/signals/recent', {
        params: {
          limit,
          signal_type: signalType,
        },
      });
      const rows = resp.data?.signals as SignalHistoryRecord[] | undefined;
      let dataRows: SignalHistoryRecord[] = Array.isArray(rows) ? rows : [];
      // Fallback: if no data for ml_buy, try without filtering
      if (dataRows.length === 0 && signalType === 'ml_buy') {
        resp = await http.get('/api/trading/signals/recent', { params: { limit } });
        const rows2 = resp.data?.signals as SignalHistoryRecord[] | undefined;
        dataRows = Array.isArray(rows2) ? rows2 : [];
      }
      if (!Array.isArray(dataRows)) throw new Error('invalid_signal_history');
      const mapped = dataRows
        .map((row) => mapHistoryRecord(row))
        .filter((signal): signal is AutopilotSignal => Boolean(signal));
      // oldest first for smoother marker stacking
      historicalSignals.value = mapped.reverse();
      historyLoaded = true;
    } catch (err: any) {
      error.value = err?.__friendlyMessage || err?.message || 'signal_history_failed';
      throw err;
    }
  }

  function pushEvent(evt: AutopilotEvent) {
    events.value.push(evt);
    if (events.value.length > MAX_LOG_SIZE) {
      events.value.splice(0, events.value.length - MAX_LOG_SIZE);
    }
    if (evt.type === 'signal') {
      const payload = evt.payload as Record<string, unknown>;
      const raw = (payload as any)?.signal as AutopilotSignal | undefined;
      if (raw) {
        activeSignal.value = raw;
        if (strategy.value) {
          strategy.value.enabled = true;
          strategy.value.last_heartbeat = raw.emitted_ts;
        }
      }
    } else if (evt.type === 'signal_clear') {
      activeSignal.value = null;
      if (strategy.value) {
        strategy.value.enabled = false;
        strategy.value.last_heartbeat = evt.ts;
      }
    }
    if (evt.type === 'heartbeat' && strategy.value) {
      const enabled = evt.payload?.enabled;
      if (typeof enabled === 'boolean') strategy.value.enabled = enabled;
      const util = evt.payload?.utilization_pct;
      if (typeof util === 'number') {
        risk.value = { ...(risk.value ?? {}), utilization_pct: util };
      }
      const size = evt.payload?.position_size;
      if (typeof size === 'number' && position.value) {
        position.value.size = size;
      }
      strategy.value.last_heartbeat = evt.ts;
    }
  }

  function cleanupSource() {
    if (source) {
      source.close();
      source = null;
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    streamConnected.value = false;
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connectStream(true);
    }, 3000);
  }

  function connectStream(force = false) {
    if (source && !force) return;
    cleanupSource();
    if (typeof window === 'undefined') return;
    const es = new EventSource('/api/trading/autopilot/stream');
    source = es;
    es.onopen = () => {
      streamConnected.value = true;
    };
    es.onmessage = (evt) => {
      const parsed = parseEvent(evt.data);
      if (!parsed) return;
      pushEvent(parsed);
    };
    es.onerror = () => {
      cleanupSource();
      scheduleReconnect();
    };
    if (!lifecycleHooked) {
      window.addEventListener('beforeunload', disconnectStream);
      lifecycleHooked = true;
    }
  }

  function disconnectStream() {
    cleanupSource();
  }

  const hasPosition = computed(() => (position.value?.size ?? 0) !== 0);
  const latestEvent = computed(() => events.value.length ? events.value[events.value.length - 1] : null);

  // Local-only reset helpers (UI convenience)
  function resetPositionLocal(opts?: { alsoClearSignal?: boolean }) {
    position.value = null;
    if (opts?.alsoClearSignal) {
      activeSignal.value = null;
      // Clear markers sources so chart rebuild clears markers
      historicalSignals.value = [];
      events.value = [];
    }
  }

  // Delete server-side trading signals and clear local state so markers don't reappear after refresh
  async function clearServerSignals(options?: { signalType?: string; alsoResetPosition?: boolean }) {
    resetError();
    try {
      await http.delete('/api/trading/signals', {
        params: options?.signalType ? { signal_type: options.signalType } : undefined,
      });
    } catch (err: any) {
      error.value = err?.__friendlyMessage || err?.message || 'signals_delete_failed';
      throw err;
    }
    // Clear local caches and flags
    activeSignal.value = null;
    historicalSignals.value = [];
    events.value = [];
    historyLoaded = false;
    if (options?.alsoResetPosition) {
      position.value = null;
    }
  }

  // Convenience: delete all signals server-side and clear local position/signals at once
  async function resetAllSignals() {
    await clearServerSignals({ alsoResetPosition: true });
  }

  // Auto-refresh performance when a heartbeat arrives (best-effort throttle)
  {
    let _perfTimer: any = null;
    const schedulePerf = () => {
      if (_perfTimer) return;
      _perfTimer = setTimeout(async () => {
        _perfTimer = null;
        try { await fetchPerformance(); } catch { /* ignore */ }
      }, 1500);
    };
    // Lightweight event tap: when we push any event, schedule a refresh
    const origPush = pushEvent;
    // @ts-expect-error: we intentionally reassign a function to inject scheduling
    pushEvent = (evt: AutopilotEvent) => {
      origPush(evt);
      if (evt?.type === 'heartbeat' || evt?.type === 'order' || evt?.type === 'trade') schedulePerf();
    };
  }

  return {
    loading,
    error,
    strategy,
    position,
    activeSignal,
    pendingOrders,
    health,
    risk,
    performance,
    events,
    streamConnected,
    exitPolicy,
    historicalSignals,
    hasPosition,
    latestEvent,
    fetchInitial,
    fetchState,
    fetchPerformance,
    fetchSignalHistory,
    connectStream,
    disconnectStream,
    resetPositionLocal,
    clearServerSignals,
    resetAllSignals,
  };
});
