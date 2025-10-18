<template>
  <div class="ohlcv-chart">
    <header class="chart-header">
      <div class="pair">{{ symbolLabel }}</div>
      <div class="timeframe">1분 봉</div>
      <div class="status" :class="priceColorClass">{{ lastPriceLabel }}</div>
    </header>
    <div ref="container" class="chart-surface"></div>
    <footer class="chart-footer">
      <div class="follow">실시간 추적 고정</div>
      <div class="detached">과거 데이터는 스크롤로 확인하세요</div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { createChart, type ISeriesApi, type Time, type UTCTimestamp, LineStyle } from 'lightweight-charts';
import { useOhlcvStore } from '../../stores/ohlcv';

interface CandlePoint {
  time: UTCTimestamp;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  isClosed?: boolean;
}

interface Props {
  width?: number;
  height?: number;
}

const SMA_PERIOD = 20;
const EMA_PERIOD = 50;

const props = withDefaults(defineProps<Props>(), {
  width: undefined,
  height: undefined,
});

const store = useOhlcvStore();
const container = ref<HTMLDivElement | null>(null);
let chart: ReturnType<typeof createChart> | null = null;
let candleSeries: ISeriesApi<'Candlestick'> | null = null;
let smaSeries: ISeriesApi<'Line'> | null = null;
let emaSeries: ISeriesApi<'Line'> | null = null;
let resizeObserver: ResizeObserver | null = null;
type VisibleRange = { from: Time; to: Time } | null;

let timeRangeHandler: ((range: VisibleRange) => void) | null = null;

let candleCache: CandlePoint[] = [];
let smaCache: { time: UTCTimestamp; value: number }[] = [];
let emaCache: { time: UTCTimestamp; value: number }[] = [];

const symbolLabel = computed(() => store.symbol || '—');
const lastCandle = computed(() => store.candles.at(-1) ?? null);
const previousClose = computed(() => (store.candles.length > 1 ? store.candles.at(-2)?.close ?? null : null));
const lastPriceLabel = computed(() => {
  const price = lastCandle.value?.close;
  if(price == null) return '—';
  if(price >= 100) return price.toFixed(2);
  if(price >= 1) return price.toFixed(3);
  return price.toPrecision(3);
});
const priceColorClass = computed(() => {
  const current = lastCandle.value?.close;
  const prev = previousClose.value;
  if(current == null || prev == null) return 'flat';
  if(current > prev) return 'up';
  if(current < prev) return 'down';
  return 'flat';
});

function toPoint(sample: typeof store.candles[number]): CandlePoint {
  return {
    time: Math.floor(sample.open_time / 1000) as UTCTimestamp,
    open: sample.open,
    high: sample.high,
    low: sample.low,
    close: sample.close,
    volume: sample.volume,
    isClosed: sample.is_closed,
  };
}

function buildSmaPoints(period: number, source: CandlePoint[]): { time: UTCTimestamp; value: number }[] {
  if(period <= 1 || source.length === 0) return [];
  const result: { time: UTCTimestamp; value: number }[] = [];
  const window: number[] = [];
  let sum = 0;
  for(const candle of source){
    window.push(candle.close);
    sum += candle.close;
    if(window.length > period){
      sum -= window.shift()!;
    }
    result.push({ time: candle.time, value: sum / window.length });
  }
  return result;
}

function buildEmaPoints(period: number, source: CandlePoint[]): { time: UTCTimestamp; value: number }[] {
  if(period <= 1 || source.length === 0) return [];
  const alpha = 2 / (period + 1);
  const result: { time: UTCTimestamp; value: number }[] = [];
  let ema = source[0].close;
  for(const candle of source){
    ema = candle.close * alpha + ema * (1 - alpha);
    result.push({ time: candle.time, value: ema });
  }
  return result;
}

