<template>
  <div class="space-y-4">
    <div class="flex flex-wrap gap-4 items-end">
      <div class="flex flex-col gap-1">
        <label class="text-[10px] text-neutral-400">Symbol</label>
        <div class="px-2 py-1 text-xs rounded bg-neutral-800 border border-neutral-700 font-mono tracking-wide select-none">{{ store.symbol }}</div>
      </div>
      <div class="flex flex-col gap-1">
        <label class="text-[10px] text-neutral-400">Interval</label>
        <input v-model="store.interval" class="inp" placeholder="e.g. 1m" />
      </div>
      <div class="flex flex-col gap-1">
        <label class="text-[10px] text-neutral-400">Limit</label>
        <input type="number" v-model.number="store.limit" class="inp w-24" min="10" max="500" />
      </div>
      <div class="flex flex-col gap-1">
        <label class="text-[10px] text-neutral-400">Poll (s)</label>
        <input type="number" v-model.number="store.pollSec" class="inp w-20" min="5" max="120" />
      </div>
      <button class="btn-xs h-8 self-start" @click="refresh" :disabled="store.loading">Refresh</button>
      <button class="btn-xs h-8 self-start" @click="store.toggleAuto()" :class="store.auto ? 'bg-neutral-700':'bg-neutral-800'">Auto: {{ store.auto? 'On':'Off' }}</button>
    </div>

    <div class="grid md:grid-cols-3 gap-4">
      <div class="md:col-span-2 space-y-4">
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60">
          <div class="flex items-center justify-between mb-2">
            <h2 class="text-xs font-semibold text-neutral-300">Mini Chart</h2>
            <div class="text-[10px] text-neutral-500" v-if="store.range">Range: <span class="text-neutral-300">{{ store.range.low.toFixed(4) }} - {{ store.range.high.toFixed(4) }}</span></div>
          </div>
          <div v-if="!store.candles.length" class="text-[11px] text-neutral-500">No data</div>
          <svg v-else :viewBox="`0 0 ${chartW} ${chartH}`" class="w-full h-48">
            <g v-for="(c,i) in store.candles" :key="c.open_time">
              <line :x1="x(i)" :x2="x(i)" :y1="y(c.high)" :y2="y(c.low)" stroke="currentColor" stroke-width="1" :class="c.close>=c.open ? 'text-emerald-400':'text-rose-400'" />
              <rect :x="x(i)-barW/2" :y="y(Math.max(c.open,c.close))" :width="barW" :height="Math.max(1, Math.abs(y(c.open)-y(c.close)))" :class="c.close>=c.open ? 'fill-emerald-500/50':'fill-rose-500/50'" />
            </g>
          </svg>
        </div>

        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60 overflow-auto">
          <h2 class="text-xs font-semibold text-neutral-300 mb-2">Recent Candles (latest last)</h2>
          <table class="w-full text-[10px] border-collapse">
            <thead class="text-neutral-400">
              <tr>
                <th class="text-left font-normal pb-1">Time</th>
                <th class="text-right font-normal pb-1">Open</th>
                <th class="text-right font-normal pb-1">High</th>
                <th class="text-right font-normal pb-1">Low</th>
                <th class="text-right font-normal pb-1">Close</th>
                <th class="text-right font-normal pb-1">Vol</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in store.candles" :key="c.open_time" class="hover:bg-neutral-800/40">
                <td>{{ fmtTs(c.open_time) }}</td>
                <td class="text-right" :class="closeClass(c)">{{ nf(c.open) }}</td>
                <td class="text-right">{{ nf(c.high) }}</td>
                <td class="text-right">{{ nf(c.low) }}</td>
                <td class="text-right" :class="closeClass(c)">{{ nf(c.close) }}</td>
                <td class="text-right">{{ c.volume!=null? smallNum(c.volume): '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="space-y-4">
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60">
          <h2 class="text-xs font-semibold text-neutral-300 mb-2">Metrics</h2>
          <dl class="text-[11px] space-y-1">
            <div class="flex justify-between"><dt class="text-neutral-500">Pct Change</dt><dd :class="valColor(store.metrics.pct_change)">{{ pctChange }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Avg Volume</dt><dd>{{ store.metrics.avg_volume!=null? smallNum(store.metrics.avg_volume): '-' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Volatility</dt><dd>{{ store.metrics.volatility!=null? store.metrics.volatility.toFixed(4): '-' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Candles</dt><dd>{{ store.candles.length }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Auto</dt><dd>{{ store.auto? 'yes':'no' }}</dd></div>
          </dl>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, computed } from 'vue';
import { useOhlcvStore } from '@/stores/ohlcv';

const store = useOhlcvStore();
const chartW = 800; // virtual width
const chartH = 180;
const barW = 6;

function refresh(){ store.fetchRecent(); }
function fmtTs(ms:number){ const d = new Date(ms); return d.toLocaleTimeString(); }
function nf(v:number){ return v.toFixed(4); }
function smallNum(v:number){ if(v===0) return '0'; const a = Math.abs(v); if(a>=1e6) return (v/1e6).toFixed(2)+'M'; if(a>=1e3) return (v/1e3).toFixed(2)+'K'; return v.toFixed(2); }
function closeClass(c:any){ return c.close>=c.open? 'text-emerald-400':'text-rose-400'; }
const pctChange = computed(()=> store.metrics.pct_change!=null? (store.metrics.pct_change*100).toFixed(2)+'%':'-');
function valColor(v:number|null){ if(v==null) return 'text-neutral-400'; if(v>0) return 'text-emerald-400'; if(v<0) return 'text-rose-400'; return 'text-neutral-300'; }

function x(i:number){ const n = store.candles.length || 1; return (i+0.5) * (chartW / n); }
function y(price:number){ if(!store.range) return chartH/2; const r = store.range; const span = r.high - r.low || 1; return chartH - ((price - r.low) / span) * chartH; }

onMounted(()=>{
  // interval 기본값만 설정 (심볼은 스토어에서 XRPUSDT 고정)
  store.initDefaults('XRPUSDT','1m');
  store.fetchRecent();
  store.startPolling();
});
</script>

<style scoped>
.inp { @apply bg-neutral-900 border border-neutral-700 rounded px-2 py-1 text-xs focus:outline-none focus:border-brand-accent; }
.btn-xs { @apply text-[10px] px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 transition border border-neutral-700; }
</style>
