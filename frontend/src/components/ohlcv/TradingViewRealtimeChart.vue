<template>
  <div class="realtime-chart" :class="{ chromeless }">
    <header v-if="!chromeless" class="chart-header">
      <div class="symbol">{{ symbolLabel }}</div>
      <div class="interval">1m</div>
      <div class="status" :class="priceDirection">{{ lastPriceText }}</div>
    </header>
    <div ref="container" class="chart-surface"></div>
  </div>
  
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { createChart, type ISeriesApi, type SeriesMarker, type UTCTimestamp, type Time } from 'lightweight-charts';
import http from '@/lib/http';
import { useOhlcvStore, type Candle } from '@/stores/ohlcv';
import { useBinanceKline } from '@/composables/useBinanceKline';
import { useAutoTraderStore } from '@/stores/autoTrader';
import type { AutopilotEvent, AutopilotSignal } from '@/types/autopilot';

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
  chromeless?: boolean; // hide header and remove borders/background
  // When provided, chart will use this bar spacing on init (treat as "100% zoom")
  initialBarSpacing?: number | null;
  // If false, skip fitContent on initial data sync to preserve bar spacing/zoom
  fitOnInit?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  width: undefined,
  height: 420,
  chromeless: true,
  initialBarSpacing: null,
  fitOnInit: true,
});

const store = useOhlcvStore();
const autopilot = useAutoTraderStore();
const container = ref<HTMLDivElement | null>(null);
const symbolRef = computed(() => store.symbol || '');
const { latest: binanceKline, connect: connectBinance, disconnect: disconnectBinance } = useBinanceKline(symbolRef, '1m');

let chart: ReturnType<typeof createChart> | null = null;
let candleSeries: ISeriesApi<'Candlestick'> | null = null;
let resizeObserver: ResizeObserver | null = null;
let timeRangeHandler: ((range: { from: Time; to: Time } | null) => void) | null = null;
let signalMarkers: SeriesMarker<Time>[] = [];
let orderMarkers: SeriesMarker<Time>[] = [];

const HISTORY_CHUNK = 1200; // larger history batch to minimize repeated fetches during manual scroll
let loadingOlder = false;
const FALLBACK_INTERVAL_SECONDS = 60;
let lastHistoryRequest: number | null = null;
let initialHistoryPrefetchDone = false;

let cache: CandlePoint[] = [];

async function loadInitialFromBinance(limit = 200) {
  const symbol = (store.symbol || 'XRPUSDT').toUpperCase();
  try {
    const url = `https://api.binance.com/api/v3/klines?symbol=${encodeURIComponent(symbol)}&interval=1m&limit=${limit}`;
    const resp = await fetch(url);
    if(!resp.ok) return;
    const data = await resp.json();
    if(!Array.isArray(data)) return;
    const candles: Candle[] = data.map((row: any[]) => ({
      open_time: Number(row[0]),
      open: Number(row[1]),
      high: Number(row[2]),
      low: Number(row[3]),
      close: Number(row[4]),
      volume: Number(row[5]),
      close_time: Number(row[6]),
      trade_count: Number(row[8] ?? 0),
      is_closed: true,
    }));
    candles.sort((a, b) => a.open_time - b.open_time);
    const points = candles.map((candle) => ({
      time: Math.floor(candle.open_time / 1000) as UTCTimestamp,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      volume: candle.volume,
      isClosed: candle.is_closed,
    }));
    syncSeries(points, { fit: true });
    store.$patch({ candles });
  } catch(_err) {
    // ignore fetch failures, backend snapshot will still load
  }
}

