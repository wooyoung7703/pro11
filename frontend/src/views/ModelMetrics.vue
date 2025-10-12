<template>
  <div class="space-y-6">
    <section class="card">
      <div class="flex items-center justify-between mb-2">
        <h1 class="text-xl font-semibold">Model Metrics</h1>
        <div class="flex items-center gap-3 text-xs">
          <label class="flex items-center gap-1">간격
            <input type="range" min="5" max="60" v-model.number="intervalSec" />
            <span>{{ intervalSec }}s</span>
          </label>
          <label class="flex items-center gap-1">
            <input type="checkbox" v-model="auto" /> 자동
          </label>
          <button class="btn" @click="refresh" :disabled="loading">새로고침</button>
        </div>
      </div>
      <p class="text-sm text-neutral-300">프로덕션 모델 지표 (Prometheus /metrics 파싱)</p>
      <div class="grid grid-cols-2 md:grid-cols-5 gap-4 mt-4 text-sm">
        <MetricBox label="AUC" :value="latest?.auc" />
        <MetricBox label="Accuracy" :value="latest?.accuracy" />
        <MetricBox label="Brier" :value="latest?.brier" />
        <MetricBox label="ECE" :value="latest?.ece" :warn="eceWorse" />
  <MetricBox label="Version" :value="versionDisplay(latest?.version)" />
      </div>
      <div class="mt-6">
        <h2 class="font-semibold mb-2 text-sm">Trend (최근 {{ history.length }}개)</h2>
        <div class="overflow-x-auto">
          <table class="w-full text-xs md:text-sm">
            <thead class="text-neutral-400 text-left">
              <tr>
                <th class="py-1 pr-3">Time</th>
                <th class="py-1 pr-3">AUC</th>
                <th class="py-1 pr-3">ACC</th>
                <th class="py-1 pr-3">Brier</th>
                <th class="py-1 pr-3">ECE</th>
                <th class="py-1 pr-3">Version</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in historySorted" :key="r.ts" class="border-t border-neutral-700/40">
                <td class="py-1 pr-3">{{ new Date(r.ts).toLocaleTimeString() }}</td>
                <td class="py-1 pr-3">{{ fmt(r.auc) }}</td>
                <td class="py-1 pr-3">{{ fmt(r.accuracy) }}</td>
                <td class="py-1 pr-3">{{ fmt(r.brier) }}</td>
                <td class="py-1 pr-3" :class="r.ece_delta && r.ece_delta > 0 ? 'text-brand-danger' : ''">
                  {{ fmt(r.ece) }}
                  <span v-if="r.ece_delta !== null" class="ml-1 text-[10px] px-1 rounded bg-neutral-600/40 text-neutral-300">Δ {{ fmt(r.ece_delta) }}</span>
                </td>
                <td class="py-1 pr-3" :title="String(r.version ?? '')">{{ versionDisplay(r.version) }}</td>
              </tr>
              <tr v-if="!loading && history.length === 0">
                <td colspan="6" class="py-3 text-center text-neutral-400">데이터 없음</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="mt-4 text-xs text-neutral-400 flex flex-wrap gap-4">
        <div v-if="lastUpdated">갱신: {{ new Date(lastUpdated).toLocaleTimeString() }}</div>
        <div>자동: {{ auto ? 'ON':'OFF' }}</div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, onActivated, watch, defineComponent } from 'vue';
import http from '../lib/http';

