<template>
  <div class="panel">
    <header class="panel-header">
      <div class="title">
        <h2>시장 뷰</h2>
        <span class="hint">{{ intervalLabel }} · 자동 갱신</span>
      </div>
      <div class="metrics">
        <span v-if="latestPrice!=null" class="metric">현재가 <strong>{{ formatPrice(latestPrice) }}</strong></span>
        <span v-if="positionSizeLabel" class="metric">포지션 {{ positionSizeLabel }}</span>
      </div>
    </header>
    <section class="panel-body">
      <TradingViewRealtimeChart :height="chartHeight" />
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, watch } from 'vue';
import TradingViewRealtimeChart from '@/components/ohlcv/TradingViewRealtimeChart.vue';
import { useOhlcvStore } from '@/stores/ohlcv';
import { useAutoTraderStore } from '@/stores/autoTrader';

const ohlcv = useOhlcvStore();
const autopilot = useAutoTraderStore();

const chartHeight = 520;

const latestPrice = computed(() => ohlcv.lastCandle?.close ?? null);
const positionSizeLabel = computed(() => {
  const size = autopilot.position?.size ?? 0;
  if (!size) return '';
  return `${size.toFixed(3)} ${autopilot.position?.symbol ?? ''}`.trim();
});

const intervalLabel = computed(() => {
  return `${ohlcv.symbol} · ${ohlcv.interval || '1m'}`;
});

function formatPrice(value: number) {
  return new Intl.NumberFormat('ko-KR', { style: 'currency', currency: 'USD', maximumFractionDigits: 3 }).format(value);
}

async function bootstrap(symbol: string, interval: string) {
  ohlcv.stopPolling();
  ohlcv.symbol = symbol;
  ohlcv.interval = interval;
  ohlcv.initDefaults(symbol, interval);
  await ohlcv.fetchRecent({ includeOpen: true, limit: 600 });
  ohlcv.fetchGaps();
  ohlcv.fetchMeta();
  ohlcv.startPolling();
}

onMounted(async () => {
  const symbol = autopilot.position?.symbol || 'XRPUSDT';
  const interval = ohlcv.interval || '1m';
  await bootstrap(symbol, interval);
});

watch(
  () => autopilot.position?.symbol,
  async (nextSymbol, prevSymbol) => {
    if (!nextSymbol || nextSymbol === prevSymbol) return;
    await bootstrap(nextSymbol, ohlcv.interval || '1m');
  }
);
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  color: #cbd7eb;
}
.title {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.panel-header h2 {
  margin: 0;
  font-size: 16px;
}
.hint {
  font-size: 12px;
  color: #8fa3c0;
}
.metrics {
  display: flex;
  gap: 12px;
  align-items: center;
  font-size: 12px;
  color: #9cb2d6;
}
.metric strong {
  color: #e5ecf5;
  font-weight: 600;
}
.panel-body {
  flex: 1;
  background: #101a2b;
  border-radius: 10px;
  border: 1px solid rgba(58, 78, 110, 0.45);
  padding: 8px;
}
</style>
