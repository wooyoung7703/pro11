<template>
  <div class="badge-card" :title="tooltip">
    <span class="dot" :class="dotClass"></span>
    <span class="text">활동</span>
    <span class="counts">{{ d1 }} / {{ d5 }}</span>
    <span class="unit">(1m / 5m)</span>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import http from '@/lib/http';

interface ActivitySummary { status: string; last_decision_ts: number | null; decisions_1m: number; decisions_5m: number; auto_loop_enabled: boolean; interval: number | null; disable_reason?: string | null; }

const data = ref<ActivitySummary | null>(null);
let timer: number | null = null;

async function fetchSummary() {
  try {
    const r = await http.get<ActivitySummary>('/api/inference/activity/summary');
    data.value = r.data as any;
  } catch {/* keep last */}
}

onMounted(() => {
  fetchSummary();
  timer = window.setInterval(fetchSummary, 10_000);
});
onBeforeUnmount(() => { if (timer) clearInterval(timer); });

const d1 = computed(() => data.value?.decisions_1m ?? 0);
const d5 = computed(() => data.value?.decisions_5m ?? 0);

const statusKey = computed(() => {
  if (!data.value?.auto_loop_enabled) return 'inactive';
  if (d1.value === 0 && d5.value === 0) return 'idle';
  if (d5.value > 0 && d5.value < 5) return 'warming';
  return 'active';
});

const dotClass = computed(() => ({
  'dot--green': statusKey.value === 'active',
  'dot--amber': statusKey.value === 'warming',
  'dot--gray': statusKey.value === 'idle',
  'dot--red': statusKey.value === 'inactive',
}));

const tooltip = computed(() => {
  const a = data.value;
  if (!a) return '—';
  const parts: string[] = [];
  parts.push(`auto=${a.auto_loop_enabled ? 'on':'off'}`);
  if (a.auto_loop_enabled && a.interval) parts.push(`interval=${a.interval}s`);
  parts.push(`1m=${d1.value}`);
  parts.push(`5m=${d5.value}`);
  if (!a.auto_loop_enabled && a.disable_reason) parts.push(`reason=${a.disable_reason}`);
  return parts.join(' · ');
});
</script>

<style scoped>
.badge-card { display:inline-flex; align-items:center; gap:6px; padding:8px 10px; border:1px solid rgba(70,96,140,.35); background:#0e1624; border-radius:10px; color:#d2ddf0; font-size:12px; height: fit-content; }
.dot { width:8px; height:8px; border-radius:50%; display:inline-block; box-shadow: 0 0 6px currentColor; }
.dot--green { color:#86efac; background:#22c55e; }
.dot--amber { color:#fbbf24; background:#f59e0b; }
.dot--gray { color:#cbd5e1; background:#94a3b8; }
.dot--red { color:#fca5a5; background:#ef4444; }
.text { color:#a7b8d8; }
.counts { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace; }
.unit { color:#8fa3c0; font-size:11px; }
</style>
