<template>
  <div class="snapshot">
    <header>
      <h3>포지션</h3>
      <div class="right">
        <button class="btn-reset" title="UI에서 포지션 정보를 초기화합니다" @click="onReset">초기화</button>
        <span class="status" :class="statusClass">{{ statusLabel }}</span>
      </div>
    </header>
    <div v-if="position" class="grid">
      <div>
        <label>심볼</label>
        <strong>{{ position.symbol }}</strong>
      </div>
      <div>
        <label>수량</label>
        <strong>{{ formatNumber(position.size) }}</strong>
      </div>
      <div>
        <label>평단</label>
        <strong>{{ formatPrice(position.avg_price) }}</strong>
      </div>
      <div>
        <label>미실현</label>
        <strong :class="{ up: position.unrealized_pnl >= 0, down: position.unrealized_pnl < 0 }">
          {{ formatPrice(position.unrealized_pnl) }}
        </strong>
      </div>
      <div>
        <label>실현 손익</label>
        <strong :class="{ up: position.realized_pnl >= 0, down: position.realized_pnl < 0 }">
          {{ formatPrice(position.realized_pnl) }}
        </strong>
      </div>
      <div>
        <label>업데이트</label>
        <strong>{{ formatTime(position.updated_ts) }}</strong>
      </div>
      <div v-if="metrics">
        <label>수익률</label>
        <strong :class="{ up: metrics.pnlValue >= 0, down: metrics.pnlValue < 0 }">
          {{ metrics.pnlLabel }}
        </strong>
      </div>
      <div v-if="metrics">
        <label>익절까지</label>
        <strong>{{ metrics.tpLabel }}</strong>
      </div>
      <div v-if="metrics">
        <label>손절까지</label>
        <strong>{{ metrics.slLabel }}</strong>
      </div>
    </div>
    <div v-else class="empty">열린 포지션이 없습니다.</div>
    <section v-if="signal" class="signal">
      <h4>활성 시그널</h4>
      <p class="kind">{{ signal.kind }} · {{ signal.confidence.toFixed(2) }}</p>
      <p v-if="signal.reason" class="reason">{{ signal.reason }}</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { AutopilotPosition, AutopilotSignal } from '@/types/autopilot';
import { useAutoTraderStore } from '@/stores/autoTrader';
import { useOhlcvStore } from '@/stores/ohlcv';

const props = defineProps<{
  position: AutopilotPosition | null;
  signal: AutopilotSignal | null;
}>();

