<template>
  <div class="space-y-4">
    <!-- Controls -->
    <div class="flex flex-wrap gap-3 items-end">
      <div>
        <label class="lbl">Interval</label>
        <input v-model="store.interval" class="inp w-24" placeholder="1m" />
      </div>
      <div>
        <label class="lbl">Limit</label>
        <input type="number" v-model.number="store.limit" class="inp w-24" min="50" max="1000" />
      </div>
      <button class="btn-xs h-8" @click="refresh" :disabled="store.loading">Refresh</button>
      <button class="btn-xs h-8" @click="toggleWs">WS: {{ wsStatus }}</button>
      <button class="btn-xs h-8" @click="fillGaps" :disabled="filling">Gaps</button>
      <button class="btn-xs h-8" @click="triggerYearBackfill" :disabled="store.yearBackfillPolling">Year Backfill</button>
      <div v-if="store.yearBackfill" class="text-[10px] text-neutral-400 flex items-center gap-1">
        <span>{{ (store.yearBackfill.percent||0).toFixed(1) }}%</span>
        <span v-if="store.yearBackfill.status==='running'">⏳</span>
        <span v-else-if="store.yearBackfill.status==='success'" class="text-emerald-400">✔</span>
        <span v-else-if="store.yearBackfill.status==='error'" class="text-red-400">✖</span>
        <span v-if="etaDisplay">ETA {{ etaDisplay }}</span>
      </div>
    </div>

    <!-- Layout -->
    <div class="grid lg:grid-cols-4 gap-4">
      <!-- Charts -->
      <div class="lg:col-span-3 space-y-4">
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60 space-y-3 overflow-hidden">
          <div class="flex items-center gap-3">
            <h2 class="title mb-0">Main Chart</h2>
            <label class="flex items-center gap-1 text-[10px] text-neutral-400">
              <input type="checkbox" v-model="useLightweight" class="align-middle" /> Lightweight
            </label>
          </div>
          <div ref="mainChartBox" class="chart-wrapper" style="height:430px;">
            <component
              :is="useLightweight ? LightweightOhlcvChart : OhlcvMainChart"
              :width="mainChartWidth"
              :height="430"
              :initialBars="140"
            />
          </div>
        </div>
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60 overflow-hidden">
          <h2 class="title flex items-center gap-2">Gap Overlay <small class="text-[10px] text-neutral-500">(Mini)</small></h2>
          <div ref="miniChartBox" class="chart-wrapper" style="height:70px;">
            <GapOverlayMiniChart :width="miniChartWidth" :height="70" />
          </div>
        </div>
      </div>

      <!-- Sidebar -->
      <div class="space-y-4">
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60">
          <h2 class="title">Stats</h2>
          <dl class="stat">
            <div><dt>Candles</dt><dd>{{ store.candles.length }}</dd></div>
            <div><dt>Open Gaps</dt><dd>{{ store.openGapCount }}</dd></div>
            <div><dt>365d Comp</dt><dd>{{ store.completeness365d!=null? (store.completeness365d*100).toFixed(3)+'%':'-' }}</dd></div>
            <div><dt>Last Range</dt><dd v-if="store.range">{{ store.range.low.toFixed(4) }} ~ {{ store.range.high.toFixed(4) }}</dd><dd v-else>-</dd></div>
          </dl>
        </div>
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60">
          <h2 class="title">Delta/WS</h2>
          <dl class="text-[11px] space-y-1">
            <div><dt class="text-neutral-500 inline-block w-24">Syncing</dt><dd class="inline-block" :class="syncing? 'text-amber-400':'text-emerald-400'">{{ syncing? 'yes':'no' }}</dd></div>
            <div><dt class="text-neutral-500 inline-block w-24">Last Delta</dt><dd class="inline-block">{{ lastDeltaSince || '-' }}</dd></div>
            <div><dt class="text-neutral-500 inline-block w-24">Appends</dt><dd class="inline-block">{{ appliedAppends }}</dd></div>
            <div><dt class="text-neutral-500 inline-block w-24">Repairs</dt><dd class="inline-block">{{ appliedRepairs }}</dd></div>
          </dl>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, computed, ref, nextTick } from 'vue';
import { useOhlcvStore } from '../stores/ohlcv';
import { defineAsyncComponent } from 'vue';
const OhlcvMainChart = defineAsyncComponent(()=> import('../components/ohlcv/OhlcvMainChart.vue'));
const LightweightOhlcvChart = defineAsyncComponent(()=> import('../components/ohlcv/LightweightOhlcvChart.vue'));
const GapOverlayMiniChart = defineAsyncComponent(()=> import('../components/ohlcv/GapOverlayMiniChart.vue'));
import { useOhlcvDeltaSync } from '../composables/useOhlcvDeltaSync';

