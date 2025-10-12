<template>
  <div class="ohlcv-main-chart" ref="root" tabindex="0" @keydown="onKeyDown" @wheel.prevent="onWheel" @mousedown="onMouseDown" @mousemove="onChartMouseMove" @mouseleave="onChartMouseLeave">
    <svg :width="width" :height="height">
      <rect :width="width" :height="height" fill="#0d0f14" />
      <!-- Price 영역 -->
      <g :transform="`translate(0,0)`">
        <!-- 수평 그리드 + 가격 틱 라벨 -->
        <g class="price-grid">
          <g v-for="pt in priceTicks" :key="'pt'+pt.value">
            <line :x1="0" :x2="width" :y1="priceToY(pt.value)" :y2="priceToY(pt.value)" stroke="#1d232c" stroke-width="1" stroke-dasharray="2,4" />
            <text :x="4" :y="priceToY(pt.value)-2" font-size="10" fill="#596471">{{ pt.label }}</text>
          </g>
        </g>
        <!-- Gap Segments Overlay (price 영역 아래 레이어) -->
        <g class="gap-layer">
          <g v-for="g in visibleGapSegments" :key="g.from_ts + '-' + g.to_ts">
            <rect
              :x="gapX(g)"
              y="1"
              :width="gapW(g)"
              :height="priceAreaHeight - 2"
              :fill="g.state==='open'? 'rgba(255,120,0,0.08)' : 'rgba(80,160,255,0.05)'"
              :stroke="g.state==='open'? '#ff7800aa':'#4ea1ffaa'"
              stroke-width="1"
              stroke-dasharray="4,4"
            />
            <text v-if="gapW(g) > 34" :x="gapX(g)+4" :y="12" font-size="10" :fill="g.state==='open'? '#ffb273':'#8ec4ff'">
              {{ g.missing }}ms {{ g.state==='open'?'OPEN':'OK' }}
            </text>
          </g>
        </g>
        <g v-for="c in visibleCandles" :key="c.open_time">
          <line
            class="wick"
            :x1="candleX(c) + candleWidth/2" :x2="candleX(c) + candleWidth/2"
            :y1="priceToY(c.high)" :y2="priceToY(c.low)"
            :stroke="candleStroke(c)"
            :stroke-dasharray="isPartial(c)? '2,2': 'none'"
            stroke-width="1"
          />
          <rect
            class="body"
            :x="candleX(c)"
            :y="priceToY(Math.max(c.open, c.close))"
            :width="candleBodyWidth"
            :height="Math.max(1, Math.abs(priceToY(c.open) - priceToY(c.close)))"
            :fill="candleFill(c)"
            :opacity="isPartial(c)? 0.45:1"
          />
        </g>
        <!-- SMA / EMA 라인 -->
        <polyline v-if="smaPoints.length" :points="smaPoints" fill="none" stroke="#ffaa00" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round" />
        <polyline v-if="emaPoints.length" :points="emaPoints" fill="none" stroke="#4ea1ff" stroke-width="1.2" stroke-linejoin="round" stroke-linecap="round" opacity="0.85" />
        <!-- Trade Markers -->
        <g class="trade-markers">
          <g v-for="(m, i) in markerPoints" :key="'m'+i">
            <circle :cx="m.x" :cy="m.y" r="4" :fill="m.side==='buy'? '#34d399' : '#f87171'" stroke="#0b0e13" stroke-width="1" />
          </g>
        </g>
        <!-- Crosshair -->
        <g v-if="hoverCandle">
          <!-- vertical -->
          <line :x1="hoverX" :x2="hoverX" y1="0" :y2="priceAreaHeight" stroke="#888" stroke-dasharray="3,3" stroke-width="1" />
          <!-- horizontal + price bubble -->
          <line :x1="0" :x2="width" :y1="hoverY" :y2="hoverY" stroke="#707782" stroke-dasharray="3,5" stroke-width="1" opacity="0.7" />
          <g :transform="`translate(${width-74}, ${Math.max(12, Math.min(priceAreaHeight-12, hoverY))-10})`">
            <rect width="70" height="20" rx="4" ry="4" fill="#0f1319" stroke="#2a3340" />
            <text x="35" y="13" text-anchor="middle" font-size="11" fill="#cbd3dc">{{ fmt(hoverPrice) }}</text>
          </g>
          <!-- time bubble at bottom -->
          <g :transform="`translate(${Math.max(4, Math.min(width-90, hoverX-45))}, ${priceAreaHeight+2})`">
            <rect width="90" height="18" rx="4" ry="4" fill="#0f1319" stroke="#2a3340" />
            <text x="45" y="12" text-anchor="middle" font-size="10" fill="#cbd3dc">{{ formatTs(hoverCandle.open_time) }}</text>
          </g>
        </g>
      </g>
      <!-- Volume 영역 -->
      <g :transform="`translate(0,${priceAreaHeight})`">
        <rect :width="width" :height="volumeAreaHeight" fill="#11151d" />
        <!-- 볼륨 그리드 -->
        <g class="vol-grid">
          <g v-for="vt in volumeTicks" :key="'vt'+vt.value">
            <line :x1="0" :x2="width" :y1="priceAreaHeight + (volumeAreaHeight - (vt.ratio*volumeAreaHeight))" :y2="priceAreaHeight + (volumeAreaHeight - (vt.ratio*volumeAreaHeight))" stroke="#1a2027" stroke-width="1" stroke-dasharray="2,6" />
            <text :x="4" :y="priceAreaHeight + (volumeAreaHeight - (vt.ratio*volumeAreaHeight)) - 2" font-size="9" fill="#49525d">{{ vt.label }}</text>
          </g>
        </g>
        <rect
          v-for="c in visibleCandles"
          :key="'v'+c.open_time"
          class="vol"
          :x="candleX(c)"
          :y="volumeToY(c.volume || 0)"
          :width="candleBodyWidth"
          :height="Math.max(1, volumeAreaHeight - volumeToY(c.volume || 0))"
          :fill="c.close >= c.open ? volUpColor : volDownColor"
          opacity="0.85"
        />
        <!-- Hover volume highlight -->
        <rect v-if="hoverCandle" :x="candleX(hoverCandle)" :y="volumeToY(hoverCandle.volume||0)" :width="candleBodyWidth" :height="Math.max(1, volumeAreaHeight - volumeToY(hoverCandle.volume||0))" fill="#ffffff22" />
      </g>
      <!-- 현재 뷰 범위 표시 라벨 -->
      <text x="6" y="14" font-size="11" fill="#9aa4b1">Bars: {{ visibleCandles.length }} | Range: {{ displayRange }}</text>
    </svg>
    <!-- 드래그 중 오버레이 -->
    <div v-if="dragging" class="drag-overlay">DRAG</div>
    <!-- Tooltip -->
    <div v-if="hoverCandle" class="tt" :style="tooltipStyle">
      <div class="tt-row"><span>T</span><b>{{ hoverCandle.open_time }}</b></div>
      <div class="tt-row"><span>O</span><b>{{ fmt(hoverCandle.open) }}</b></div>
      <div class="tt-row"><span>H</span><b>{{ fmt(hoverCandle.high) }}</b></div>
      <div class="tt-row"><span>L</span><b>{{ fmt(hoverCandle.low) }}</b></div>
      <div class="tt-row"><span>C</span><b>{{ fmt(hoverCandle.close) }}</b></div>
      <div class="tt-row"><span>V</span><b>{{ hoverCandle.volume ?? '-' }}</b></div>
      <div v-if="smaValue!=null" class="tt-row"><span>SMA{{ smaPeriod }}</span><b>{{ fmt(smaValue) }}</b></div>
      <div v-if="emaValue!=null" class="tt-row"><span>EMA{{ emaPeriod }}</span><b>{{ fmt(emaValue) }}</b></div>
      <div v-if="isPartial(hoverCandle)" class="tt-row warn">partial</div>
      <div v-if="hoverMarker" class="tt-row"><span>TRADE</span><b>{{ hoverMarker.side.toUpperCase() }} @ {{ fmt(hoverMarker.price) }}</b></div>
    </div>
  </div>
