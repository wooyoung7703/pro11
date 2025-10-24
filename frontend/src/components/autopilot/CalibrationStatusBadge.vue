<template>
  <div class="calib-badge bg-brand-700/80 border border-slate-700 rounded-xl text-slate-200" :class="chipClass" :title="tooltip">
    <span class="dot" />
    <span class="label">캘리브레이션</span>
    <span class="kv">ECE <b>{{ liveEceText }}</b></span>
    <span class="kv" v-if="deltaText">Δ <b>{{ deltaText }}</b></span>
    <span class="kv">s {{ samples }}/{{ minSamples }}</span>
    <span class="kv" v-if="streak>0">streak {{ streak }}</span>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import http from '@/lib/http';

interface CalibStatus {
  enabled?: boolean;
  labeler_enabled?: boolean;
  abs_streak?: number;
  rel_streak?: number;
  window_seconds?: number;
  min_samples?: number;
  interval?: number;
  recommend_retrain?: boolean;
  recommend_retrain_reasons?: string[];
  last_snapshot?: {
    ts?: number;
    live_ece?: number | null;
    live_mce?: number | null;
    prod_ece?: number | null;
    delta?: number | null;
    abs_drift?: boolean;
    rel_drift?: boolean;
    sample_count?: number;
  } | null;
}

const data = ref<CalibStatus | null>(null);
let timer: number | null = null;

async function fetchStatus() {
  try {
    const r = await http.get<CalibStatus>('/api/monitor/calibration/status');
    data.value = r.data as any;
  } catch {/* keep last */}
}

onMounted(() => {
  fetchStatus();
  timer = window.setInterval(fetchStatus, 60_000);
});
onBeforeUnmount(() => { if (timer) clearInterval(timer); });

function fmt4(n: number | null | undefined): string {
  if (n == null || !isFinite(Number(n))) return '—';
  return Number(n).toFixed(4);
}

const liveEce = computed(() => data.value?.last_snapshot?.live_ece ?? null);
const _prodEce = computed(() => data.value?.last_snapshot?.prod_ece ?? null);
const delta = computed(() => data.value?.last_snapshot?.delta ?? null);
const samples = computed(() => data.value?.last_snapshot?.sample_count ?? 0);
const minSamples = computed(() => data.value?.min_samples ?? 1000);
const streak = computed(() => data.value?.abs_streak ?? 0);
const liveEceText = computed(() => fmt4(liveEce.value));
const deltaText = computed(() => (delta.value==null? '' : fmt4(delta.value)));

const stateKey = computed(() => {
  if (!data.value?.enabled) return 'off';
  const s = samples.value;
  const ms = minSamples.value || 1000;
  const absDrift = Boolean(data.value?.last_snapshot?.abs_drift);
  if (s < Math.min(100, Math.floor(ms * 0.1))) return 'low';
  if (absDrift) return 'drift';
  return 'ok';
});

const chipClass = computed(() => ({
  'state-ok': stateKey.value === 'ok',
  'state-low': stateKey.value === 'low',
  'state-drift': stateKey.value === 'drift',
  'state-off': stateKey.value === 'off',
}));

const tooltip = computed(() => {
  const d = data.value;
  if (!d) return '—';
  const parts: string[] = [];
  parts.push(`enabled=${d.enabled ? 'on':'off'}`);
  parts.push(`labeler=${d.labeler_enabled ? 'on':'off'}`);
  parts.push(`live_ece=${fmt4(d.last_snapshot?.live_ece ?? null)}`);
  parts.push(`prod_ece=${fmt4(d.last_snapshot?.prod_ece ?? null)}`);
  parts.push(`delta=${fmt4(d.last_snapshot?.delta ?? null)}`);
  parts.push(`abs_drift=${d.last_snapshot?.abs_drift ? 'Y':'N'}`);
  parts.push(`samples=${samples.value}/${minSamples.value}`);
  if (d.recommend_retrain) parts.push('recommend=Y');
  return parts.join(' · ');
});
</script>

<style scoped>
.calib-badge { display:inline-flex; align-items:center; gap:8px; padding:8px 10px; font-size:12px; height: fit-content; }
.dot { width:8px; height:8px; border-radius:50%; display:inline-block; box-shadow: 0 0 6px currentColor; }
.label { color:#a7b8d8; }
.kv { color:#b9c7de; }
.state-ok { color:#86efac; border-color: rgba(34,197,94,0.35); }
.state-ok .dot { background:#22c55e; }
.state-low { color:#fbbf24; border-color: rgba(245,158,11,0.35); }
.state-low .dot { background:#f59e0b; }
.state-drift { color:#fca5a5; border-color: rgba(239,68,68,0.35); }
.state-drift .dot { background:#ef4444; }
.state-off { color:#cbd5e1; border-color: rgba(148,163,184,0.3); }
.state-off .dot { background:#94a3b8; }
</style>
