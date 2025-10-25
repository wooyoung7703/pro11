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
    <section ref="bodyEl" class="panel-body" :style="{ height: chartHeight + 'px' }" v-show="activeTab==='chart'">
      <TradingViewRealtimeChart
        :height="chartHeight"
        class="w-full h-full"
        style="display:block;"
        :initialBarSpacing="marketViewBarSpacing"
        :fitOnInit="false"
      />
    </section>

    <section v-show="activeTab==='baseline'" class="tab-body">
      <BaselineABView />
    </section>

    <section v-show="activeTab==='bocpd'" class="tab-body">
      <BocpdGuardView />
    </section>

    <nav class="bottom-tabs">
      <button :class="['tab', { active: activeTab==='chart' }]" @click="activeTab='chart'">차트</button>
      <button :class="['tab', { active: activeTab==='baseline' }]" @click="activeTab='baseline'">Baseline A/B</button>
      <button :class="['tab', { active: activeTab==='bocpd' }]" @click="activeTab='bocpd'">BOCPD 가드</button>
    </nav>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch, nextTick } from 'vue';
import TradingViewRealtimeChart from '@/components/ohlcv/TradingViewRealtimeChart.vue';
import BaselineABView from '@/components/autopilot/BaselineABView.vue';
import BocpdGuardView from '@/components/autopilot/BocpdGuardView.vue';
import { useOhlcvStore } from '@/stores/ohlcv';
import { useAutoTraderStore } from '@/stores/autoTrader';

const ohlcv = useOhlcvStore();
const autopilot = useAutoTraderStore();

// Responsive chart height (16:9) with viewport cap
const bodyEl = ref<HTMLElement | null>(null);
const bodyWidth = ref(0);
// Market view: define a default bar spacing that corresponds to ~100% zoom for our container densities
// Typical good starting value for 1m candles; adjust as needed per UI density.
const marketViewBarSpacing = 6; // pixels per bar
const chartHeight = computed(() => {
  const w = bodyWidth.value || 0;
  if (!w) return 320; // safe default
  const h = Math.round((w * 9) / 16);
  const vh = typeof window !== 'undefined' ? window.innerHeight : 0;
  const maxH = Math.min(520, vh ? Math.round(vh * 0.50) : 520); // cap at 50vh or 520px
  const minH = 180;
  return Math.max(minH, Math.min(maxH, h));
});
// No inner padding; chart uses full height of the panel body

// Bottom tabs state
const activeTab = ref<'chart'|'baseline'|'bocpd'>('chart');

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
  // measure after mount
  nextTick(() => {
    const measure = () => {
      if (bodyEl.value) bodyWidth.value = bodyEl.value.clientWidth || 0;
    };
    measure();
    // Observe size changes
    try {
      if (typeof window !== 'undefined' && 'ResizeObserver' in window) {
        const ro = new ResizeObserver(() => measure());
        (bodyEl as any)._ro = ro;
        if (bodyEl.value) ro.observe(bodyEl.value as Element);
      } else if (typeof window !== 'undefined') {
        (globalThis as any).addEventListener?.('resize', measure);
        (bodyEl as any)._resizeHandler = measure;
      }
    } catch { /* ignore */ }
  });
});

watch(
  () => autopilot.position?.symbol,
  async (nextSymbol: string | undefined, prevSymbol: string | undefined) => {
    if (!nextSymbol || nextSymbol === prevSymbol) return;
    await bootstrap(nextSymbol, ohlcv.interval || '1m');
  }
);

onBeforeUnmount(() => {
  // cleanup observers
  const anyEl = bodyEl as any;
  if (anyEl && anyEl._ro) {
    try { anyEl._ro.disconnect(); } catch { /* ignore */ }
    anyEl._ro = null;
  }
  if (typeof window !== 'undefined' && anyEl && anyEl._resizeHandler) {
    try { (globalThis as any).removeEventListener?.('resize', anyEl._resizeHandler); } catch { /* ignore */ }
    anyEl._resizeHandler = null;
  }
});
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  gap: 8px; /* tighter spacing between header and chart */
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
  /* Remove flex grow to avoid forced tall panel; height is controlled by style binding */
  position: relative;
  overflow: hidden;
  background: #101a2b;
  border-radius: 10px;
  border: none;
  padding: 0;
}

.tab-body {
  position: relative;
  overflow: auto;
  background: #0f172a;
  border-radius: 10px;
  border: 1px solid #193052;
  padding: 12px;
  min-height: 140px;
}

.bottom-tabs {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}

.bottom-tabs .tab {
  appearance: none;
  border: 1px solid #1e2a44;
  background: #0b1322;
  color: #cbd7eb;
  padding: 6px 10px;
  border-radius: 8px;
  font-size: 12px;
  cursor: pointer;
}
.bottom-tabs .tab.active {
  background: #14213a;
  border-color: #2a3b61;
}
</style>