</template>

<script setup lang="ts">
// 사용 가이드:
// 1) Pinia `useOhlcvStore` 가 상위에서 초기화되어 candles 가 reactive 하게 채워져야 합니다.
// 2) interval 변경 시 store.candles 새로고침 후 본 컴포넌트는 자동으로 최신 window 를 우측 정렬 렌더링합니다.
// 3) 인터랙션:
//    - 마우스 휠: 좌/우 스크롤 (수평 이동)
//    - Ctrl/Cmd + 휠: 줌 인/아웃 (barsInView 변경)
//    - 드래그(좌클릭): 팬 (과거/최근 이동)
// 4) Props 로 초기/최소/최대 바 개수 조정 가능.
// 5) 차후 개선 아이디어 (TODO):
//    - Y축 가격/볼륨 스케일 라벨
//    - Crosshair & Tooltip(open/high/low/close/volume)
//    - 지표(SMA/EMA) 오버레이 라인 (frontend/src/lib/indicators 활용)
//    - partial 진행 중 미확정 캔들 시각적 구분 (is_closed=false → body 투명)
//    - GapSegment 시각화(현재 mini chart 별도) 통합 옵션
// 6) 접근성: 키보드 화살표 ←/→ 지원 예정
// 7) 고성능 모드: 많은 바(>2000) 표시 시 canvas 전환 고려
import { computed, ref, onBeforeUnmount } from 'vue';
// 상대 경로 사용 (일부 환경에서 @ alias 미인식 오류 방지)
import { createSMA, smaAppend } from '../../lib/indicators/sma';
import { createEMA, emaAppend } from '../../lib/indicators/ema';
import { useOhlcvStore } from '../../stores/ohlcv';

