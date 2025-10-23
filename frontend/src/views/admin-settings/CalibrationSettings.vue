<template>
  <div class="space-y-3">
    <div class="text-sm text-neutral-400">보정(캘리브레이션) 기본값</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">라이브 윈도우(초)</div>
        <input type="number" step="60" min="60" v-model.number="windowSec" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">bins</div>
        <input type="number" step="1" min="5" max="200" v-model.number="bins" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">프론트 폴링 주기(초)</div>
        <input type="number" step="1" min="2" v-model.number="pollSec" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
    </div>

    <div class="text-sm text-neutral-400">빠른 라벨링(eager) 및 모니터 임계값</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      <label class="block flex items-center gap-2">
        <input type="checkbox" v-model="eagerEnabled" />
        <div class="text-xs text-neutral-300">eager.enabled</div>
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">eager.limit</div>
        <input type="number" step="1" min="10" v-model.number="eagerLimit" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">eager.min_age_seconds</div>
        <input type="number" step="10" min="0" v-model.number="eagerMinAgeSec" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">monitor.ece_abs (절대 ΔECE)</div>
        <input type="number" step="0.01" min="0" v-model.number="eceAbs" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">monitor.ece_rel (상대 변화율 0-1)</div>
        <input type="number" step="0.01" min="0" max="1" v-model.number="eceRel" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
    </div>
    <div class="flex items-center gap-2">
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/40 hover:bg-sky-500/30 transition" @click="save">저장</button>
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-neutral-800 text-neutral-300 border border-neutral-700 hover:bg-neutral-700/80 transition" @click="load">DB 기본값 적용</button>
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-neutral-800 text-neutral-300 border border-neutral-700 hover:bg-neutral-700/80 transition" @click="quickCheck" :disabled="checking">라이브 캘리 확인</button>
      <span v-if="status" class="text-xs text-neutral-400">{{ status }}</span>
      <span v-if="checkResult" class="text-xs text-neutral-400">
        ECE={{ fmt(checkResult?.ece) }}, MCE={{ fmt(checkResult?.mce) }}, N={{ checkResult?.n ?? '-' }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue';
import http from '../../lib/http';

const props = defineProps<{ refreshKey?: number }>();
const emit = defineEmits<{ (e: 'changed'): void }>();

const windowSec = ref<number>(3600);
const bins = ref<number>(10);
const pollSec = ref<number>(10);
const eagerEnabled = ref<boolean>(true);
const eagerLimit = ref<number>(500);
const eagerMinAgeSec = ref<number>(120);
const eceAbs = ref<number>(0.05);
const eceRel = ref<number>(0.5);
const status = ref('');
const checking = ref(false);
const checkResult = ref<any | null>(null);

async function getSetting(key: string): Promise<any|undefined> {
  try { const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`); return data?.item?.value; } catch { return undefined; }
}
async function putSetting(key: string, value: any): Promise<boolean> {
  try { const { data } = await http.put(`/admin/settings/${encodeURIComponent(key)}`, { value, apply: true }); return !!data?.status; } catch { return false; }
}

async function load() {
  status.value = '';
  const w = await getSetting('calibration.live.window_seconds'); if (typeof w === 'number') windowSec.value = w;
  const b = await getSetting('calibration.live.bins'); if (typeof b === 'number') bins.value = b;
  const p = await getSetting('ui.calibration.poll_interval_sec'); if (typeof p === 'number') pollSec.value = p;
  const ee = await getSetting('calibration.eager.enabled'); if (typeof ee === 'boolean') eagerEnabled.value = ee;
  const el = await getSetting('calibration.eager.limit'); if (typeof el === 'number') eagerLimit.value = el;
  const ema = await getSetting('calibration.eager.min_age_seconds'); if (typeof ema === 'number') eagerMinAgeSec.value = ema;
  const ea = await getSetting('calibration.monitor.ece_abs'); if (typeof ea === 'number') eceAbs.value = ea;
  const er = await getSetting('calibration.monitor.ece_rel'); if (typeof er === 'number') eceRel.value = er;
}

async function save() {
  status.value = '저장 중…';
  const oks = await Promise.all([
    putSetting('calibration.live.window_seconds', Number(windowSec.value)),
    putSetting('calibration.live.bins', Number(bins.value)),
    putSetting('ui.calibration.poll_interval_sec', Number(pollSec.value)),
    putSetting('calibration.eager.enabled', Boolean(eagerEnabled.value)),
    putSetting('calibration.eager.limit', Number(eagerLimit.value)),
    putSetting('calibration.eager.min_age_seconds', Number(eagerMinAgeSec.value)),
    putSetting('calibration.monitor.ece_abs', Number(eceAbs.value)),
    putSetting('calibration.monitor.ece_rel', Number(eceRel.value)),
  ]);
  const ok = oks.every(Boolean);
  status.value = ok ? '저장 완료' : '일부 실패';
  if (ok) emit('changed');
}

onMounted(load);
watch(() => props.refreshKey, () => load());

async function quickCheck() {
  try {
    checking.value = true;
    const { data } = await http.get('/api/inference/calibration/live');
    checkResult.value = data;
  } finally {
    checking.value = false;
  }
}

function fmt(v: any) {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'number' && Number.isFinite(v)) return v.toFixed(3);
  return String(v);
}
</script>
