<template>
  <div class="performance bg-brand-700/80 border border-slate-700 rounded-xl text-slate-200">
    <header>
      <h3>성과 요약</h3>
      <span v-if="performance" class="updated">{{ formatTime(performance.updated_ts) }} 업데이트</span>
    </header>
    <div class="cards" v-if="performance && performance.buckets && performance.buckets.length">
      <div v-for="bucket in performance.buckets" :key="bucket.window" class="card">
        <span class="window">{{ bucket.window }}</span>
        <strong :class="{ up: (bucket.pnl ?? 0) >= 0, down: (bucket.pnl ?? 0) < 0 }">{{ formatCurrency(bucket.pnl) }}</strong>
        <div class="meta">
          <span>손익률 {{ formatPct(bucket.pnl_pct) }}</span>
          <span>승률 {{ formatPct(bucket.win_rate) }}</span>
          <span>트레이드 {{ bucket.trades }}</span>
        </div>
      </div>
    </div>
    <div v-else class="empty">성과 데이터가 아직 없습니다.</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { AutopilotPerformance } from '@/types/autopilot';

const props = defineProps<{ performance: AutopilotPerformance | null }>();

const pctFormatter = new Intl.NumberFormat('ko-KR', { style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const currencyFormatter = new Intl.NumberFormat('ko-KR', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
const timeFormatter = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

const performance = computed(() => props.performance);

function formatPct(value?: number | null) {
  if (value == null) return '-';
  return pctFormatter.format(value);
}

function formatCurrency(value?: number | null) {
  if (value == null) return '-';
  return currencyFormatter.format(value);
}

function formatTime(ts: number) {
  return timeFormatter.format(new Date(ts * 1000));
}
</script>

<style scoped>
.performance { border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 12px; min-height: 220px; }
header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.updated {
  font-size: 11px;
  color: #7f8ea8;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 12px;
}
.card { background: rgba(19, 32, 51, 0.6); border: 1px solid rgba(70, 96, 140, 0.3); border-radius: 8px; padding: 10px; display: flex; flex-direction: column; gap: 6px; }
.window {
  font-size: 12px;
  color: #9cb2d6;
}
strong {
  font-size: 16px;
}
.up { color: #3ce69a; }
.down { color: #f87171; }
.meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 11px;
  color: #9cb2d6;
}
.empty {
  text-align: center;
  color: #6f7f98;
  padding: 20px 0;
  margin-top: auto;
}
</style>