interface Props {
  width?: number;  // 전체 SVG 폭
  height?: number; // 전체 SVG 높이
  volumeRatio?: number; // 볼륨 영역 비율(0~0.5)
  initialBars?: number; // 초기 표시 바 개수
  minBars?: number;
  maxBars?: number;
  tradeMarkers?: { time:number; side:'buy'|'sell'; price:number }[]; // ms time
}
const props = withDefaults(defineProps<Props>(), {
  width: 860,
  height: 420,
  volumeRatio: 0.25,
  initialBars: 120,
  minBars: 20,
  maxBars: 500,
  tradeMarkers: () => [] as { time:number; side:'buy'|'sell'; price:number }[],
});

const store = useOhlcvStore();
const root = ref<HTMLElement|null>(null);

// 뷰 윈도우 상태 (rightAligned: 최신이 오른쪽 끝)
const barsInView = ref(props.initialBars);
const rightOffset = ref(0); // 0=최신 끝, 10=과거로 10칸 밀림

// 드래그 상태
const dragging = ref(false);
let dragStartX = 0;
let dragStartRightOffset = 0;

const candles = computed(()=> store.candles);

const priceAreaHeight = computed(()=> Math.round(props.height * (1 - props.volumeRatio)));
const volumeAreaHeight = computed(()=> props.height - priceAreaHeight.value);

const visibleCandles = computed(()=>{
  if(!candles.value.length) return [] as typeof candles.value;
  const total = candles.value.length;
  const endIdx = Math.max(0, total - 1 - rightOffset.value);
  const startIdx = Math.max(0, endIdx - barsInView.value + 1);
  return candles.value.slice(startIdx, endIdx + 1);
});

const candleWidth = computed(()=> props.width / Math.max(1, barsInView.value));
const candleBodyWidth = computed(()=> Math.max(1, candleWidth.value * 0.7));

