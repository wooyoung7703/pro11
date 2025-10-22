<template>
  <div class="space-y-3">
    <div class="text-[12px] text-neutral-400">OHLCV 제어. 인터벌/리밋은 DB에 저장됩니다. 나머지는 즉시 실행되는 관리 액션입니다.</div>
    <div class="flex flex-wrap items-end gap-2 text-[11px]">
      <label class="flex flex-col">
        <span class="text-neutral-400 text-[10px]">Interval</span>
        <input v-model="ohlcv.interval" class="input !py-1 !px-2 w-24" placeholder="1m" />
      </label>
      <label class="flex flex-col">
        <span class="text-neutral-400 text-[10px]">Limit</span>
        <input type="number" v-model.number="ohlcv.limit" class="input !py-1 !px-2 w-24" min="50" max="2000" />
      </label>
      <button class="btn !py-0.5 !px-2" :disabled="ohlcv.loading" @click="ohlcv.fetchRecent({ includeOpen: true })">Refresh</button>
      <button class="btn !py-0.5 !px-2" @click="ohlcvWsToggle">WS: {{ ohlcvWsStatus }}</button>
      <button class="btn !py-0.5 !px-2" :disabled="ohlcvFilling" @click="ohlcvFillGaps">Gaps</button>
      <button class="btn !py-0.5 !px-2" :disabled="ohlcv.yearBackfillPolling" @click="ohlcv.startYearBackfill()">Year Backfill</button>
    </div>
    <div v-if="ohlcv.yearBackfill" class="text-[10px] text-neutral-400 flex items-center gap-1">
      <span>{{ (ohlcv.yearBackfill.percent||0).toFixed(1) }}%</span>
      <span v-if="ohlcv.yearBackfill.status==='running'">⏳</span>
      <span v-else-if="ohlcv.yearBackfill.status==='success'" class="text-emerald-400">✔</span>
      <span v-else-if="ohlcv.yearBackfill.status==='error'" class="text-red-400">✖</span>
      <span v-if="ohlcvEtaDisplay">ETA {{ ohlcvEtaDisplay }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, watch, ref, computed } from 'vue';
import { useOhlcvStore } from '../../stores/ohlcv';
import { useOhlcvDeltaSync } from '../../composables/useOhlcvDeltaSync';
import http from '../../lib/http';
import { getUiPref, setUiPref } from '../../lib/uiSettings';

defineProps<{ refreshKey?: number }>();

const ohlcv = useOhlcvStore();

// Persist interval/limit as UI prefs in DB
onMounted(async () => {
  try {
    const [iv, lm] = await Promise.all([
      getUiPref<string>('ui.ohlcv.interval'),
      getUiPref<number>('ui.ohlcv.limit'),
    ]);
    if (typeof iv === 'string' && iv.trim()) ohlcv.interval = iv;
    if (typeof lm === 'number' && isFinite(lm) && lm > 0) ohlcv.limit = lm;
  } catch {}
});
watch(() => ohlcv.interval, v => { setUiPref('ui.ohlcv.interval', String(v)).catch(()=>{}); });
watch(() => ohlcv.limit, v => { setUiPref('ui.ohlcv.limit', Number(v)).catch(()=>{}); });

// OHLCV WS / Gaps controls
const { wsCtl } = useOhlcvDeltaSync();
const ohlcvFilling = ref<boolean>(false);
const ohlcvWsStatus = computed(() => wsCtl.connected.value ? 'On' : 'Off');
function ohlcvWsToggle(){ if (wsCtl.connected.value) wsCtl.disconnect(); else wsCtl.connect(); }

const ohlcvEtaDisplay = computed(() => {
  const st: any = ohlcv.yearBackfill;
  if (!st) return '';
  if (!st.eta_seconds) return '';
  const s = Number(st.eta_seconds);
  if (!isFinite(s) || s <= 0) return '';
  const m = Math.floor(s/60); const ss = Math.floor(s%60);
  return `${m}m ${ss}s`;
});

async function ohlcvFillGaps(){
  if (ohlcvFilling.value) return;
  ohlcvFilling.value = true;
  try {
    const url = `/api/ohlcv/gaps/fill?symbol=${encodeURIComponent(ohlcv.symbol)}&interval=${encodeURIComponent(ohlcv.interval)}`;
    await http.post(url);
    await ohlcv.fetchRecent();
    await ohlcv.fetchGaps();
    await ohlcv.fetchMeta();
  } finally { ohlcvFilling.value = false; }
}
</script>