interface MetricSnapshot { ts: number; auc: number|null; accuracy: number|null; brier: number|null; ece: number|null; version: string|number|null; ece_delta: number|null; }
const history = ref<MetricSnapshot[]>([]);
const loading = ref(false);
const auto = ref(true);
const intervalSec = ref(15);
let timer: any = null;
const lastUpdated = ref<number|null>(null);
const latest = computed(()=> history.value[history.value.length-1]);
const historySorted = computed(()=> [...history.value].sort((a,b)=>a.ts-b.ts));
const eceWorse = computed(()=>{
  if (history.value.length < 2) return false;
  const last = latest.value; const first = history.value[0];
  if (!last?.ece || !first?.ece) return false;
  return (last.ece - first.ece) > 0.02;
});
function fmt(v:any){ if(v===null||v===undefined||Number.isNaN(v)) return '-'; if(typeof v==='number') return v.toFixed(3); return String(v); }
// Extract a Prometheus gauge value, supporting optional labels and scientific notation
function extractGauge(text:string,name:string){
  const re = new RegExp(`^${name}(?:\\{[^}]*\\})?\\s+([-+]?((?:\\d*\\.\\d+)|(?:\\d+))(?:[eE][-+]?\\d+)?)`,'m');
  const m = text.match(re);
  if(!m) return null;
  const v = parseFloat(m[1]);
  return Number.isNaN(v)?null:v;
}
function versionDisplay(v:any){
  if(v===null||v===undefined||Number.isNaN(v)) return '-';
  const num = Number(v);
  if(!Number.isFinite(num)) return String(v);
  // Heuristics: epoch sec ~ 1e9-1e11, epoch ms ~ >1e12
  if(num > 1e12) { try { return new Date(num).toLocaleString(); } catch { return String(num); } }
  if(num > 1e9) { try { return new Date(num*1000).toLocaleString(); } catch { return String(num); } }
  return String(num);
}
// Hydrate persisted state to avoid reset on page refresh
try {
  const hist = localStorage.getItem('model_metrics_history_v1');
  if (hist) {
    const arr = JSON.parse(hist);
    if (Array.isArray(arr)) history.value = arr.slice(-200);
  }
  const a = localStorage.getItem('model_metrics_auto');
  if (a === 'true' || a === 'false') auto.value = (a === 'true');
  const iv = localStorage.getItem('model_metrics_interval');
  if (iv != null) {
    const n = Number(iv);
    if (!Number.isNaN(n) && n >= 5 && n <= 300) intervalSec.value = n;
  }
  if (history.value.length) lastUpdated.value = history.value[history.value.length-1]?.ts ?? null;
} catch {}
async function fetchMetrics(){
  try{
    loading.value=true;
    const resp=await http.get('/metrics',{responseType:'text'} as any);
    const text:string=resp.data as any;
  const snap:MetricSnapshot={ ts:Date.now(), auc:extractGauge(text,'model_production_auc'), accuracy:extractGauge(text,'model_production_accuracy'), brier:extractGauge(text,'model_production_brier'), ece:extractGauge(text,'model_production_ece'), version:extractGauge(text,'model_production_version_timestamp'), ece_delta:null};
    const first=history.value[0];
    if(first&&first.ece!==null&&snap.ece!==null){ snap.ece_delta = snap.ece - first.ece; }
    history.value.push(snap);
    if(history.value.length>200) history.value.shift();
    lastUpdated.value=snap.ts;
    try { localStorage.setItem('model_metrics_history_v1', JSON.stringify(history.value)); } catch {}
  } catch(e){ console.error(e);} finally { loading.value=false; }
}
function refresh(){ fetchMetrics(); }
watch([auto, intervalSec],()=>{
  if(timer) clearInterval(timer);
  try { localStorage.setItem('model_metrics_auto', String(auto.value)); } catch {}
  try { localStorage.setItem('model_metrics_interval', String(intervalSec.value)); } catch {}
  if(auto.value) {
    // Immediate fetch for snappy UX, then schedule
    fetchMetrics();
    timer=setInterval(fetchMetrics, intervalSec.value*1000);
  }
});
onMounted(()=>{
  fetchMetrics();
  if (auto.value) timer=setInterval(fetchMetrics, intervalSec.value*1000);
});
onActivated(()=>{ if (auto.value && !loading.value) fetchMetrics(); });
onBeforeUnmount(()=>{ if (timer) clearInterval(timer); timer=null; });
const MetricBox = defineComponent({
  name: 'MetricBox',
  props: {
    label: { type: String, required: true },
    // value can be number | string | null | undefined; provide default to satisfy default-prop rule
    value: { type: [Number, String, null] as unknown as any, default: null },
    warn: { type: Boolean, default: false },
  },
  setup(props) {
    const display = computed(() => {
      const v: any = props.value as any;
      if (v === null || v === undefined || Number.isNaN(v)) return '-';
      if (typeof v === 'number') return v.toFixed(3);
      return String(v);
    });
    return { display };
  },
  template: `<div class='p-3 rounded bg-neutral-700/40 flex flex-col justify-between'><div class='text-neutral-400 text-xs'>{{ label }}</div><div :class="'font-semibold ' + (warn ? 'text-brand-danger':'')">{{ display }}</div></div>`
});
</script>