const priceRange = computed(()=>{
  if(!visibleCandles.value.length) return {lo:0, hi:1};
  let lo = visibleCandles.value[0].low;
  let hi = visibleCandles.value[0].high;
  for(const c of visibleCandles.value){ if(c.low < lo) lo=c.low; if(c.high>hi) hi=c.high; }
  if(hi===lo){ hi += 1; lo -= 1; }
  return {lo, hi};
});

const volumeRange = computed(()=>{
  let maxV = 0;
  for(const c of visibleCandles.value){ if((c.volume||0) > maxV) maxV = c.volume||0; }
  if(maxV <= 0) maxV = 1;
  return {maxV};
});

function candleX(c: any){
  // visibleCandles 순서 가정: ASC
  const idx = visibleCandles.value.indexOf(c);
  return idx * candleWidth.value + (candleWidth.value - candleBodyWidth.value)/2;
}
function priceToY(v: number){
  const {lo, hi} = priceRange.value; const span = hi-lo; return ( (hi - v) / span ) * (priceAreaHeight.value - 2) + 1;
}
function volumeToY(v: number){
  const {maxV} = volumeRange.value; return priceAreaHeight.value + ((1 - (v / maxV)) * (volumeAreaHeight.value - 2)) + 1 - priceAreaHeight.value;
}

// Trade markers mapping (time -> nearest visible candle index; price -> y)
const markerRaw = computed(()=> props.tradeMarkers || []);
const markerPoints = computed(()=>{
  const out: { x:number; y:number; side:'buy'|'sell'; price:number }[] = [];
  if(!visibleCandles.value.length || !props.tradeMarkers?.length) return out;
  const firstTs = visibleCandles.value[0].open_time;
  const lastTs = visibleCandles.value[visibleCandles.value.length-1].open_time;
  for(const m of markerRaw.value){
    if(m.time < firstTs || m.time > lastTs) continue;
    // find nearest candle index by time
    let idx = visibleCandles.value.findIndex(c=> c.open_time === m.time);
    if(idx < 0){
      // find first candle with open_time > m.time then take previous
      let j = visibleCandles.value.findIndex(c=> c.open_time > m.time);
      idx = (j<=0)? 0 : (j-1);
    }
    const c = visibleCandles.value[idx];
    const x = candleX(c) + candleBodyWidth.value/2;
    const y = priceToY(m.price);
    out.push({ x, y, side: m.side, price: m.price });
  }
  return out;
});

// 인터랙션
function onWheel(e: WheelEvent){
  const rect = root.value?.getBoundingClientRect();
  const cursorRatio = rect ? (e.clientX - rect.left) / rect.width : 0.5;
  const delta = e.deltaY;
  if(e.ctrlKey || e.metaKey){
    // 중심(커서) 기준 줌 → 줌 전후 기준 캔들 인덱스 유지 시도
    const prevBars = barsInView.value;
    const targetIndexFromStart = Math.round(prevBars * cursorRatio);
    const factor = delta > 0 ? 1.15 : 0.85;
    let next = Math.round(prevBars * factor);
    next = Math.min(props.maxBars, Math.max(props.minBars, next));
    barsInView.value = next;
    const afterBars = barsInView.value;
  // const newTargetStart = Math.max(0, targetIndexFromStart - Math.round(afterBars * cursorRatio));
    // rightOffset 재계산: endIdx = total-1-rightOffset
    const total = candles.value.length;
    const endIdx = Math.max(0, total - 1 - rightOffset.value);
    const startIdx = Math.max(0, endIdx - prevBars + 1);
    const anchorGlobal = startIdx + targetIndexFromStart;
    const newStartIdx = Math.max(0, anchorGlobal - Math.round(afterBars * cursorRatio));
    const newEndIdx = newStartIdx + afterBars - 1;
    const newRight = Math.max(0, total - 1 - newEndIdx);
    rightOffset.value = Math.min(newRight, Math.max(0, total - afterBars));
  } else {
    const step = delta > 0 ? 5 : -5;
    rightOffset.value = Math.max(0, Math.min(rightOffset.value + step, Math.max(0, (candles.value.length - barsInView.value))));
  }
}

