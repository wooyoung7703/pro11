<template>
  <div class="risk-card">
    <header>
      <h3>리스크 한도</h3>
    </header>
    <div class="grid" v-if="risk">
      <div>
        <label>최대 손실률</label>
        <strong>{{ formatPct(risk.max_drawdown) }}</strong>
      </div>
      <div>
        <label>최대 익스포저</label>
        <strong>{{ formatCurrency(risk.max_notional) }}</strong>
      </div>
      <div>
        <label>현재 드로우다운</label>
        <strong>{{ formatPct(risk.drawdown_pct) }}</strong>
      </div>
      <div>
        <label>활용률</label>
        <strong>{{ formatPct(risk.utilization_pct) }}</strong>
      </div>
      <div>
        <label>쿨다운</label>
        <strong>{{ formatCooldown(risk.cooldown_sec) }}</strong>
      </div>
    </div>
    <div v-else class="empty">리스크 정보가 없습니다.</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { AutopilotRiskSnapshot } from '@/types/autopilot';

const props = defineProps<{ risk: AutopilotRiskSnapshot | null }>();

const pctFormatter = new Intl.NumberFormat('ko-KR', { style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const currencyFormatter = new Intl.NumberFormat('ko-KR', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

function formatPct(value?: number | null) {
  if (value == null) return '-';
  return pctFormatter.format(value);
}

function formatCurrency(value?: number | null) {
  if (value == null) return '-';
  return currencyFormatter.format(value);
}

function formatCooldown(value?: number | null) {
  if (!value) return '-';
  return `${value}s`;
}

const risk = computed(() => props.risk);
</script>

<style scoped>
.risk-card {
  background: #111d2e;
  border-radius: 10px;
  padding: 12px;
  border: 1px solid rgba(60, 86, 125, 0.4);
  color: #d2ddf0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 220px;
}
header h3 {
  margin: 0;
  font-size: 15px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 12px;
}
label {
  font-size: 11px;
  color: #7f8ea8;
}
strong {
  display: block;
  font-size: 13px;
}
.empty {
  text-align: center;
  color: #6f7f98;
  padding: 20px 0;
  margin-top: auto;
}
</style>