function timeToSeconds(value: Time | null | undefined): number | null {
  if(value == null) return null;
  if(typeof value === 'number') return value;
  if(typeof value === 'string'){
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  const date = Date.UTC(value.year, value.month - 1, value.day);
  return Math.floor(date / 1000);
}

function refreshIndicators(){
  smaCache = buildSmaPoints(SMA_PERIOD, candleCache);
  emaCache = buildEmaPoints(EMA_PERIOD, candleCache);
  smaSeries?.setData(smaCache as any);
  emaSeries?.setData(emaCache as any);
}

function applyFullDataset(points: CandlePoint []){
  candleCache = points.slice();
  candleSeries?.setData(candleCache as any);
  refreshIndicators();
}

async function maybeLoadOlder(range: VisibleRange){
  if(!chart || !range || !store.candles.length) return;
  const firstVisible = timeToSeconds(range.from);
  if(firstVisible == null) return;
  const earliest = store.candles[0]?.open_time;
  if(!earliest) return;
  const earliestSec = Math.floor(earliest / 1000);
  let intervalSec = 60;
  if(store.candles.length >= 2){
    const a = store.candles[0].open_time;
    const b = store.candles[1].open_time;
    intervalSec = Math.max(1, Math.round((b - a) / 1000));
  }
  if(firstVisible > earliestSec + intervalSec * 2) return;

  const ts = chart.timeScale();
  const prevRange = ts.getVisibleLogicalRange();
  const loaded = await store.fetchOlder(earliest, 600);
  if(loaded <= 0) return;

  const points = store.candles.map(toPoint);
  candleCache = points.slice();
  candleSeries?.setData(candleCache as any);
  refreshIndicators();

  if(prevRange && typeof prevRange.from === 'number' && typeof prevRange.to === 'number'){
    ts.setVisibleLogicalRange({ from: prevRange.from + loaded, to: prevRange.to + loaded });
  }
}

function handleRealtimeUpdate(points: CandlePoint []){
  if(!candleSeries){
    candleCache = points.slice();
    return;
  }
  const nextLen = points.length;
  const prevLen = candleCache.length;

  if(nextLen === 0){
    candleCache = [];
    candleSeries.setData([] as any);
    smaSeries?.setData([] as any);
    emaSeries?.setData([] as any);
    smaCache = [];
    emaCache = [];
    return;
  }

  if(prevLen === 0){
    applyFullDataset(points);
    return;
  }

  if(nextLen < prevLen - 1 || nextLen > prevLen + 2){
    applyFullDataset(points);
    return;
  }

  const prevLast = candleCache[prevLen - 1];
  const nextLast = points[nextLen - 1];

  if(nextLen === prevLen){
    if(prevLast.time === nextLast.time){
      candleCache[prevLen - 1] = nextLast;
      candleSeries.update(nextLast as any);
    } else {
      applyFullDataset(points);
      return;
    }
  } else if(nextLen === prevLen + 1){
    const prevNext = points[nextLen - 2];
    if(prevNext && prevLast.time === prevNext.time){
      candleCache[prevLen - 1] = prevNext;
      candleSeries.update(prevNext as any);
    }
    candleCache.push(nextLast);
    candleSeries.update(nextLast as any);
  } else {
    applyFullDataset(points);
    return;
  }

  refreshIndicators();
}

watch(
  () => store.candles,
  (candles) => {
    const points = candles.map(toPoint);
    handleRealtimeUpdate(points);
  },
  { deep: true }
);

onMounted(() => {
  if(!container.value) return;
  chart = createChart(container.value, {
    layout: {
      background: { color: '#0d1117' },
      textColor: '#c9d1d9',
    },
    rightPriceScale: {
      borderColor: '#21262d',
    },
    timeScale: {
      borderColor: '#21262d',
      timeVisible: true,
      rightBarStaysOnScroll: true,
      shiftVisibleRangeOnNewBar: false,
      secondsVisible: false,
    },
    grid: {
      vertLines: { color: '#21262d' },
      horzLines: { color: '#21262d' },
    },
    crosshair: { mode: 0 },
    width: props.width ?? container.value.clientWidth,
    height: props.height ?? container.value.clientHeight,
  });

  candleSeries = chart.addCandlestickSeries({
    upColor: '#2ecc71',
    downColor: '#e74c3c',
    borderVisible: false,
    wickUpColor: '#2ecc71',
    wickDownColor: '#e74c3c',
  });
  smaSeries = chart.addLineSeries({ color: '#f4d35e', lineWidth: 2, priceLineVisible: false });
  emaSeries = chart.addLineSeries({ color: '#4ea1ff', lineWidth: 2, priceLineVisible: false, lineStyle: LineStyle.Solid });

  const initialPoints = store.candles.map(toPoint);
  applyFullDataset(initialPoints);

  resizeObserver = new ResizeObserver(() => {
    if(!chart || !container.value) return;
    chart.applyOptions({
      width: props.width ?? container.value.clientWidth,
      height: props.height ?? container.value.clientHeight,
    });
  });
  resizeObserver.observe(container.value);

  timeRangeHandler = (range) => {
    if(range) {
      maybeLoadOlder(range);
    }
  };
  chart.timeScale().subscribeVisibleTimeRangeChange(timeRangeHandler);
});

onBeforeUnmount(() => {
  if(resizeObserver && container.value){
    resizeObserver.unobserve(container.value);
    resizeObserver.disconnect();
  }
  resizeObserver = null;
  if(chart && timeRangeHandler){
    chart.timeScale().unsubscribeVisibleTimeRangeChange(timeRangeHandler);
  }
  timeRangeHandler = null;
  chart?.remove();
  chart = null;
  candleSeries = null;
  smaSeries = null;
  emaSeries = null;
  candleCache = [];
  smaCache = [];
  emaCache = [];
});
</script>

<style scoped>
.ohlcv-chart {
  display: flex;
  flex-direction: column;
  background: #0d1117;
  border: 1px solid #1c2330;
  border-radius: 12px;
  height: 100%;
  min-height: 100%;
}

.chart-header,
.chart-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  font-size: 13px;
  color: #8b949e;
}

.chart-header {
  border-bottom: 1px solid #1c2330;
}

.chart-footer {
  border-top: 1px solid #1c2330;
  font-size: 12px;
}

.chart-header .pair {
  font-size: 15px;
  font-weight: 600;
  color: #f0f6fc;
}

.chart-header .timeframe {
  font-size: 12px;
  color: #8b949e;
}

.chart-header .status {
  font-family: 'JetBrains Mono', 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace;
  font-weight: 500;
  padding: 2px 6px;
  border-radius: 6px;
  background: #161b22;
}

.chart-header .status.up {
  color: #2ecc71;
}

.chart-header .status.down {
  color: #ff7b72;
}

.chart-header .status.flat {
  color: #e6edf3;
}

.chart-surface {
  flex: 1 1 auto;
  min-height: 320px;
  width: 100%;
  position: relative;
}

.chart-footer .follow {
  color: #42b983;
}

.chart-footer .detached {
  color: #ffdf5d;
}
</style>