function onMouseDown(e: MouseEvent){
  if(e.button !== 0) return;
  dragging.value = true;
  dragStartX = e.clientX;
  dragStartRightOffset = rightOffset.value;
  window.addEventListener('mousemove', onMouseMove);
  window.addEventListener('mouseup', onMouseUp);
}
function onMouseMove(e: MouseEvent){
  if(!dragging.value) return;
  const dx = e.clientX - dragStartX;
  const barsDelta = Math.round(dx / candleWidth.value);
  rightOffset.value = Math.max(0, dragStartRightOffset - barsDelta);
  const maxOff = Math.max(0, candles.value.length - barsInView.value);
  if(rightOffset.value > maxOff) rightOffset.value = maxOff;
  // If we've reached far left (showing earliest candle) and dragging further left (barsDelta positive => moving earlier)
  if(rightOffset.value === maxOff && barsDelta > 0){
    // Attempt fetch older
    const earliest = candles.value.length? candles.value[0].open_time: undefined;
    if(earliest){
      (store as any).fetchOlder(earliest, 800).then((loaded:number)=>{
        if(loaded>0){
          // Keep visual position by increasing rightOffset by loaded bars (since we prepended)
          rightOffset.value = Math.min(maxOff + loaded, Math.max(0, (candles.value.length - barsInView.value)));
        }
      });
    }
  }
}
function onMouseUp(){
  dragging.value = false;
  window.removeEventListener('mousemove', onMouseMove);
  window.removeEventListener('mouseup', onMouseUp);
}

onBeforeUnmount(()=>{ onMouseUp(); });

const upColor = '#2ecc71';
const downColor = '#e74c3c';
const volUpColor = '#247e4d';
const volDownColor = '#7e2b24';

const displayRange = computed(()=>{
  if(!visibleCandles.value.length) return '-';
  const first = visibleCandles.value[0].open_time;
  const last = visibleCandles.value[visibleCandles.value.length-1].open_time;
  return `${first} ~ ${last}`;
});

// ---------- SMA / EMA 계산 (표시 범위 기준) ----------
const smaPeriod = 20;
const emaPeriod = 50;
const smaValues = computed(()=>{
  const vals:number[] = [];
  let state = createSMA(smaPeriod);
  for(const c of visibleCandles.value){
    smaAppend(state, c.close);
    if(state.buffer.length === smaPeriod) vals.push(state.sum / state.buffer.length); else vals.push(NaN);
  }
  return vals;
});
const emaValues = computed(()=>{
  const vals:number[] = [];
  let state = createEMA(emaPeriod);
  for(const c of visibleCandles.value){
    vals.push(emaAppend(state, c.close));
  }
  return vals;
});
const smaPoints = computed(()=> visibleCandles.value.map((c,i)=> isNaN(smaValues.value[i])? null : `${candleX(c)+candleBodyWidth.value/2},${priceToY(smaValues.value[i])}`).filter(Boolean).join(' '));
const emaPoints = computed(()=> visibleCandles.value.map((c,i)=> `${candleX(c)+candleBodyWidth.value/2},${priceToY(emaValues.value[i])}`).join(' '));

