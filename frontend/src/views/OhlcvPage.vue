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
              :tradeMarkers="tradeMarkers"
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
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60">
          <h2 class="title">DCA Simulator</h2>
          <div class="grid grid-cols-2 gap-2 text-[11px]">
            <label class="flex items-center gap-2"><span class="w-28 text-neutral-500">base notional</span>
              <input class="inp w-full" type="number" min="0" step="0.01" v-model.number="dca.baseNotional" />
            </label>
            <label class="flex items-center gap-2"><span class="w-28 text-neutral-500">add ratio</span>
              <input class="inp w-full" type="number" min="0" step="0.05" v-model.number="dca.addRatio" />
            </label>
            <label class="flex items-center gap-2"><span class="w-28 text-neutral-500">max legs</span>
              <input class="inp w-full" type="number" min="1" step="1" v-model.number="dca.maxLegs" />
            </label>
            <label class="flex items-center gap-2"><span class="w-28 text-neutral-500">cooldown(s)</span>
              <input class="inp w-full" type="number" min="0" step="1" v-model.number="dca.cooldownSec" />
            </label>
            <label class="flex items-center gap-2"><span class="w-28 text-neutral-500">min move %</span>
              <input class="inp w-full" type="number" min="0" step="0.001" v-model.number="dca.minPriceMovePct" />
            </label>
            <label class="flex items-center gap-2"><span class="w-28 text-neutral-500">fee rate</span>
              <input class="inp w-full" type="number" min="0" step="0.0001" v-model.number="dca.feeRate" />
            </label>
            <label class="flex items-center gap-2"><span class="w-28 text-neutral-500">take profit %</span>
              <input class="inp w-full" type="number" min="0.001" step="0.001" v-model.number="dca.takeProfitPct" />
            </label>
            <label class="flex items-center gap-2"><span class="w-28 text-neutral-500">trail %</span>
              <input class="inp w-full" type="number" min="0" step="0.001" v-model.number="dca.trailingTakeProfitPct" />
            </label>
            <label class="flex items-center gap-2"><span class="w-28 text-neutral-500">max holding bars</span>
              <input class="inp w-full" type="number" min="0" step="1" v-model.number="dca.maxHoldingBars" />
            </label>
            <label class="flex items-center gap-2 col-span-2"><span class="w-28 text-neutral-500">label</span>
              <input class="inp w-full" type="text" v-model="simLabel" placeholder="e.g. test run" />
            </label>
          </div>
          <div class="mt-2 flex items-center gap-2">
            <button class="btn-xs" @click="runSim" :disabled="!store.candles.length">Simulate</button>
            <button class="btn-xs" @click="saveSim" :disabled="saving || !sim.trades.length">Save</button>
            <span class="text-[10px] text-neutral-500" v-if="sim.closed">TP hit · ROI {{ simRoiLabel }} · Fees {{ sim.totalFees.toFixed(4) }}</span>
            <span class="text-[10px] text-neutral-500" v-else-if="sim.trades.length">Open · legs {{ legCount }} / {{ dca.maxLegs }} · entry {{ sim.avgEntry?.toFixed(6) ?? '-' }}</span>
            <span class="text-[10px]" v-if="saveMessage" :class="saveOk? 'text-emerald-400':'text-rose-400'">{{ saveMessage }}</span>
          </div>
          <div class="mt-2 max-h-48 overflow-auto">
            <table class="w-full text-[10px] border-collapse">
              <thead class="text-neutral-400">
                <tr>
                  <th class="text-left font-normal p-1 border-b border-neutral-700/60">Time</th>
                  <th class="text-left font-normal p-1 border-b border-neutral-700/60">Side</th>
                  <th class="text-right font-normal p-1 border-b border-neutral-700/60">Price</th>
                  <th class="text-right font-normal p-1 border-b border-neutral-700/60">Qty</th>
                  <th class="text-right font-normal p-1 border-b border-neutral-700/60">Notional</th>
                  <th class="text-right font-normal p-1 border-b border-neutral-700/60">Fee</th>
                  <th class="text-left font-normal p-1 border-b border-neutral-700/60">Note</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(t,i) in sim.trades" :key="i" class="hover:bg-neutral-800/40">
                  <td class="p-1">{{ new Date(t.time).toLocaleString() }}</td>
                  <td class="p-1" :class="t.side==='buy'? 'text-emerald-300':'text-rose-300'">{{ t.side }}</td>
                  <td class="p-1 text-right">{{ t.price.toFixed(6) }}</td>
                  <td class="p-1 text-right">{{ t.qty.toFixed(6) }}</td>
                  <td class="p-1 text-right">{{ t.notional.toFixed(4) }}</td>
                  <td class="p-1 text-right">{{ t.fee.toFixed(4) }}</td>
                  <td class="p-1">{{ t.note || '' }}</td>
                </tr>
                <tr v-if="!sim.trades.length"><td class="p-2 text-neutral-500" colspan="7">no trades yet</td></tr>
              </tbody>
            </table>
          </div>
        </div>
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60">
          <div class="flex items-center justify-between">
            <h2 class="title mb-0">Saved Sims</h2>
            <div class="flex items-center gap-2">
              <button class="btn-xs" @click="fetchSimList" :disabled="loadingSims">Refresh</button>
              <span class="text-[10px] text-neutral-500" v-if="loadingSims">loading…</span>
            </div>
          </div>
          <div class="mt-2 max-h-48 overflow-auto">
            <table class="w-full text-[10px] border-collapse">
              <thead class="text-neutral-400">
                <tr>
                  <th class="text-left font-normal p-1 border-b border-neutral-700/60">#</th>
                  <th class="text-left font-normal p-1 border-b border-neutral-700/60">When</th>
                  <th class="text-left font-normal p-1 border-b border-neutral-700/60">Label</th>
                  <th class="text-right font-normal p-1 border-b border-neutral-700/60">Legs</th>
                  <th class="text-right font-normal p-1 border-b border-neutral-700/60">ROI</th>
                  <th class="text-right font-normal p-1 border-b border-neutral-700/60">Action</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="it in savedSims" :key="it.id" class="hover:bg-neutral-800/40" :class="selectedSimId===it.id? 'bg-neutral-800/60':''" @click="onSelectSim(it)">
                  <td class="p-1">{{ it.id }}</td>
                  <td class="p-1">{{ new Date((it.created_ts||0)*1000).toLocaleString() }}</td>
                  <td class="p-1">{{ it.label || '-' }}</td>
                  <td class="p-1 text-right">{{ countBuyLegs(it.trades) }}</td>
                  <td class="p-1 text-right">{{ it.summary?.realizedRoi!=null? (it.summary.realizedRoi*100).toFixed(2)+'%':'-' }}</td>
                  <td class="p-1 text-right"><button class="btn-xs" @click.stop="loadSim(it.id)">Load</button></td>
                </tr>
                <tr v-if="!savedSims.length"><td class="p-2 text-neutral-500" colspan="6">no saved sims</td></tr>
              </tbody>
            </table>
          </div>
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
import { simulateDCA, type DcaParams, type DcaResult, toMarkers } from '../utils/dcaSimulator';

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