const store = useOhlcvStore();
const { wsCtl, runDelta, syncing, lastDeltaSince, appliedAppends, appliedRepairs } = useOhlcvDeltaSync();

const useLightweight = ref(true);
const filling = ref(false);

// Responsive width logic
const mainChartBox = ref<HTMLElement|null>(null);
const miniChartBox = ref<HTMLElement|null>(null);
const mainChartWidth = ref(800);
const miniChartWidth = ref(800);
let resizeObs: ResizeObserver | null = null;
let partialTimer: any = null;   // ~0.3분(18초)
let closedTimer: any = null;    // ~0.5분(30초)
let fetchingTail = false;

function measure(){
  if(mainChartBox.value){
    const w = mainChartBox.value.clientWidth;
    if(w && w !== mainChartWidth.value) mainChartWidth.value = w;
  }
  if(miniChartBox.value){
    const w2 = miniChartBox.value.clientWidth;
    if(w2 && w2 !== miniChartWidth.value) miniChartWidth.value = w2;
  }
}

function refresh(){ store.fetchRecent(); }
function toggleWs(){ if(wsCtl.connected.value) wsCtl.disconnect(); else wsCtl.connect(); }
const wsStatus = computed(()=> wsCtl.connected.value? 'On':'Off');
const etaDisplay = computed(()=>{
  const st:any = store.yearBackfill;
  if(!st || st.eta_seconds==null) return '';
  const s = Math.round(st.eta_seconds);
  if(s<60) return s+'s';
  const m = Math.floor(s/60); const sec = s%60; return m+'m'+(sec>0? sec+'s':'');
});

function triggerYearBackfill(){ store.startYearBackfill(); }

async function fillGaps(){
  if(filling.value) return;
  filling.value = true;
  try {
    await (await fetch(`/api/ohlcv/gaps/fill?symbol=${encodeURIComponent(store.symbol)}&interval=${encodeURIComponent(store.interval)}`, { method: 'POST' })).json();
    await store.fetchRecent();
    await store.fetchGaps();
    await store.fetchMeta();
  } catch { /* silent */ }
  finally { filling.value = false; }
}

onMounted(()=>{
  store.initDefaults('XRPUSDT','1m');
  store.fetchRecent({ includeOpen: true });
  store.fetchMeta();
  wsCtl.connect();
  setTimeout(()=> { runDelta(); }, 1000);
  try {
    if (partialTimer) clearInterval(partialTimer);
    partialTimer = setInterval(async ()=>{
      if (fetchingTail) return; fetchingTail = true;
      try {
        await store.fetchRecent({ includeOpen: true, limit: Math.max(120, store.limit) });
        runDelta();
      } finally { fetchingTail = false; }
    }, 18_000);

    if (closedTimer) clearInterval(closedTimer);
    closedTimer = setInterval(async ()=>{
      if (fetchingTail) return; fetchingTail = true;
      try {
        await store.fetchRecent({ includeOpen: false, limit: Math.max(120, store.limit) });
        runDelta();
      } finally { fetchingTail = false; }
    }, 30_000);
  } catch { /* ignore */ }
  nextTick(()=>{
    measure();
    if(typeof window !== 'undefined' && 'ResizeObserver' in window){
      resizeObs = new ResizeObserver(()=> measure());
      if(mainChartBox.value) resizeObs.observe(mainChartBox.value as Element);
      if(miniChartBox.value) resizeObs.observe(miniChartBox.value as Element);
    } else if (typeof window !== 'undefined') {
      (window as any).addEventListener('resize', measure);
    }
  });
});

onBeforeUnmount(()=>{
  if(resizeObs){ resizeObs.disconnect(); resizeObs=null; }
  else if (typeof window !== 'undefined') { (window as any).removeEventListener('resize', measure); }
  if (partialTimer) clearInterval(partialTimer); partialTimer = null;
  if (closedTimer) clearInterval(closedTimer); closedTimer = null;
});

</script>

<style scoped>
.chart-wrapper { width:100%; overflow:hidden; position:relative; }
/* Converted previous Tailwind @apply to raw utility classes already in template; no @apply here to avoid build warnings */
.lbl { font-size:10px; color:#9ca3af; margin-bottom:4px; display:block; }
.inp { background:#111827; border:1px solid #374151; border-radius:4px; padding:4px 8px; font-size:12px; }
.btn-xs { font-size:10px; padding:4px 8px; border-radius:4px; background:#1f2937; border:1px solid #374151; }
.title { font-size:12px; font-weight:600; color:#d1d5db; margin-bottom:8px; }
.stat { font-size:11px; }
.stat div { display:flex; justify-content:space-between; }
</style>