// ---------- Gap Overlay ----------
interface GapSeg { from_ts: number; to_ts: number; missing: number; state: 'open'|'closed'; }
const allGapSegments = computed(()=> store.gapSegments as GapSeg[]);
const visibleGapSegments = computed(()=>{
  if(!visibleCandles.value.length) return [] as GapSeg[];
  const start = visibleCandles.value[0].open_time;
  const end = visibleCandles.value[visibleCandles.value.length-1].open_time;
  return allGapSegments.value.filter(g => !(g.to_ts < start || g.from_ts > end));
});
// 각 gap 의 x, width 계산: 캔들 폭 기반 선형 매핑 (시간 간격 균일 가정)
function gapX(g:GapSeg){
  if(!visibleCandles.value.length) return 0;
  const firstTs = visibleCandles.value[0].open_time;
  const lastTs = visibleCandles.value[visibleCandles.value.length-1].open_time;
  const span = Math.max(1, lastTs - firstTs);
  const ratio = (Math.max(firstTs, g.from_ts) - firstTs)/span;
  return ratio * props.width;
}
function gapW(g:GapSeg){
  if(!visibleCandles.value.length) return 0;
  const firstTs = visibleCandles.value[0].open_time;
  const lastTs = visibleCandles.value[visibleCandles.value.length-1].open_time;
  const span = Math.max(1, lastTs - firstTs);
  const from = Math.max(firstTs, g.from_ts);
  const to = Math.min(lastTs, g.to_ts);
  const wRatio = (to - from)/span;
  return Math.max(2, wRatio * props.width);
}

// Hover / Crosshair
const hoverX = ref(0);
const hoverIndex = ref<number|null>(null);
const hoverCandle = computed(()=> (hoverIndex.value!=null? visibleCandles.value[hoverIndex.value]: null));
const smaValue = computed(()=> (hoverIndex.value!=null? smaValues.value[hoverIndex.value]: null));
const emaValue = computed(()=> (hoverIndex.value!=null? emaValues.value[hoverIndex.value]: null));

function onChartMouseMove(e: MouseEvent){
  if(!root.value) return;
  const rect = root.value.getBoundingClientRect();
  const x = e.clientX - rect.left;
  const idx = Math.floor(x / candleWidth.value);
  if(idx >=0 && idx < visibleCandles.value.length){
    hoverIndex.value = idx;
    hoverX.value = idx * candleWidth.value + candleWidth.value/2;
  } else {
    hoverIndex.value = null;
  }
  // Marker hover detection (within 6px radius)
  const y = e.clientY - rect.top;
  let found: { side:'buy'|'sell'; price:number } | null = null;
  for(const mp of markerPoints.value){
    const dx = (mp.x) - (x);
    const dy = (mp.y) - (y);
    const dist2 = dx*dx + dy*dy;
    if(dist2 <= (6*6)) { found = { side: mp.side, price: mp.price }; break; }
  }
  hoverMarker.value = found;
  // track hoverY/hoverPrice for horizontal crosshair
  hoverY.value = y;
  const {lo, hi} = priceRange.value; const span = hi - lo;
  hoverPrice.value = hi - ((y-1) / Math.max(1, (priceAreaHeight.value - 2))) * span;
}
function onChartMouseLeave(){ hoverIndex.value = null; }

function fmt(v:number){ return (v>=1 || v<=-1)? v.toFixed(4): v.toPrecision(4); }
function isPartial(c:any){ return c.is_closed === false; }
function candleFill(c:any){ return c.close >= c.open ? upColor : downColor; }
function candleStroke(c:any){ return c.close >= c.open ? upColor : downColor; }
function formatTs(ts:number){
  // ts is expected as epoch milliseconds or seconds depending on store; normalize
  const ms = ts > 1e12 ? ts : ts * 1000;
  const d = new Date(ms);
  // HH:MM and optionally date if day changed within view later (simple for now)
  const hh = String(d.getHours()).padStart(2,'0');
  const mm = String(d.getMinutes()).padStart(2,'0');
  return `${hh}:${mm}`;
}

const tooltipStyle = computed(()=>{
  if(!hoverCandle.value || !root.value) return {} as any;
  const rect = root.value.getBoundingClientRect();
  const left = Math.min(rect.width - 140, Math.max(0, hoverX.value + 12));
  return { left: left + 'px', top: '8px' };
});
const hoverMarker = ref<{ side:'buy'|'sell'; price:number } | null>(null);
const hoverY = ref(0);
const hoverPrice = ref(0);

