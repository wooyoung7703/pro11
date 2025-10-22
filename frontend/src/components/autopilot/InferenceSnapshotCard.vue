<template>
  <div class="snap-card">
    <header>
      <h3>추론 스냅샷</h3>
      <span class="ctx">{{ symbol }} · {{ interval || '—' }}</span>
    </header>
    <div class="body">
      <div class="row">
        <span class="label">확률</span>
        <span class="value font-mono" :title="typeof result?.probability==='number' ? String(result?.probability) : ''">{{ probDisplay }}</span>
      </div>
      <div class="bar">
        <div class="p" :style="{ width: probBarWidth }"></div>
        <div class="t" :style="{ left: thresholdLeft }" :title="`threshold=${appliedThreshold!=null?appliedThreshold.toFixed(2):'—'}`"></div>
      </div>
      <div class="row">
        <span class="label">결정</span>
        <span class="value" :class="decisionClass">{{ decisionDisplay }}</span>
      </div>
      <div class="grid">
        <div class="cell">
          <div class="label">임계값</div>
          <div class="value font-mono">{{ appliedThreshold != null ? appliedThreshold.toFixed(2) : '—' }}</div>
        </div>
        <div class="cell">
          <div class="label">모델 버전</div>
          <div class="value font-mono">{{ result?.model_version ?? '—' }}</div>
        </div>
        <div class="cell">
          <div class="label">프로덕션</div>
          <div class="value" :class="result?.used_production ? 'ok' : 'dim'">{{ result?.used_production ? '예' : '아니오' }}</div>
        </div>
        <div class="cell" :title="featureTitle">
          <div class="label">피처 연령</div>
          <div class="value" :class="isStale ? 'warn' : ''">{{ featureAge }}</div>
        </div>
      </div>
      <div v-if="hint" class="hint">{{ hint }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import http from '@/lib/http';
import { useOhlcvStore } from '@/stores/ohlcv';

const ohlcv = useOhlcvStore();
const symbol = computed(() => ohlcv.symbol);
const interval = computed(() => ohlcv.interval);

// Note: No client-side threshold override; use server-applied values
// Applied threshold: prefer API response, fallback to server effective
const effectiveThreshold = ref<number | null>(null);
const appliedThreshold = computed<number | null>(() => {
  const t = (typeof result.value?.threshold === 'number') ? Number(result.value?.threshold) : effectiveThreshold.value;
  return (typeof t === 'number' && isFinite(t)) ? t : null;
});

interface PredictRes {
  status?: string;
  probability?: number;
  decision?: number;
  model_version?: string | number;
  used_production?: boolean;
  threshold?: number;
  overridden_threshold?: boolean;
  feature_close_time?: number | string | null;
  feature_age_seconds?: number | null;
  hint?: string;
}

const result = ref<PredictRes | null>(null);
const timer = ref<number | null>(null);

async function fetchOnce() {
  try {
    const params: Record<string, any> = {
      symbol: symbol.value,
      interval: interval.value,
      target: 'bottom',
    };
    // Use default selection (production-first) for stability on dashboard
    const r = await http.get<PredictRes>('/api/inference/predict', { params });
    result.value = r.data as any;
  } catch {
    // keep last value on errors
  }
}

onMounted(() => {
  fetchOnce();
  timer.value = window.setInterval(fetchOnce, 10_000);
  loadEffectiveThreshold();
});
onBeforeUnmount(() => { if (timer.value) clearInterval(timer.value); });

function formatProb(prob: number | null | undefined, fixed: number = 4): string {
  if (prob == null) return '—';
  const p = Number(prob);
  if (!isFinite(p)) return '—';
  if (p === 0) return '0';
  return p >= 1e-3 ? p.toFixed(fixed) : p.toExponential(2);
}

const probDisplay = computed(() => formatProb(result.value?.probability, 4));
const probBarWidth = computed(() => {
  const p = result.value?.probability;
  return typeof p === 'number' && p >= 0 && p <= 1 ? (p * 100).toFixed(1) + '%' : '0%';
});
const thresholdLeft = computed(() => {
  const t = appliedThreshold.value;
  if (typeof t === 'number' && t > 0 && t < 1) return (t * 100).toFixed(1) + '%';
  return '0%';
});

const decisionDisplay = computed(() => {
  const d = result.value?.decision;
  return d === 1 ? 'TRIGGER' : 'NO TRIGGER';
});
const decisionClass = computed(() => (result.value?.decision === 1 ? 'hit' : 'miss'));

const featureAge = computed(() => {
  const s = result.value?.feature_age_seconds;
  if (s == null || !isFinite(Number(s)) || Number(s) < 0) return '—';
  const n = Number(s);
  if (n < 60) return `${n.toFixed(0)}s`;
  if (n < 3600) return `${Math.floor(n/60)}m ${Math.floor(n%60)}s`;
  const h = Math.floor(n/3600), m = Math.floor((n%3600)/60);
  return `${h}h ${m}m`;
});
const isStale = computed(() => (result.value?.feature_age_seconds ?? 0) > 180);
const featureTitle = computed(() => `feature_close_time=${String(result.value?.feature_close_time ?? '—')}`);
const hint = computed(() => result.value?.hint || '');

async function loadEffectiveThreshold(){
  try {
    // Prefer non-admin endpoint
    try {
      const r1 = await http.get('/api/inference/effective_threshold');
      const t1 = r1.data?.threshold;
      if (typeof t1 === 'number' && isFinite(t1)) { effectiveThreshold.value = t1; return; }
    } catch {}
    // Fallback to admin diagnostics
    try {
      const r2 = await http.get('/admin/inference/settings');
      const e = r2.data?.threshold?.effective_default;
      if (typeof e === 'number' && isFinite(e)) { effectiveThreshold.value = e; return; }
    } catch {}
  } catch {}
}
</script>

<style scoped>
.snap-card { background:#0e1624; border:1px solid rgba(70,96,140,.35); border-radius:10px; padding:12px; color:#d2ddf0; display:flex; flex-direction:column; gap:10px; }
header { display:flex; align-items:center; gap:8px; }
header h3 { margin:0; font-size:15px; }
.ctx { font-size:11px; color:#8fa3c0; margin-left:auto; }
.body { display:flex; flex-direction:column; gap:8px; }
.row { display:flex; align-items:center; justify-content:space-between; }
.label { font-size:12px; color:#8fa3c0; }
.value { font-size:13px; }
.value.ok { color:#86efac; }
.value.dim { color:#9aa8bf; }
.value.warn { color:#fbbf24; }
.hit { color:#86efac; font-weight:600; }
.miss { color:#cbd5e1; }
.grid { display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:8px; }
.cell { display:flex; flex-direction:column; gap:2px; }
.bar { position:relative; height:6px; background:#30415f; border-radius:4px; overflow:hidden; }
.bar .p { position:absolute; top:0; left:0; bottom:0; background:#60a5fa; }
.bar .t { position:absolute; top:-3px; width:2px; bottom:-3px; background:#fbbf24; }
.hint { font-size:11px; color:#9aa8bf; }
</style>
