import { defineStore } from 'pinia';
import { ref, onUnmounted } from 'vue';
import http from '@/lib/http';
import { useOhlcvStore } from '@/stores/ohlcv';

export interface InferenceResult {
  status?: string;
  probability?: number;
  decision?: number;
  model_version?: string | number;
  used_production?: boolean;
  threshold?: number;
  overridden_threshold?: boolean;
  [k: string]: any;
}

export interface HistoryEntry {
  ts: number;
  probability: number | null;
  decision: number | null;
  threshold: number;
}

export const useInferenceStore = defineStore('inference', () => {
  const threshold = ref(0.5);
  // If true, manual predict sends threshold param (client override).
  // If false, backend uses its DB/effective threshold.
  const useClientThreshold = ref(false);
  // bottom-only
  const selectedTarget = ref<'bottom'>('bottom');
  // Model selection policy for predict API: production (default) | latest | specific(version)
  const selectionMode = ref<'production' | 'latest' | 'specific'>('production');
  const forcedVersion = ref<string>('');
  const lastResult = ref<InferenceResult | null>(null);
  const history = ref<HistoryEntry[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const lastRequestId = ref<string | null>(null);
  // auto mode persisted locally to survive page refresh (optional UX convenience)
  const auto = ref(false);
  try {
    const persisted = localStorage.getItem('inference_auto');
    if (persisted === 'true') auto.value = true;
    const thr = localStorage.getItem('inference_threshold');
    if (thr != null) {
      const v = Number(thr);
      if (!isNaN(v) && v >= 0 && v <= 1) threshold.value = v;
    }
    const uct = localStorage.getItem('inference_use_client_threshold');
    if (uct === 'true') useClientThreshold.value = true;
  // Force bottom-only regardless of old localStorage
  try { localStorage.setItem('inference_target', 'bottom'); } catch {}
    const sel = localStorage.getItem('inference_select_mode');
    if (sel === 'production' || sel === 'latest' || sel === 'specific') selectionMode.value = sel;
    const fv = localStorage.getItem('inference_forced_version');
    if (typeof fv === 'string') forcedVersion.value = fv;
    const hist = localStorage.getItem('inference_history_v1');
    if (hist) {
      try {
        const arr = JSON.parse(hist);
        if (Array.isArray(arr)) history.value = arr.slice(0, 30);
      } catch {}
    }
  } catch { /* noop */ }
  const intervalSec = ref(5);
  let timer: any = null;
  const historyLimit = 30;

  function pushHistory(prob: number | undefined, decision: number | undefined, usedThreshold?: number | null) {
    history.value.unshift({
      ts: Date.now(),
      probability: typeof prob === 'number' && !isNaN(prob) ? prob : null,
      decision: typeof decision === 'number' ? decision : null,
      // Record the threshold actually used by backend if provided; fallback to current slider value
      threshold: typeof usedThreshold === 'number' && !isNaN(usedThreshold) ? usedThreshold : threshold.value,
    });
    if (history.value.length > historyLimit) history.value.pop();
    try { localStorage.setItem('inference_history_v1', JSON.stringify(history.value)); } catch {}
  }

  async function runOnce() {
    try {
      loading.value = true;
      error.value = null;
      lastRequestId.value = null;
      const ohlcv = useOhlcvStore();
      const params: Record<string, any> = {
        symbol: ohlcv.symbol,
        interval: ohlcv.interval,
        target: 'bottom',
      };
      if (useClientThreshold.value) params.threshold = threshold.value;
      // Apply selection override
      if (selectionMode.value === 'latest') {
        params.use = 'latest';
      } else if (selectionMode.value === 'specific' && forcedVersion.value) {
        params.version = forcedVersion.value;
      }
      const r = await http.get('/api/inference/predict', { params });
      lastResult.value = r.data;
      if (r.data?.status === 'ok') {
        // Guard: skip if backend flags indicate seed/no_data/insufficient/no_model
        const s = String(r.data?.backend_status || r.data?.source || '').toLowerCase();
        const st = String(r.data?.status || '').toLowerCase();
        const bad = (
          r.data?.seed ||
          s.includes('seed') || st.includes('seed') ||
          s.includes('no_data') || st.includes('no_data') ||
          s.includes('no-model') || st.includes('no-model') ||
          s.includes('no_model') || st.includes('no_model') ||
          s.includes('insufficient') || st.includes('insufficient')
        );
        // Guard: push only when we have a numeric probability and a real model context (prod or version)
        const hasProb = typeof r.data?.probability === 'number' && isFinite(r.data.probability);
        const hasModel = Boolean(r.data?.used_production || r.data?.model_version);
        if (!bad && hasProb && hasModel) {
          const usedThr = (typeof r.data?.threshold === 'number' && !isNaN(r.data.threshold)) ? r.data.threshold : undefined;
          pushHistory(r.data.probability, r.data.decision, usedThr);
        }
      } else {
        error.value = r.data?.status || 'unknown status';
      }
    } catch (e: any) {
      console.warn('[inference] run failed', e);
      error.value = e.__friendlyMessage || e.message || 'request failed';
      try { lastRequestId.value = e?.requestId ? String(e.requestId) : null; } catch { lastRequestId.value = null; }
    } finally {
      loading.value = false;
    }
  }

  function startAuto() {
    // Prevent stacking intervals
    stopAuto();
    if (!auto.value) return;
    // Immediately schedule next run after current finishes (simple setInterval is fine here)
    timer = setInterval(() => {
      if (!loading.value) {
        runOnce();
      }
    }, Math.max(1, intervalSec.value) * 1000);
  }

  function stopAuto() {
    if (timer) clearInterval(timer); timer = null;
  }

  function toggleAuto(v?: boolean) {
    if (typeof v === 'boolean') auto.value = v; else auto.value = !auto.value;
  try { localStorage.setItem('inference_auto', String(auto.value)); } catch {}
    if (auto.value) {
      runOnce();
    } else {
      stopAuto();
    }
    startAuto();
  }

  function setThreshold(v: number) {
    threshold.value = Math.min(1, Math.max(0, v));
    try { localStorage.setItem('inference_threshold', String(threshold.value)); } catch {}
  }

  function setUseClientThreshold(v: boolean) {
    useClientThreshold.value = !!v;
    try { localStorage.setItem('inference_use_client_threshold', String(useClientThreshold.value)); } catch {}
  }

  function setTarget(_t: 'direction' | 'bottom') {
    // bottom-only; ignore input and keep bottom
    selectedTarget.value = 'bottom';
    try { localStorage.setItem('inference_target', 'bottom'); } catch {}
  }

  function setSelectionModeLocal(m: 'production' | 'latest' | 'specific') {
    selectionMode.value = m;
    try { localStorage.setItem('inference_select_mode', m); } catch {}
    if (m !== 'specific') {
      // Clear forced version when not in specific mode
      forcedVersion.value = '';
      try { localStorage.removeItem('inference_forced_version'); } catch {}
    }
  }

  function setForcedVersionLocal(v: string) {
    forcedVersion.value = v;
    try { localStorage.setItem('inference_forced_version', v); } catch {}
  }

  function setIntervalSec(v: number) {
    intervalSec.value = Math.max(1, v);
    // restarting auto loop with new interval
    if (auto.value) startAuto();
  }

  onUnmounted(() => {
    stopAuto();
  });

  function decisionColor(d: number | undefined | null) {
    if (d === 1) return 'text-brand-accent';
    if (d === -1) return 'text-brand-danger';
    return 'text-neutral-400';
  }

  return {
    threshold, useClientThreshold, selectedTarget, selectionMode, forcedVersion, lastResult, history, loading, error, lastRequestId, auto, intervalSec,
    runOnce, toggleAuto, setThreshold, setUseClientThreshold, setTarget, setSelectionModeLocal, setForcedVersionLocal, setIntervalSec, startAuto, stopAuto, decisionColor,
  };
});