import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import http from '../lib/http';
import { connectSSE } from '../lib/sse';

interface SessionState {
  starting_equity: number;
  peak_equity: number;
  current_equity: number;
  cumulative_pnl: number;
  last_reset_ts?: number;
}
interface PositionState { symbol: string; size: number; entry_price: number; }
interface RiskLimits { max_notional: number; max_daily_loss: number; max_drawdown: number; atr_multiple: number }
interface RiskStateResponse { session: SessionState; positions: PositionState[]; limits?: RiskLimits }

export const useRiskStore = defineStore('risk', () => {
  const session = ref<SessionState | null>(null);
  const positions = ref<PositionState[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const lastUpdated = ref<number | null>(null);
  const limits = ref<RiskLimits | null>(null);
  const connStatus = ref<'idle'|'online'|'lagging'|'offline'>('idle');
  const lastServerTs = ref<number | null>(null);
  const auto = ref(true);
  const intervalSec = ref(10);
  let timer: any = null;
  let sseHandle: { close: () => void } | null = null;
  const equityHistory = ref<number[]>([]);
  const historyLimit = 240; // ~40 min at 10s

  const drawdown = computed(() => {
    if (!session.value) return null;
    const peak = session.value.peak_equity;
    const cur = session.value.current_equity;
    if (peak <= 0) return 0;
    return (peak - cur) / peak; // fraction
  });
  const pnl = computed(() => session.value?.cumulative_pnl ?? null);
  const utilization = computed(() => {
    // Prefer notional utilization against applied limit (DB-admin), fallback to starting_equity if limit unknown
    let exposure = 0;
    for (const p of positions.value) {
      const notional = Math.abs(p.size * p.entry_price);
      if (!isFinite(notional)) continue;
      exposure += notional;
    }
    const maxNotional = limits.value?.max_notional;
    if (typeof maxNotional === 'number' && maxNotional > 0) {
      return exposure / maxNotional;
    }
    const start = session.value?.starting_equity;
    if (!start || start <= 0) return null;
    return exposure / start;
  });

  const sparkPath = computed(() => {
    const pts = equityHistory.value;
    if (pts.length < 2) return '';
    const w = 100; const h = 30;
    const min = Math.min(...pts);
    const max = Math.max(...pts);
    const span = max - min || 1;
    return pts.map((v,i) => {
      const x = (i/(pts.length-1))*w;
      const y = h - ((v - min)/span)*h;
      return (i===0?`M${x},${y}`:`L${x},${y}`);
    }).join(' ');
  });

  function pushEquity() {
    if (!session.value) return;
    const v = session.value.current_equity;
    if (typeof v === 'number' && isFinite(v)) {
      equityHistory.value.push(v);
      if (equityHistory.value.length > historyLimit) equityHistory.value.shift();
    }
  }

  function applyRiskDelta(payload: any) {
    // Accept snapshot or delta shapes: { session, positions } minimal subset
    if (!payload || typeof payload !== 'object') return;
    if (payload.session) session.value = { ...(session.value || {} as any), ...payload.session } as any;
    if (Array.isArray(payload.positions)) positions.value = payload.positions as any;
    lastUpdated.value = Date.now();
    // Optional server_time for lag calc
    if (typeof payload.server_time === 'number') lastServerTs.value = payload.server_time * 1000;
    pushEquity();
  }

  async function fetchState() {
    loading.value = true; error.value = null;
    try {
      const r = await http.get('/api/risk/state');
      const data: RiskStateResponse = r.data;
      if (data?.session) session.value = data.session;
      positions.value = Array.isArray(data?.positions) ? data.positions : [];
      if (data?.limits && typeof data.limits === 'object') {
        limits.value = data.limits as RiskLimits;
      }
      lastUpdated.value = Date.now();
      pushEquity();
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'fetch failed';
    } finally {
      loading.value = false;
    }
  }

  function connectLive() {
    // Idempotent connect; if already connected, close and reopen for safety
    try { sseHandle && sseHandle.close(); } catch(_) {}
    sseHandle = null;
    const base = (import.meta as any).env?.VITE_BACKEND_URL || '';
    const url = `${base}/stream/risk`.replace(/\/$/, '/stream/risk');
    connStatus.value = 'offline';
    sseHandle = connectSSE({ url, heartbeatTimeoutMs: 15000 }, {
      onOpen: () => { connStatus.value = 'online'; },
      onMessage: ({ type, data }) => {
        if (!data) return;
        if (data.type === 'heartbeat' || type === 'heartbeat') {
          // Heartbeat carries server_time optionally
          if (typeof data.server_time === 'number') lastServerTs.value = data.server_time * 1000;
          const age = Date.now() - (lastServerTs.value || Date.now());
          connStatus.value = age > 15000 ? 'lagging' : 'online';
          return;
        }
        // snapshot or delta
        applyRiskDelta(data);
        const age = Date.now() - (lastServerTs.value || Date.now());
        connStatus.value = age > 15000 ? 'lagging' : 'online';
      },
      onError: () => { connStatus.value = 'lagging'; },
      onClose: () => { connStatus.value = 'offline'; },
    });
  }

  function startAuto() {
    stopAuto();
    if (auto.value) {
      timer = setInterval(fetchState, Math.max(3, intervalSec.value)*1000);
    }
  }
  function stopAuto() { if (timer) clearInterval(timer); timer = null; }
  function startLive() { connectLive(); }
  function stopLive() { if (sseHandle) { try { sseHandle.close(); } catch(_){} sseHandle = null; } connStatus.value = 'offline'; }
  function toggleAuto(v?: boolean) { auto.value = typeof v === 'boolean' ? v : !auto.value; if (auto.value) fetchState(); startAuto(); }
  function setIntervalSec(v: number) { intervalSec.value = Math.max(3, v); startAuto(); }

  async function deletePosition(symbol: string) {
    try {
      await http.delete(`/api/trading/positions/${encodeURIComponent(symbol)}`);
      const idx = positions.value.findIndex(p => p.symbol === symbol);
      if (idx >= 0) positions.value.splice(idx, 1);
      // Optionally refresh session metrics after deletion
      await fetchState();
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'delete failed';
      throw e;
    }
  }

  return { session, positions, loading, error, lastUpdated, auto, intervalSec, drawdown, pnl, utilization, sparkPath, equityHistory, limits,
    connStatus, lastServerTs,
    fetchState, startAuto, stopAuto, toggleAuto, setIntervalSec, deletePosition, startLive, stopLive };
});