const formatter = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 4 });
const moneyFormatter = new Intl.NumberFormat('ko-KR', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });
const moneyFineFormatter = new Intl.NumberFormat('ko-KR', { style: 'currency', currency: 'USD', maximumFractionDigits: 4 });
const percentFormatter = new Intl.NumberFormat('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const timeFormatter = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

const statusMap: Record<string, string> = {
  flat: '관망',
  long: '롱',
  short: '숏',
};

const statusClassMap: Record<string, string> = {
  flat: 'flat',
  long: 'long',
  short: 'short',
};

// Treat size===0 as closed (flat) so the card resets after settlement
const rawPosition = computed(() => props.position);
const position = computed(() => {
  const p = rawPosition.value as any;
  if (!p || typeof p !== 'object') return null;
  try {
    const size = Number(p.size ?? 0);
    if (!Number.isFinite(size) || size === 0) return null;
  } catch {
    return null;
  }
  return p as any;
});
const signal = computed(() => props.signal);
const autopilot = useAutoTraderStore();
const ohlcv = useOhlcvStore();
const exitPolicy = computed(() => autopilot.exitPolicy);
const currentPrice = computed(() => ohlcv.lastCandle?.close ?? null);

const statusLabel = computed(() => {
  const key = position.value?.status ?? rawPosition.value?.status ?? 'flat';
  return statusMap[key] || key;
});

const statusClass = computed(() => {
  const key = position.value?.status ?? rawPosition.value?.status ?? 'flat';
  return statusClassMap[key] || 'flat';
});

function formatNumber(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return formatter.format(value);
}

function formatPrice(value: number | null | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  return moneyFormatter.format(value);
}

function formatTime(ts: number | null | undefined) {
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return '—';
  return timeFormatter.format(new Date(ts * 1000));
}

function formatPercent(value: number | null) {
  if (value == null) return '—';
  return `${percentFormatter.format(value * 100)}%`;
}

function formatDiff(value: number | null) {
  if (value == null) return '—';
  return moneyFineFormatter.format(value);
}

const metrics = computed(() => {
  if (!position.value || !currentPrice.value || position.value.avg_price <= 0) return null;
  const avg = position.value.avg_price;
  const size = position.value.size;
  if (size === 0) return null;
  const direction = size >= 0 ? 1 : -1;
  const absSize = Math.abs(size);
  const priceChange = (currentPrice.value - avg) / avg;
  const pnlFraction = priceChange * direction;
  const pnlValue = absSize * avg * pnlFraction;

  const policy = exitPolicy.value;
  const takeProfitPct = policy?.take_profit_pct ?? null;
  const stopLossPct = policy?.stop_loss_pct ?? null;

  const longSide = direction >= 0;

  const takeProfitPrice = takeProfitPct != null
    ? avg * (longSide ? 1 + takeProfitPct : 1 - takeProfitPct)
    : null;
  const stopLossPrice = stopLossPct != null
    ? avg * (longSide ? 1 - stopLossPct : 1 + stopLossPct)
    : null;

  const tpRemainingPrice = takeProfitPrice != null
    ? (longSide ? takeProfitPrice - currentPrice.value : currentPrice.value - takeProfitPrice)
    : null;
  const slRemainingPrice = stopLossPrice != null
    ? (longSide ? currentPrice.value - stopLossPrice : stopLossPrice - currentPrice.value)
    : null;

  const tpRemainingPct = takeProfitPct != null ? takeProfitPct - pnlFraction : null;
  const slRemainingPct = stopLossPct != null ? pnlFraction + stopLossPct : null;

  const pnlLabel = `${formatPercent(pnlFraction)} (${moneyFormatter.format(pnlValue)})`;

  const tpLabel = takeProfitPct == null
    ? '—'
    : (tpRemainingPct != null && tpRemainingPct <= 0)
      ? '목표 도달'
      : `${formatDiff(tpRemainingPrice != null ? Math.max(tpRemainingPrice, 0) : null)} (${formatPercent(tpRemainingPct != null ? Math.max(tpRemainingPct, 0) : null)}) 남음`;

  const slLabel = stopLossPct == null
    ? '—'
    : (slRemainingPct != null && slRemainingPct <= 0)
      ? '손절 범위 이탈'
      : `${formatDiff(slRemainingPrice != null ? Math.max(slRemainingPrice, 0) : null)} (${formatPercent(slRemainingPct != null ? Math.max(slRemainingPct, 0) : null)}) 여유`;

  return {
    pnlValue,
    pnlFraction,
    pnlLabel,
    takeProfitPrice,
    stopLossPrice,
    tpLabel,
    slLabel,
  };
});

async function onReset() {
  try {
    // Remove server-side trading signals and clear local position/signals so a refresh doesn't re-draw markers
    await autopilot.resetAllSignals();
  } catch {
    // fall back to local-only clear so UI still resets
    try { autopilot.resetPositionLocal({ alsoClearSignal: true }); } catch { /* ignore */ }
  }
}
</script>

<style scoped>
.snapshot {
  background: #121f33;
  border-radius: 10px;
  padding: 12px;
  border: 1px solid rgba(60, 86, 125, 0.4);
  color: #d2ddf0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 220px;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
h3 { margin: 0; }
.right { display: inline-flex; align-items: center; gap: 8px; }
.btn-reset {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 6px;
  background: #21314b;
  color: #d2ddf0;
  border: 1px solid rgba(77, 108, 158, 0.45);
}
.btn-reset:hover { background: #2a3c5c; }
h3 {
  margin: 0;
  font-size: 15px;
}
.status {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
  border: 1px solid rgba(89, 111, 150, 0.6);
}
.status.long {
  color: #3ce69a;
  border-color: rgba(60, 230, 154, 0.5);
}
.status.short {
  color: #ff8f6f;
  border-color: rgba(255, 143, 111, 0.5);
}
.status.flat {
  color: #9aa8ba;
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
.up { color: #3ce69a; }
.down { color: #f87171; }
.empty {
  text-align: center;
  color: #6f7f98;
  padding: 20px 0;
  margin-top: auto;
}
.signal {
  border-top: 1px solid rgba(78, 103, 141, 0.35);
  padding-top: 8px;
}
.signal h4 {
  margin: 0 0 6px 0;
  font-size: 13px;
}
.kind {
  margin: 0;
  font-size: 12px;
  color: #8fb8ff;
}
.reason {
  margin: 4px 0 0 0;
  font-size: 12px;
  color: #cbd7eb;
}
</style>
