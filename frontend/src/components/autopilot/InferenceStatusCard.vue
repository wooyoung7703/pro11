<template>
  <div class="status-card">
    <header>
      <h3>추론 상태</h3>
      <span class="badge" :class="badgeClass">{{ statusText }}</span>
    </header>
    <div class="grid">
      <div class="item">
        <div class="label">루프</div>
        <div class="value">
          <span :class="enabledClass">{{ data?.auto_loop_enabled ? 'Enabled' : 'Disabled' }}</span>
          <span v-if="data?.auto_loop_enabled && data?.interval" class="sub">/ {{ data?.interval }}s</span>
        </div>
      </div>
      <div class="item">
        <div class="label">최근 결정</div>
        <div class="value">{{ lastDecisionAgo }}</div>
      </div>
      <div class="item">
        <div class="label">결정 빈도</div>
        <div class="value">{{ data?.decisions_1m ?? 0 }} / {{ data?.decisions_5m ?? 0 }} (1m / 5m)</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed } from 'vue';
import http from '@/lib/http';

interface ActivitySummary {
  status: 'ok';
  last_decision_ts: number | null;
  decisions_1m: number;
  decisions_5m: number;
  auto_loop_enabled: boolean;
  interval: number | null;
  override?: boolean;
  interval_override?: number | null;
  disable_reason?: string | null;
}

const data = ref<ActivitySummary | null>(null);
const timer = ref<number | null>(null);

async function fetchSummary() {
  try {
    const res = await http.get<ActivitySummary>('/api/inference/activity/summary');
    data.value = res.data as any;
  } catch (e) {
    // swallow errors; keep last good state
  }
}

function start() {
  fetchSummary();
  timer.value = window.setInterval(fetchSummary, 10_000);
}
function stop() {
  if (timer.value) {
    clearInterval(timer.value);
    timer.value = null;
  }
}

onMounted(start);
onBeforeUnmount(stop);

function formatAgo(ts: number | null): string {
  if (!ts) return '-';
  const now = Date.now() / 1000;
  const diff = Math.max(0, now - ts);
  if (diff < 60) return '<1m';
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  return `${(diff / 3600).toFixed(1)}h`;
}

const lastDecisionAgo = computed(() => formatAgo(data.value?.last_decision_ts ?? null));

const statusText = computed(() => {
  const d5 = data.value?.decisions_5m ?? 0;
  const d1 = data.value?.decisions_1m ?? 0;
  if (!data.value?.auto_loop_enabled) return 'inactive';
  if (d5 === 0 && d1 === 0) return 'idle';
  if (d5 > 0 && d5 < 5) return 'warming';
  return 'active';
});

const badgeClass = computed(() => {
  switch (statusText.value) {
    case 'active': return 'badge--green';
    case 'warming': return 'badge--amber';
    case 'idle': return 'badge--gray';
    default: return 'badge--off';
  }
});

const enabledClass = computed(() => data.value?.auto_loop_enabled ? 'on' : 'off');
</script>

<style scoped>
.status-card {
  background: #0e1624;
  border: 1px solid rgba(70, 96, 140, 0.35);
  border-radius: 10px;
  padding: 12px;
  color: #d2ddf0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
header {
  display: flex;
  align-items: center;
  gap: 8px;
}
header h3 {
  margin: 0;
  font-size: 15px;
}
.badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid transparent;
}
.badge--green { background: rgba(34,197,94,0.15); color: #86efac; border-color: rgba(34,197,94,0.35); }
.badge--amber { background: rgba(245,158,11,0.15); color: #fbbf24; border-color: rgba(245,158,11,0.35); }
.badge--gray { background: rgba(148,163,184,0.12); color: #cbd5e1; border-color: rgba(148,163,184,0.3); }
.badge--off { background: rgba(239,68,68,0.12); color: #fca5a5; border-color: rgba(239,68,68,0.3); }

.grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.item { display: flex; flex-direction: column; gap: 4px; }
.label { font-size: 11px; color: #8fa3c0; }
.value { font-size: 13px; }
.value .sub { font-size: 11px; color: #8fa3c0; margin-left: 6px; }
.value .on { color: #86efac; }
.value .off { color: #fca5a5; }
</style>