// --------- DCA Simulator state ---------
const dca = ref<DcaParams>({
  baseNotional: 100,
  addRatio: 1.0,
  maxLegs: 20,
  cooldownSec: 120,
  minPriceMovePct: 0.005,
  feeRate: 0.001,
  takeProfitPct: 0.01,
  trailingTakeProfitPct: 0,
  maxHoldingBars: 0,
});
const sim = ref<DcaResult>({ trades: [], realizedPnl: 0, realizedRoi: null, totalFees: 0, closed: false });
const tradeMarkers = ref<{ time:number; side:'buy'|'sell'; price:number }[]>([]);
const legCount = computed(()=> sim.value.trades.filter(t=> t.side==='buy').length);
const simRoiLabel = computed(()=> sim.value.realizedRoi!=null? (sim.value.realizedRoi*100).toFixed(2)+'%':'-');
function runSim(){
  const data = store.candles.map(c=> ({ open_time:c.open_time, close_time:c.close_time, open:c.open, high:c.high, low:c.low, close:c.close, volume:c.volume }));
  sim.value = simulateDCA(data as any, dca.value);
  tradeMarkers.value = toMarkers(sim.value.trades);
}

// Persist current simulation to backend
const saving = ref(false);
const saveOk = ref<boolean|null>(null);
const saveMessage = ref('');
async function saveSim(){
  if(!sim.value.trades.length || !store.candles.length) return;
  if(saving.value) return;
  saving.value = true; saveOk.value = null; saveMessage.value = '';
  try {
    const first = store.candles[0].open_time;
    const last = store.candles[store.candles.length-1].open_time;
    const payload = {
      symbol: store.symbol,
      interval: store.interval,
      params: dca.value,
      trades: sim.value.trades,
      summary: {
        realizedPnl: sim.value.realizedPnl,
        realizedRoi: sim.value.realizedRoi,
        totalFees: sim.value.totalFees,
        closed: sim.value.closed,
        avgEntry: sim.value.avgEntry ?? null,
        positionQty: sim.value.positionQty ?? null,
        positionCost: sim.value.positionCost ?? null,
      },
      first_open_time: first,
      last_open_time: last,
  label: (simLabel.value || 'DCA run'),
    };
    const res = await fetch('/api/sim/dca/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const j = await res.json();
    if(j && j.status === 'ok'){
      saveOk.value = true; saveMessage.value = `saved #${j.id}`;
    } else {
      saveOk.value = false; saveMessage.value = (j && j.error) ? `save failed: ${j.error}` : 'save failed';
    }
  } catch {
    saveOk.value = false; saveMessage.value = 'save failed';
  } finally {
    saving.value = false;
  }
}

// --------- Saved Sims (list & load) ---------
const savedSims = ref<any[]>([]);
const loadingSims = ref(false);
const selectedSimId = ref<number|null>(null);
const simLabel = ref<string>('');
async function fetchSimList(){
  if(loadingSims.value) return;
  loadingSims.value = true;
  try{
    const q = new URLSearchParams({ symbol: store.symbol, interval: store.interval, limit: String(20) }).toString();
    const res = await fetch(`/api/sim/dca/list?${q}`);
    const j = await res.json();
    savedSims.value = (j && j.status==='ok' && Array.isArray(j.items))? j.items : [];
  } catch {
    savedSims.value = [];
  } finally {
    loadingSims.value = false;
  }
}
async function loadSim(id:number){
  try{
    const res = await fetch(`/api/sim/dca/${id}`);
    const j = await res.json();
    if(!j || j.status!=='ok' || !j.data) return;
    const row = j.data;
    // apply params
    if(row.params){ dca.value = { ...dca.value, ...row.params } as DcaParams; }
    // apply trades & summary to sim state
    const summary = row.summary || {};
    sim.value = {
      trades: row.trades || [],
      realizedPnl: summary.realizedPnl ?? 0,
      realizedRoi: summary.realizedRoi ?? null,
      totalFees: summary.totalFees ?? 0,
      closed: summary.closed ?? false,
      avgEntry: summary.avgEntry ?? undefined,
      positionQty: summary.positionQty ?? undefined,
      positionCost: summary.positionCost ?? undefined,
    } as DcaResult;
    tradeMarkers.value = toMarkers(sim.value.trades);
    selectedSimId.value = id;
    simLabel.value = row.label || '';
  } catch {
    /* ignore */
  }
}
function onSelectSim(it:any){ selectedSimId.value = it.id; simLabel.value = it.label || ''; }
onMounted(()=>{ fetchSimList(); });

function countBuyLegs(trades: any[]): number {
  if(!Array.isArray(trades)) return 0;
  try {
    return trades.filter((t:any)=> (t && t.side==='buy')).length;
  } catch {
    return 0;
  }
}
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