const symbolLabel = computed(() => store.symbol || '—');
const lastCandle = computed(() => store.candles.at(-1) ?? null);
const prevClose = computed(() => (store.candles.length > 1 ? store.candles.at(-2)?.close ?? null : null));
const lastPriceText = computed(() => {
  const price = lastCandle.value?.close;
  if(price == null) return '—';
  if(price >= 100) return price.toFixed(2);
  if(price >= 1) return price.toFixed(3);
  return price.toPrecision(3);
});
const priceDirection = computed(() => {
  const price = lastCandle.value?.close;
  const prev = prevClose.value;
  if(price == null || prev == null) return 'flat';
  if(price > prev) return 'up';
  if(price < prev) return 'down';
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

function syncSeries(points: CandlePoint[], options: { fit?: boolean } = {}) {
  const { fit = false } = options;
  // Enforce strict ascending order and dedupe by time to satisfy lightweight-charts
  const normalized = (() => {
    if (!Array.isArray(points) || points.length === 0) return [] as CandlePoint[];
    const sorted = points.slice().sort((a, b) => (a.time as number) - (b.time as number));
    const out: CandlePoint[] = [];
    for (const p of sorted) {
      const last = out[out.length - 1];
      if (!last) { out.push(p); continue; }
      if (p.time === last.time) {
        // replace with the newer sample for the same time bucket
        out[out.length - 1] = p;
      } else if ((p.time as number) > (last.time as number)) {
        out.push(p);
      }
      // values < last.time are ignored by ascending pass
    }
    return out;
  })();
  cache = normalized;
  candleSeries?.setData(cache as any);
  if(fit && props.fitOnInit) {
    chart?.timeScale().fitContent();
  }
}

function replaceAllCandles(points: CandlePoint[]) {
  syncSeries(points);
}

// No sliding-window diffing; rely on full sync when dataset boundaries change to keep data consistent.

function upsertFromStore(candles: typeof store.candles) {
  if(!candleSeries) return;
  if(candles.length === 0) {
    cache = [];
    candleSeries.setData([] as any);
    return;
  }

  const latest = candles.map(toPoint);
  // normalize to avoid descending time assertion
  latest.sort((a,b)=> (a.time as number) - (b.time as number));
  // collapse duplicates while preserving the last occurrence
  const latestNorm: CandlePoint[] = [];
  for (const p of latest) {
    const last = latestNorm[latestNorm.length-1];
    if (!last) { latestNorm.push(p); continue; }
    if (p.time === last.time) latestNorm[latestNorm.length-1] = p; else if ((p.time as number) > (last.time as number)) latestNorm.push(p);
  }
  const shouldFit = cache.length === 0;
  if(cache.length === 0 || latestNorm.length === 0) {
    replaceAllCandles(latestNorm);
    if(shouldFit && props.fitOnInit) {
      chart?.timeScale().fitContent();
    }
    return;
  }

  const currentFirst = cache[0]?.time;
  const latestFirst = latestNorm[0]?.time;
  if(currentFirst == null || latestFirst == null) {
    replaceAllCandles(latestNorm);
    if(shouldFit && props.fitOnInit) {
      chart?.timeScale().fitContent();
    }
    return;
  }

  // If new history prepended or dataset shrank, perform full sync.
  if(latestFirst !== currentFirst) {
    replaceAllCandles(latestNorm);
    if(shouldFit && props.fitOnInit) {
      chart?.timeScale().fitContent();
    }
    return;
  }

  if(latestNorm.length < cache.length) {
    const lastPoint = latestNorm.at(-1);
    const cachedLast = cache.at(-1);
    if(lastPoint && cachedLast && (
      lastPoint.time !== cachedLast.time ||
      lastPoint.open !== cachedLast.open ||
      lastPoint.high !== cachedLast.high ||
      lastPoint.low !== cachedLast.low ||
      lastPoint.close !== cachedLast.close
    )) {
      cache[cache.length - 1] = lastPoint;
      candleSeries?.update(lastPoint as any);
    }
    return;
  }

  // Append any newly fetched candles to the right without resetting view.
  if(latestNorm.length > cache.length) {
    const extra = latestNorm.slice(cache.length);
    for(const point of extra) {
      cache.push(point);
      candleSeries?.update(point as any);
    }
    return;
  }

  // Update trailing candle in-place when values change (e.g., live candle).
  const lastPoint = latestNorm.at(-1);
  const cachedLast = cache.at(-1);
  if(lastPoint && cachedLast && (
    lastPoint.time !== cachedLast.time ||
    lastPoint.open !== cachedLast.open ||
    lastPoint.high !== cachedLast.high ||
    lastPoint.low !== cachedLast.low ||
    lastPoint.close !== cachedLast.close
  )) {
    cache[cache.length - 1] = lastPoint;
    candleSeries?.update(lastPoint as any);
    return;
  }
}

function applyRealtimePoint(point: CandlePoint) {
  if(!candleSeries) return;
  const last = cache.at(-1);
  if(last && last.time === point.time) {
    cache[cache.length - 1] = point;
    candleSeries.update(point as any);
    return;
  }
  if(!last || point.time > last.time) {
    cache.push(point);
    candleSeries.update(point as any);
    return;
  }
  // Fallback: dataset drifted (e.g., history fetch). Re-sync.
  syncSeries(store.candles.map(toPoint));
}

function applyUpdateToStore(update: CandlePoint & { closeTime: number; volume?: number; tradeCount?: number; isFinal?: boolean }) {
  const openMs = update.time * 1000;
  const next: Candle = {
    open_time: openMs,
    close_time: update.closeTime,
    open: update.open,
    high: update.high,
    low: update.low,
    close: update.close,
    volume: update.volume,
    trade_count: update.tradeCount,
    is_closed: update.isFinal,
  };
  const existingIdx = store.candles.findIndex((c) => c.open_time === openMs);
  if(existingIdx >= 0) {
    store.candles.splice(existingIdx, 1, next);
  } else {
    store.candles.push(next);
    store.candles.sort((a, b) => a.open_time - b.open_time);
  }
}

function handleBinanceUpdate() {
  const update = binanceKline.value;
  if(!update) return;
  const lastStoreCandle = store.candles.at(-1);
  const updateOpenSec = Math.floor(update.openTime / 1000);
  if(lastStoreCandle){
    const lastOpenSec = Math.floor(lastStoreCandle.open_time / 1000);
    const intervalSec = inferIntervalSeconds();
    if(updateOpenSec - lastOpenSec > intervalSec){
      store.fetchRecent({ includeOpen: true }).catch(() => {/* swallow errors */});
    }
  }
  const point: CandlePoint & { closeTime: number; tradeCount?: number; isFinal?: boolean } = {
    time: Math.floor(update.openTime / 1000) as UTCTimestamp,
    open: update.open,
    high: update.high,
    low: update.low,
    close: update.close,
    volume: update.volume,
    isClosed: update.isFinal,
    closeTime: update.closeTime,
    tradeCount: update.tradeCount,
    isFinal: update.isFinal,
  };
  applyRealtimePoint(point);
  applyUpdateToStore(point);
}

function timeToSeconds(value: Time | null | undefined): number | null {
  if(value == null) return null;
  if(typeof value === 'number') return value;
  if(typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  const utc = Date.UTC(value.year, value.month - 1, value.day);
  return Math.floor(utc / 1000);
}

async function maybeLoadOlder(range: { from: Time; to: Time } | null){
  if(!chart || loadingOlder || !range) return;
  const fromSeconds = timeToSeconds(range.from);
  if(fromSeconds == null) return;
  const earliest = store.candles[0]?.open_time;
  if(!earliest) return;
  if(lastHistoryRequest !== null && lastHistoryRequest === earliest) return;
  const earliestSec = Math.floor(earliest / 1000);
  const intervalSec = inferIntervalSeconds();
  if(fromSeconds > earliestSec + intervalSec * 2) return;

  loadingOlder = true;
  lastHistoryRequest = earliest;
  let loadedCount = 0;
  try {
  const timeScale = chart.timeScale();
  const prevRange = timeScale.getVisibleLogicalRange();
  loadedCount = await store.fetchOlder(earliest, HISTORY_CHUNK);
    if(loadedCount && loadedCount > 0){
      const updated = store.candles.map(toPoint);
      syncSeries(updated, { fit: false });
      if(prevRange && typeof prevRange.from === 'number' && typeof prevRange.to === 'number'){
        timeScale.setVisibleLogicalRange({
          from: prevRange.from + loadedCount,
          to: prevRange.to + loadedCount,
        });
      }
      const newEarliest = store.candles[0]?.open_time ?? null;
      if(newEarliest != null) {
        lastHistoryRequest = null;
      }
    }
  } finally {
    if(loadedCount > 0) {
      lastHistoryRequest = null;
    }
    loadingOlder = false;
  }
}

function inferIntervalSeconds(): number {
  const len = cache.length;
  if(len >= 2) {
    const diff = cache[len - 1].time - cache[len - 2].time;
    if(Number.isFinite(diff) && diff > 0) {
      return diff;
    }
  }
  const storeLen = store.candles.length;
  if(storeLen >= 2) {
    const diff = Math.floor(store.candles[storeLen - 1].open_time / 1000) - Math.floor(store.candles[storeLen - 2].open_time / 1000);
    if(Number.isFinite(diff) && diff > 0) {
      return diff;
    }
  }
  return FALLBACK_INTERVAL_SECONDS;
}

function asFiniteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function nearestCandleTime(tsSec: number): UTCTimestamp | null {
  if(!Number.isFinite(tsSec) || !store.candles.length) return null;
  let best: number | null = null;
  let bestDiff = Number.POSITIVE_INFINITY;
  for(const candle of store.candles){
    const openSec = Math.floor(candle.open_time / 1000);
    const diff = Math.abs(openSec - tsSec);
    if(diff < bestDiff){
      bestDiff = diff;
      best = openSec;
    }
  }
  if(best == null) return null;
  const tolerance = inferIntervalSeconds() * 2;
  return bestDiff > tolerance ? null : (best as UTCTimestamp);
}

function buildMarkerForSignal(signal: AutopilotSignal | null): SeriesMarker<Time> | null {
  if(!signal) return null;

  const extra = (signal.extra ?? {}) as Record<string, unknown>;
  const evaluation = (extra?.evaluation ?? {}) as Record<string, unknown>;
  const orderResponse = (evaluation?.order_response ?? {}) as Record<string, unknown>;
  const order = (orderResponse?.order ?? {}) as Record<string, unknown>;
  const metrics = (extra?.metrics ?? {}) as Record<string, unknown>;

  let ts = asFiniteNumber(order.filled_ts)
    ?? asFiniteNumber(order.created_ts)
    ?? asFiniteNumber(metrics.evaluated_at)
    ?? asFiniteNumber(signal.emitted_ts);
  if(ts == null) return null;
  // If timestamp seems to be in milliseconds, convert to seconds
  if (ts > 1e11) ts = Math.floor(ts / 1000);
  const anchor = nearestCandleTime(Math.floor(ts));
  if(anchor == null) return null;

  const sideRaw = typeof order.side === 'string' ? order.side.toLowerCase() : '';
  const isSell = sideRaw === 'sell';
  const price = asFiniteNumber(order.price) ?? asFiniteNumber(metrics.current_price);
  const priceText = price == null ? '' : (price >= 1 ? price.toFixed(3) : price.toPrecision(3));
  const label = priceText ? `${signal.kind.toUpperCase()} ${priceText}` : signal.kind.toUpperCase();

  return {
    time: anchor,
    position: isSell ? 'aboveBar' : 'belowBar',
    color: isSell ? '#f87171' : '#34d399',
    shape: isSell ? 'arrowDown' : 'arrowUp',
    text: label,
  } satisfies SeriesMarker<Time>;
}

function applySignalMarkers() {
  if(!candleSeries) return;
  // Only display real order markers; hide signal markers entirely per request.
  const out: SeriesMarker<Time>[] = [];
  const seen = new Set<string>();
  const pushUniq = (m: SeriesMarker<Time>) => {
    const key = `${String(m.time)}|${m.position}|${m.shape}|${m.text ?? ''}`;
    if(seen.has(key)) return;
    seen.add(key);
    out.push(m);
  };
  for(const m of orderMarkers) pushUniq(m);
  candleSeries.setMarkers(out);
}

function extractSignalFromEvent(event: AutopilotEvent | null | undefined): AutopilotSignal | null {
  if(!event || event.type !== 'signal') return null;
  const payload = event.payload as Record<string, unknown> | undefined;
  const raw = payload?.signal;
  if(raw && typeof raw === 'object') {
    return raw as AutopilotSignal;
  }
  return null;
}

function rebuildSignalMarkers() {
  const markers: SeriesMarker<Time>[] = [];
  const seen = new Set<string>();
  // Only show low-buy style signals, hide ML-only markers (e.g., ml_buy)
  const allowedKinds = new Set<string>(['buy', 'low_buy']);
  const addMarker = (signal: AutopilotSignal | null) => {
    if(!signal) return;
    try {
      const kind = String((signal as any).kind || '').toLowerCase();
      if(kind && !allowedKinds.has(kind)) return; // skip non low-buy kinds (e.g., ml_buy)
    } catch { /* ignore */ }
    const marker = buildMarkerForSignal(signal);
    if(!marker) return;
    const extra = (signal.extra ?? {}) as Record<string, unknown>;
    const sigId = typeof extra.signal_id === 'number' ? extra.signal_id : null;
    const key = sigId != null ? `id:${sigId}` : `${String(marker.time)}:${marker.position}:${marker.text ?? ''}`;
    if(seen.has(key)) return;
    seen.add(key);
    markers.push(marker);
  };
  const historySnapshot = Array.isArray(autopilot.historicalSignals) ? autopilot.historicalSignals.slice() : [];
  for(const signal of historySnapshot) addMarker(signal);
  const eventsSnapshot = autopilot.events.slice();
  for(const evt of eventsSnapshot) addMarker(extractSignalFromEvent(evt as AutopilotEvent));
  addMarker(autopilot.activeSignal ?? null);
  signalMarkers = markers;
  applySignalMarkers();
}

type OrderRow = { side: string; price: number; created_ts?: number | null; filled_ts?: number | null; reason?: string | null };

function buildMarkerForOrder(order: OrderRow | null): SeriesMarker<Time> | null {
  if(!order) return null;
  const sideRaw = String(order.side || '').toLowerCase();
  if(sideRaw !== 'buy' && sideRaw !== 'sell') return null;
  let ts = asFiniteNumber(order.filled_ts) ?? asFiniteNumber(order.created_ts);
  if(ts == null) return null;
  if(ts > 1e11) ts = Math.floor(ts / 1000);
  const anchor = nearestCandleTime(Math.floor(ts));
  if(anchor == null) return null;
  const price = asFiniteNumber(order.price);
  const priceText = price == null ? '' : (price >= 1 ? price.toFixed(3) : price.toPrecision(3));
  // Determine label based on order.reason for buys
  let textLabel: string;
  if (sideRaw === 'sell') {
    textLabel = 'SELL';
  } else {
    const reasonRaw = typeof order.reason === 'string' ? order.reason.toLowerCase() : '';
    // Map well-known reasons to requested labels
    if (reasonRaw.includes('scale_in')) {
      textLabel = 'scale_buy';
    } else if (reasonRaw.includes('low_buy')) {
      textLabel = 'low_buy';
    } else if (reasonRaw.includes('ml_buy')) {
      textLabel = 'ml_buy';
    } else if (reasonRaw.includes('scale')) {
      textLabel = 'scale_buy';
    } else {
      textLabel = 'BUY';
    }
  }
  return {
    time: anchor,
    position: sideRaw === 'sell' ? 'aboveBar' : 'belowBar',
    color: sideRaw === 'sell' ? '#fca5a5' : '#86efac',
    shape: sideRaw === 'sell' ? 'arrowDown' : 'arrowUp',
    text: textLabel + (priceText ? ` ${priceText}` : ''),
  } satisfies SeriesMarker<Time>;
}

async function loadOrderMarkers(limit: number = 200) {
  try {
    const sym = (store.symbol || '').toUpperCase() || undefined;
    const r = await http.get('/api/trading/orders', { params: { limit, source: 'db', symbol: sym, since_reset: true } });
    const rows = Array.isArray(r.data?.orders) ? (r.data.orders as OrderRow[]) : [];
    const markers: SeriesMarker<Time>[] = [];
    for(const row of rows){
      const m = buildMarkerForOrder(row);
      if(m) markers.push(m);
    }
    orderMarkers = markers;
    applySignalMarkers();
  } catch {
    // ignore fetch errors
  }
}

watch(
  () => store.candles.slice(),
  (candles: Candle[]) => {
    upsertFromStore(candles);
    if(!initialHistoryPrefetchDone && candles.length > 0) {
      initialHistoryPrefetchDone = true;
      const earliest = candles[0]?.open_time;
      if(earliest) {
        // Prefetch one additional chunk so older history is immediately available without manual scroll.
  store.fetchOlder(earliest, HISTORY_CHUNK).catch(() => {
          // If the prefetch fails we leave the flag set to avoid tight retry loops; manual scroll will still load history.
        });
      }
    }
    if(autopilot.events.length || autopilot.activeSignal || autopilot.historicalSignals.length) {
      rebuildSignalMarkers();
    } else if(signalMarkers.length) {
      applySignalMarkers();
    }
  },
  { deep: true }
);

watch(binanceKline, () => {
  handleBinanceUpdate();
});

watch(
  () => autopilot.events.slice(),
  () => {
    rebuildSignalMarkers();
  },
  { deep: true, immediate: true }
);

watch(
  () => autopilot.activeSignal,
  () => {
    rebuildSignalMarkers();
  }
);

watch(
  () => autopilot.historicalSignals.slice(),
  () => {
    rebuildSignalMarkers();
  },
  { deep: true, immediate: true }
);

onMounted(() => {
  if(!container.value) return;

  if(!store.interval) {
    store.initDefaults(store.symbol || 'XRPUSDT', '1m');
  }
  store.interval = '1m';

  autopilot.fetchSignalHistory({ limit: 200, signalType: 'ml_buy' }).catch(() => {
    // suppress init failure; stream will still supply live signals
  });

  chart = createChart(container.value, {
    layout: {
      background: { color: props.chromeless ? 'transparent' : '#0d1117' },
      textColor: '#c9d1d9',
    },
    rightPriceScale: {
      borderColor: '#21262d',
    },
    timeScale: {
      borderColor: '#21262d',
      timeVisible: true,
      rightBarStaysOnScroll: false,
      shiftVisibleRangeOnNewBar: false,
      secondsVisible: false,
    },
    grid: {
      vertLines: { color: '#1c2330' },
      horzLines: { color: '#1c2330' },
    },
    crosshair: { mode: 0 },
    width: props.width ?? container.value.clientWidth,
    height: props.height ?? container.value.clientHeight,
  });

  candleSeries = chart.addCandlestickSeries({
    upColor: '#2ecc71',
    downColor: '#e74c3c',
    wickUpColor: '#2ecc71',
    wickDownColor: '#e74c3c',
    borderVisible: false,
  });

  if(store.candles.length) {
    upsertFromStore(store.candles);
  } else {
    (async () => { try { await loadInitialFromBinance(); } catch { /* ignore */ } finally { store.fetchRecent({ includeOpen: true }).catch(() => {/* handled by store */}); } })();
  }

  // Apply initial zoom preferences for Market View
  try {
    const ts = chart.timeScale();
    if (typeof props.initialBarSpacing === 'number' && Number.isFinite(props.initialBarSpacing) && props.initialBarSpacing > 0) {
      ts.applyOptions({ barSpacing: props.initialBarSpacing });
    }
    // Ensure we start at the rightmost (real-time) end of the series
    ts.scrollToRealTime();
  } catch { /* ignore */ }

  resizeObserver = new ResizeObserver(() => {
    if(!chart || !container.value) return;
    chart.applyOptions({
      width: props.width ?? container.value.clientWidth,
      height: props.height ?? container.value.clientHeight,
    });
  });
  resizeObserver.observe(container.value);

  timeRangeHandler = (range: { from: Time; to: Time } | null) => {
    maybeLoadOlder(range);
  };
  chart.timeScale().subscribeVisibleTimeRangeChange(timeRangeHandler);

  connectBinance();
  applySignalMarkers();
  // initial order markers
  loadOrderMarkers().catch(() => {});
});

onBeforeUnmount(() => {
  disconnectBinance();

  if(candleSeries) {
    candleSeries.setMarkers([]);
  }

  if(resizeObserver && container.value) {
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
  cache = [];
  lastHistoryRequest = null;
  initialHistoryPrefetchDone = false;
  signalMarkers = [];
});
</script>

<style scoped>
.realtime-chart {
  display: flex;
  flex-direction: column;
  min-height: 200px;
  height: 100%;
}
.realtime-chart.chromeless {
  background: transparent;
  border: none;
  border-radius: 0;
}

.chart-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  font-size: 13px;
  color: #8b949e;
  border-bottom: 1px solid #1c2330;
}
.realtime-chart.chromeless .chart-header { display: none; }

.chart-header .symbol {
  font-size: 15px;
  font-weight: 600;
  color: #f0f6fc;
}

.chart-header .interval {
  font-size: 12px;
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
  min-height: 160px;
  height: 100%;
  width: 100%;
  position: relative;
}
</style>
