<template>
  <div class="lw-chart-wrapper">
    <div ref="container" class="chart-container"></div>
    <div class="legend" v-if="lastPrice!=null">
      <span class="sym">XRP</span>
      <span class="price">{{ fmt(lastPrice) }}</span>
      <span v-if="smaValue!=null" class="sma">SMA{{ smaPeriod }} {{ fmt(smaValue) }}</span>
      <span v-if="emaValue!=null" class="ema">EMA{{ emaPeriod }} {{ fmt(emaValue) }}</span>
      <span v-if="partialActive" class="partial">PARTIAL</span>
    </div>
    <div v-if="tooltip.visible" class="hover-tooltip" :style="tooltip.style">
      <div class="row"><span>T</span><b>{{ tooltip.point?.time || '-' }}</b></div>
      <div class="row"><span>O</span><b>{{ fmt(tooltip.point?.open ?? null) }}</b></div>
      <div class="row"><span>H</span><b>{{ fmt(tooltip.point?.high ?? null) }}</b></div>
      <div class="row"><span>L</span><b>{{ fmt(tooltip.point?.low ?? null) }}</b></div>
      <div class="row"><span>C</span><b>{{ fmt(tooltip.point?.close ?? null) }}</b></div>
      <div v-if="tooltip.sma!=null" class="row"><span>SMA</span><b>{{ fmt(tooltip.sma) }}</b></div>
      <div v-if="tooltip.ema!=null" class="row"><span>EMA</span><b>{{ fmt(tooltip.ema) }}</b></div>
      <div v-if="tooltip.partial" class="row partial">PARTIAL</div>
    </div>
    <div class="gap-overlay" v-if="gapRects.length">
      <div v-for="g in gapRects" :key="g.key" class="gap-box" :class="g.state" :style="g.style">
        <span v-if="g.w >= 46">{{ g.missing }}ms {{ g.state==='open'? 'OPEN':'OK' }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { createChart, ISeriesApi, UTCTimestamp, LineStyle } from 'lightweight-charts';
import { useOhlcvStore } from '../../stores/ohlcv';
import { createSMA, smaAppend } from '../../lib/indicators/sma';
import { createEMA, emaAppend } from '../../lib/indicators/ema';

interface CandleInput { time: UTCTimestamp; open:number; high:number; low:number; close:number; volume?:number; is_closed?: boolean; }

interface Props { width?: number; height?: number; tradeMarkers?: { time:number; side:'buy'|'sell'; price:number }[] }
const props = withDefaults(defineProps<Props>(), { width: 860, height: 430, tradeMarkers: () => [] as { time:number; side:'buy'|'sell'; price:number }[] });

const store = useOhlcvStore();
const container = ref<HTMLDivElement|null>(null);
let chart: ReturnType<typeof createChart> | null = null;
let candleSeries: ISeriesApi<'Candlestick'> | null = null;
let smaSeries: ISeriesApi<'Line'> | null = null;
let emaSeries: ISeriesApi<'Line'> | null = null;
let markerSeries: ISeriesApi<'Line'> | null = null;

const smaPeriod = 20; const emaPeriod = 50;
const smaState = createSMA(smaPeriod);
const emaState = createEMA(emaPeriod);

const lastPrice = ref<number|null>(null);
const smaValue = ref<number|null>(null);
const emaValue = ref<number|null>(null);
const partialActive = ref(false);
// Gap overlay rects
const gapRects = ref<{ key:string; style:any; state:string; missing:number; w:number }[]>([]);

// Tooltip state
const tooltip = ref<{ visible:boolean; point?: any; sma?:number|null; ema?:number|null; partial?:boolean; style: any }>({ visible:false, style:{} });

function mapCandles(){
  return store.candles.map(c=> ({
    time: Math.floor(c.open_time/1000) as UTCTimestamp,
    open: c.open, high: c.high, low: c.low, close: c.close,
    volume: c.volume, is_closed: c.is_closed
  }));
}

function recomputeIndicators(data: CandleInput[]){
  // reset states
  smaState.buffer = []; smaState.sum = 0;
  emaState.values = []; emaState.last = undefined;
  const smaVals: { time: UTCTimestamp; value: number }[] = [];
  const emaVals: { time: UTCTimestamp; value: number }[] = [];
  for(const d of data){
    const s = smaAppend(smaState, d.close);
    const e = emaAppend(emaState, d.close);
    if(smaState.buffer.length === smaPeriod) smaVals.push({ time: d.time, value: s });
    emaVals.push({ time: d.time, value: e });
  }
  smaValue.value = smaVals.length? smaVals[smaVals.length-1].value : null;
  emaValue.value = emaVals.length? emaVals[emaVals.length-1].value : null;
  return { smaVals, emaVals };
}

function applyData(){
  if(!chart || !candleSeries) return;
  const data = mapCandles();
  candleSeries.setData(data as any);
  const { smaVals, emaVals } = recomputeIndicators(data);
  smaSeries?.setData(smaVals as any);
  emaSeries?.setData(emaVals as any);
  if(data.length){ lastPrice.value = data[data.length-1].close; partialActive.value = data[data.length-1].is_closed === false; }
  applyMarkers();
}

// Incremental update when last candle changes only
let lastLen = 0;
let lastLastOpenTime: number|undefined;
function needsFullRecompute(newVal: typeof store.candles){
  if(newVal.length !== lastLen) return true; // length change
  if(!newVal.length) return false;
  // if last open_time changed but length same → repair or reorder
  const curLast = newVal[newVal.length-1].open_time;
  if(lastLastOpenTime !== undefined && curLast !== lastLastOpenTime) return true;
  return false;
}

function updateIncremental(newVal: typeof store.candles){
  if(!candleSeries) return;
  if(!newVal.length){ candleSeries.setData([] as any); lastLen = 0; lastLastOpenTime = undefined; return; }
  const last = newVal[newVal.length-1];
  const t = Math.floor(last.open_time/1000) as UTCTimestamp;
  // Update candle (partial or closed)
  candleSeries.update({ time: t, open:last.open, high:last.high, low:last.low, close:last.close,
    ...(last.is_closed===false ? { color: (last.close>=last.open? '#2ecc71':'#e74c3c'), wickColor: (last.close>=last.open? '#2ecc71':'#e74c3c'), borderColor: '#ff8f5a' } : {})
  } as any);
  // Incremental SMA append
  const smaApp = smaAppend(smaState, last.close);
  if(smaState.buffer.length === smaPeriod){
    smaSeries?.update({ time: t, value: smaApp } as any);
    smaValue.value = smaApp;
  }
  // Incremental EMA append
  const emaApp = emaAppend(emaState, last.close);
  emaSeries?.update({ time: t, value: emaApp } as any);
  emaValue.value = emaApp;
  lastPrice.value = last.close;
  partialActive.value = last.is_closed === false;
  lastLastOpenTime = last.open_time;
  applyMarkers();
}

function applyMarkers(){
  if(!chart || !markerSeries) return;
  // Use line series with no line and point markers
  const points = (props.tradeMarkers||[]).map(m => ({
    time: Math.floor(m.time/1000) as UTCTimestamp,
    value: m.price,
    color: m.side==='buy'? '#34d399' : '#f87171',
  }));
  markerSeries.setData(points as any);
}

watch(()=> store.candles, (newVal)=>{
  if(!candleSeries) return;
  if(needsFullRecompute(newVal)){
    lastLen = newVal.length;
    lastLastOpenTime = newVal.length? newVal[newVal.length-1].open_time: undefined;
    applyData();
    // Rebuild gap overlay when data set changes significantly (after gap fill)
    setTimeout(()=> buildGapRects(), 0);
  } else {
    // Only last candle content update or partial progress
    updateIncremental(newVal);
  }
},{ deep:true });

onMounted(()=>{
  if(!container.value) return;
  // Explicit width/height from props
  chart = createChart(container.value, {
    layout: { background: { color:'#0d0f14' }, textColor:'#b9c2cc' },
    rightPriceScale: { visible: true },
    timeScale: { borderColor:'#2a323c', timeVisible: true, secondsVisible: false },
    localization: {
      timeFormatter: (t: any) => {
        // t is UTCTimestamp (seconds) for our series
        const sec = typeof t === 'number' ? t : (t?.timestamp ?? 0);
        const d = new Date(sec * 1000);
        // HH:mm 24-hour
        const hh = String(d.getUTCHours()).padStart(2, '0');
        const mm = String(d.getUTCMinutes()).padStart(2, '0');
        return `${hh}:${mm}`;
      }
    },
    grid: { horzLines: { color:'#1d232c' }, vertLines:{ color:'#1d232c' } },
    crosshair: { mode: 0 },
    width: props.width,
    height: props.height,
  });
  candleSeries = chart.addCandlestickSeries({ upColor:'#2ecc71', downColor:'#e74c3c', wickUpColor:'#2ecc71', wickDownColor:'#e74c3c', borderVisible:false });
  smaSeries = chart.addLineSeries({ color:'#ffaa00', lineWidth:2, priceLineVisible:false });
  emaSeries = chart.addLineSeries({ color:'#4ea1ff', lineWidth:2, priceLineVisible:false, lineStyle: LineStyle.Solid });
  markerSeries = chart.addLineSeries({ color:'#ffffff', lineWidth:1, lastValueVisible:false, priceLineVisible:false, crosshairMarkerVisible:false });
  markerSeries.setMarkers((props.tradeMarkers||[]).map(m=> ({
    time: Math.floor(m.time/1000) as UTCTimestamp,
    position: 'inBar',
    color: m.side==='buy'? '#34d399' : '#f87171',
    shape: m.side==='buy'? 'arrowUp' : 'arrowDown',
    text: (m.side==='buy'? 'B ' : 'S ') + (m.price>=1? m.price.toFixed(3): m.price.toPrecision(3)),
  })) as any);
  applyData();
  buildGapRects();
  // Crosshair tooltip subscription
  chart.subscribeCrosshairMove(param => {
    if(!param.point || !param.time || !candleSeries) { tooltip.value.visible = false; return; }
    const sp = (param as any).seriesPrices;
    const price = sp?.get ? sp.get(candleSeries as any) as any : undefined;
    if(!price){ tooltip.value.visible = false; return; }
    const smaP = sp?.get ? sp.get(smaSeries as any) as any : undefined;
    const emaP = sp?.get ? sp.get(emaSeries as any) as any : undefined;
    // match candle by time
    const tSec = param.time as number; // UTCTimestamp
    const c = store.candles.find(cc=> Math.floor(cc.open_time/1000) === tSec);
    const boxW = 130;
  // const boxH = 140;
    let left = (param.point.x + 12);
    if(container.value){
      const rect = container.value.getBoundingClientRect();
      const innerW = rect.width;
      if(left + boxW > innerW) left = param.point.x - boxW - 12;
      if(left < 0) left = 0;
    }
    tooltip.value = {
      visible: true,
      point: c ? { time: c.open_time, open: c.open, high: c.high, low: c.low, close: c.close } : price,
      sma: smaP ?? null,
      ema: emaP ?? null,
      partial: c?.is_closed === false,
      style: { left: left + 'px', top: '8px' }
    };
  });
  const ro = new ResizeObserver(()=>{ chart?.applyOptions({ width: props.width || container.value?.clientWidth || 600, height: props.height || container.value?.clientHeight || 360 }); });
  ro.observe(container.value);
  chart.timeScale().subscribeVisibleLogicalRangeChange(()=> buildGapRects());

    // Infinite scroll (load older) when near left edge
    chart.timeScale().subscribeVisibleTimeRangeChange(async (range)=>{
      try {
        if(!range || !store.candles.length) return;
        const firstVisible = range.from as number | undefined;
        if(!firstVisible) return;
        // If the first visible time is within 2 intervals of earliest loaded, attempt fetch older
        const earliest = store.candles[0].open_time/1000; // ms->s
        // Rough interval seconds from two earliest candles if possible
        let intervalSec = 60; // default 1m
        if(store.candles.length >=2){
          const a = store.candles[0].open_time; const b = store.candles[1].open_time; const diff = Math.max(1,(b-a)/1000);
          intervalSec = diff;
        }
        if(firstVisible <= earliest + (2*intervalSec)){
          const earliestMs = store.candles[0].open_time;
          const ts = chart?.timeScale();
          const prevRange = ts?.getVisibleLogicalRange();
          const loaded = await (store as any).fetchOlder(earliestMs, 800);
          if(loaded>0 && prevRange){
            // Preserve visible logical range by shifting by number of prepended bars
            setTimeout(()=>{
              ts?.setVisibleLogicalRange({ from: (prevRange.from as number) + loaded, to: (prevRange.to as number) + loaded });
              buildGapRects();
            }, 0);
          } else if(loaded>0){
            setTimeout(()=>{ buildGapRects(); }, 0);
          }
        }
  }catch{ /* silent */ }
    });
});

onBeforeUnmount(()=>{ chart?.remove(); chart = null; });

watch(()=> props.tradeMarkers, ()=>{
  // Update both point data and marker labels when markers change
  applyMarkers();
  if(markerSeries){
    markerSeries.setMarkers((props.tradeMarkers||[]).map(m=> ({
      time: Math.floor(m.time/1000) as UTCTimestamp,
      position: 'inBar',
      color: m.side==='buy'? '#34d399' : '#f87171',
      shape: m.side==='buy'? 'arrowUp' : 'arrowDown',
      text: (m.side==='buy'? 'B ' : 'S ') + (m.price>=1? m.price.toFixed(3): m.price.toPrecision(3)),
    })) as any);
  }
}, { deep:true });

function fmt(v:number|null){ if(v==null) return '-'; if(Math.abs(v)>=1) return v.toFixed(4); return v.toPrecision(4); }
// -------- Gap Overlay Helpers --------
function buildGapRects(){
  if(!chart || !container.value){ gapRects.value = []; return; }
  const timeScale = chart.timeScale();
  const segs:any[] = (store as any).gapSegments || [];
  const rects: typeof gapRects.value = [];
  if(!segs.length){ gapRects.value = []; return; }
  const h = container.value.clientHeight;
  for(const s of segs){
    const fromCoord = timeScale.timeToCoordinate(Math.floor(s.from_ts/1000) as any);
    const toCoord = timeScale.timeToCoordinate(Math.floor(s.to_ts/1000) as any);
    if(fromCoord==null || toCoord==null) continue;
    const x = Math.min(fromCoord, toCoord);
    const w = Math.max(2, Math.abs(toCoord - fromCoord));
    rects.push({ key: s.from_ts+'-'+s.to_ts, state: s.state, missing: s.missing, w, style: { left: x+'px', top: '0px', width: w+'px', height: h+'px' }});
  }
  gapRects.value = rects;
}
</script>

<style scoped>
.lw-chart-wrapper { position: relative; width:100%; height:100%; min-height:420px; }
.chart-container { width:100%; height:100%; }
.legend { position:absolute; top:4px; left:6px; font-size:12px; background:rgba(0,0,0,0.35); padding:3px 6px; border-radius:4px; display:flex; gap:8px; align-items:center; backdrop-filter: blur(4px); }
.legend .sym { font-weight:600; color:#fff; }
.legend .price { color:#fff; }
.legend .sma { color:#ffaa00; }
.legend .ema { color:#4ea1ff; }
.legend .partial { color:#ff8f5a; font-weight:600; }
.hover-tooltip { position:absolute; top:0; background:#14181f; border:1px solid #29313b; padding:6px 8px; font-size:11px; color:#d0d7df; border-radius:4px; width:130px; pointer-events:none; box-shadow:0 2px 6px rgba(0,0,0,0.4); }
.hover-tooltip .row { display:flex; justify-content:space-between; margin:2px 0; line-height:1.15; }
.hover-tooltip .row span { color:#7f8b96; }
.hover-tooltip .row.partial { color:#ff8f5a; font-weight:600; justify-content:center; }
.gap-overlay { position:absolute; inset:0; pointer-events:none; }
.gap-overlay .gap-box { position:absolute; background:rgba(255,120,0,0.08); border:1px dashed #ff7800aa; color:#ffb273; font-size:10px; display:flex; align-items:flex-start; justify-content:flex-start; padding:2px 4px; }
.gap-overlay .gap-box.closed { background:rgba(80,160,255,0.05); border-color:#4ea1ffaa; color:#8ec4ff; }
</style>
