import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import http from '@/lib/http';

export interface IngestionStatus {
  enabled?: boolean;
  running?: boolean;
  total_messages?: number;
  last_message_ts?: number;
  reconnect_attempts?: number;
  stale?: boolean;
  buffer_size?: number;
  batch_size?: number;
  last_flush_ts?: number;
  lag_seconds?: number | null;
  flush_interval?: number;
  total_closed?: number;
}

export const useIngestionStore = defineStore('ingestion', () => {
  const status = ref<IngestionStatus>({});
  const lastUpdated = ref<number | null>(null);
  const loading = ref(false);
  const lagHistory = ref<number[]>([]);
  const error = ref<string | null>(null);
  const historyLimit = 120;
  const auto = ref(true);
  const intervalSec = ref(5);
  let timer: any = null;
  let lagTimer: any = null;

  // --- Hydrate from localStorage (soft cache) to avoid empty dashboard flash after reload
  try {
    const cached = localStorage.getItem('ingestion_status_cache_v1');
    if (cached) {
      const parsed = JSON.parse(cached);
      if (parsed && typeof parsed === 'object') {
        if (parsed.status && typeof parsed.status === 'object') {
          status.value = parsed.status;
        }
        if (typeof parsed.lastUpdated === 'number') {
          lastUpdated.value = parsed.lastUpdated;
        }
        if (Array.isArray(parsed.lagHistory)) {
          lagHistory.value = parsed.lagHistory.slice(-historyLimit);
        }
      }
    }
  } catch (_) {
    // ignore hydration errors
  }

  const lag = computed(() => {
    if (!status.value.last_message_ts) return '-';
    return (Date.now()/1000 - status.value.last_message_ts).toFixed(1);
  });
  const stale = computed(() => status.value.stale === true);
  const sparkPath = computed(() => {
    const pts = lagHistory.value;
    if (pts.length < 2) return '';
    const w = 100, h = 24;
    const max = Math.max(...pts, 1);
    const step = w / (pts.length - 1);
    return pts.map((v,i)=>`${i===0?'M':'L'}${(i*step).toFixed(1)},${(h - (v/max)*h).toFixed(1)}`).join(' ');
  });
  const bufferFillRatio = computed(() => {
    if (!status.value.batch_size || status.value.batch_size === 0) return 0;
    return Math.min(1, (status.value.buffer_size || 0) / status.value.batch_size);
  });
  const bufferBarClass = computed(() => {
    if (bufferFillRatio.value > 0.8) return 'bg-brand-danger';
    if (bufferFillRatio.value > 0.5) return 'bg-amber-400';
    return 'bg-brand-accent';
  });
  const flushAgeLabel = computed(() => {
    const ts = status.value.last_flush_ts;
    if (!ts) return 'n/a';
    const age = Date.now()/1000 - ts;
    if (age < 2) return 'just now';
    if (age < 60) return age.toFixed(0)+'s';
    return Math.floor(age/60)+'m';
  });

  function pushLagPoint() {
    if (typeof status.value.last_message_ts === 'number') {
      const v = Number((Date.now()/1000 - status.value.last_message_ts).toFixed(1));
      if (!isNaN(v)) {
        lagHistory.value.push(v);
        if (lagHistory.value.length > historyLimit) lagHistory.value.shift();
      }
    }
  }

  async function fetchStatus() {
    // Prevent overlapping requests when network is slow vs interval
    if (loading.value) return;
    try {
      loading.value = true;
      error.value = null;
  const r = await http.get('/api/ingestion/status');
      status.value = r.data;
      lastUpdated.value = Date.now();
      pushLagPoint();
  // eslint-disable-next-line no-console
  if ((import.meta as any).env?.DEV) console.debug('[ingestion] fetched', status.value);
      // Persist lightweight cache (omit large arrays if any in future)
      try {
        localStorage.setItem('ingestion_status_cache_v1', JSON.stringify({
          status: status.value,
          lastUpdated: lastUpdated.value,
          lagHistory: lagHistory.value.slice(-historyLimit),
        }));
      } catch (_) {
        // ignore quota errors
      }
    } catch (e: any) {
      // eslint-disable-next-line no-console
      console.warn('[ingestion] fetch failed', e);
      error.value = e?.message || 'fetch failed';
    } finally {
      loading.value = false;
    }
  }

  function startPolling() {
    stopPolling();
    if (lagTimer) clearInterval(lagTimer);
    lagTimer = setInterval(pushLagPoint, 1000);
    if (auto.value) {
      // Kick an immediate fetch so UI updates without waiting a full interval
      fetchStatus();
      timer = setInterval(fetchStatus, intervalSec.value * 1000);
    }
  }

  function stopPolling() {
    if (timer) clearInterval(timer); timer = null;
  }

  function setIntervalSec(v: number) {
    intervalSec.value = v;
    startPolling(); // includes an immediate fetch
  }

  function toggleAuto(v?: boolean) {
    if (typeof v === 'boolean') auto.value = v; else auto.value = !auto.value;
    startPolling(); // includes an immediate fetch
  }

  return {
    status, lastUpdated, loading, lagHistory, auto, intervalSec, error,
    lag, stale, sparkPath, bufferFillRatio, bufferBarClass, flushAgeLabel,
    fetchStatus, startPolling, stopPolling, setIntervalSec, toggleAuto,
  };
});