// ---------- 축 틱 계산 ----------
const priceTicks = computed(()=>{
  const out:{ value:number; label:string }[] = [];
  const {lo, hi} = priceRange.value;
  if(!isFinite(lo) || !isFinite(hi) || hi<=lo) return out;
  const rawSpan = hi - lo;
  const ideal = rawSpan / 5; // 목표 5개 내외
  const pow10 = Math.pow(10, Math.floor(Math.log10(ideal)));
  const steps = [1,2,2.5,5,10].map(s=> s*pow10);
  let step = steps[steps.length-1];
  for(const s of steps){ if(ideal <= s){ step = s; break; } }
  const start = Math.ceil(lo / step) * step;
  for(let v=start; v<=hi; v+=step){ out.push({ value:v, label: tickFormat(v) }); if(out.length>8) break; }
  return out;
});
function tickFormat(v:number){
  if(Math.abs(v) >= 1000) return v.toFixed(0);
  if(Math.abs(v) >= 100) return v.toFixed(1);
  if(Math.abs(v) >= 1) return v.toFixed(2);
  return v.toPrecision(3);
}
const volumeTicks = computed(()=>{
  const out:{ value:number; ratio:number; label:string }[] = [];
  const {maxV} = volumeRange.value;
  if(!isFinite(maxV) || maxV<=0) return out;
  const steps = 3;
  for(let i=1;i<=steps;i++){
    const ratio = i/steps;
    const value = maxV * ratio;
    out.push({ value, ratio, label: shortNumber(value) });
  }
  return out.reverse();
});
function shortNumber(v:number){
  if(v>=1e9) return (v/1e9).toFixed(2)+'B';
  if(v>=1e6) return (v/1e6).toFixed(2)+'M';
  if(v>=1e3) return (v/1e3).toFixed(2)+'K';
  return v.toFixed(0);
}

// ---------- 키보드 네비 ----------
function onKeyDown(e:KeyboardEvent){
  if(e.key === 'ArrowLeft'){
    rightOffset.value = Math.min(candles.value.length, rightOffset.value + Math.max(5, Math.round(barsInView.value*0.1)));
  } else if(e.key === 'ArrowRight'){
    rightOffset.value = Math.max(0, rightOffset.value - Math.max(5, Math.round(barsInView.value*0.1)));
  } else if(e.key === '+' || (e.key === '=' && (e.shiftKey || !e.ctrlKey))){
    barsInView.value = Math.max(props.minBars, Math.round(barsInView.value * 0.85));
  } else if(e.key === '-'){
    barsInView.value = Math.min(props.maxBars, Math.round(barsInView.value * 1.15));
  }
}

</script>

<style scoped>
.ohlcv-main-chart { position: relative; user-select: none; cursor: grab; }
.ohlcv-main-chart:active { cursor: grabbing; }
.drag-overlay { position:absolute; top:8px; right:8px; background:rgba(255,255,255,0.06); color:#ccc; font-size:10px; padding:2px 6px; border-radius:4px; pointer-events:none; }
/* Tooltip */
.tt { position:absolute; min-width:120px; background:#14181f; border:1px solid #29313b; padding:6px 8px; font-size:11px; color:#d0d7df; top:8px; pointer-events:none; box-shadow:0 2px 6px rgba(0,0,0,0.4); border-radius:4px; }
.tt-row { display:flex; justify-content:space-between; line-height:1.2; margin:2px 0; gap:6px; }
.tt-row span { color:#7f8b96; font-weight:500; }
.tt-row.warn { color:#ff8f5a; font-weight:600; text-transform:uppercase; letter-spacing:0.5px; }
svg { font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen; }
.gap-layer rect { pointer-events:none; }
.gap-layer text { pointer-events:none; font-weight:500; }
.price-grid text { paint-order: stroke; stroke:#0d0f14; stroke-width:2; }
.vol-grid text { paint-order: stroke; stroke:#11151d; stroke-width:2; }
.ohlcv-main-chart:focus { outline:1px solid #2d8cff; }
</style>
