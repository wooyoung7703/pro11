<template>
  <div class="space-y-3">
    <div class="text-sm text-neutral-400">드리프트(Feature Drift) 기본값</div>
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">윈도우 크기 (window)</div>
        <input type="number" step="10" min="50" v-model.number="windowSize" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">임계값 |Z| (threshold)</div>
        <input type="number" step="0.1" min="0.1" v-model.number="threshold" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block md:col-span-2 lg:col-span-1 flex items-center gap-2 pt-5">
        <input id="autoEnabled" type="checkbox" v-model="autoEnabled" />
        <label for="autoEnabled" class="text-xs text-neutral-300">자동 스캔 활성화</label>
      </label>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">자동 스캔 주기(초)</div>
        <input type="number" step="10" min="10" :disabled="!autoEnabled" v-model.number="autoInterval" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm disabled:opacity-50" />
      </label>
      <label class="block md:col-span-2">
        <div class="text-xs text-neutral-400 mb-1">피처 목록 (쉼표 구분)</div>
        <input type="text" v-model="featuresText" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm font-mono" placeholder="ret_1,ret_5,ret_10,rsi_14,rolling_vol_20,ma_20,ma_50" />
      </label>
    </div>

    <div class="flex items-center gap-2">
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/40 hover:bg-sky-500/30 transition" @click="save" :disabled="saving">저장</button>
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-neutral-800 text-neutral-300 border border-neutral-700 hover:bg-neutral-700/80 transition" @click="saveAndQuickScan" :disabled="saving || scanning">저장 후 퀵 스캔</button>
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-neutral-800 text-neutral-300 border border-neutral-700 hover:bg-neutral-700/80 transition" @click="load">초기화</button>
      <span v-if="status" class="text-xs text-neutral-400">{{ status }}</span>
      <span v-if="scanSummary" class="text-xs text-neutral-400">
        drift={{ scanSummary.drift_count }}/{{ scanSummary.total }}, max|Z|={{ scanSummary.max_abs_z != null ? scanSummary.max_abs_z.toFixed(2) : '-' }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue';
import http from '../../lib/http';

const props = defineProps<{ refreshKey?: number }>();
const emit = defineEmits<{ (e: 'changed'): void }>();

const windowSize = ref<number>(200);
const threshold = ref<number>(3.0);
const featuresText = ref<string>('ret_1,ret_5,ret_10,rsi_14,rolling_vol_20,ma_20,ma_50');
const autoEnabled = ref<boolean>(true);
const autoInterval = ref<number>(60);
const status = ref('');
const saving = ref(false);
const scanning = ref(false);
const scanSummary = ref<{ drift_count: number; total: number; max_abs_z: number | null } | null>(null);

async function getSetting(key: string): Promise<any|undefined> {
  try { const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`); return data?.item?.value; } catch { return undefined; }
}
async function putSetting(key: string, value: any): Promise<boolean> {
  try { const { data } = await http.put(`/admin/settings/${encodeURIComponent(key)}`, { value, apply: true }); return !!data?.status; } catch { return false; }
}

function normFeatures(s: any): string {
  if (typeof s !== 'string') return '';
  return s.split(',').map(v => v.trim()).filter(Boolean).join(',');
}

async function load() {
  status.value = '';
  const w = await getSetting('drift.window'); if (typeof w === 'number') windowSize.value = w;
  const t = await getSetting('drift.threshold'); if (typeof t === 'number') threshold.value = t;
  const f = await getSetting('drift.features'); if (typeof f === 'string') featuresText.value = normFeatures(f) || featuresText.value;
  const ae = await getSetting('drift.auto.enabled'); if (typeof ae === 'boolean') autoEnabled.value = ae;
  const ai = await getSetting('drift.auto.interval_sec'); if (typeof ai === 'number') autoInterval.value = ai;
}

async function save() {
  status.value = '저장 중…';
  saving.value = true;
  try {
    const oks = await Promise.all([
      putSetting('drift.window', Number(windowSize.value)),
      putSetting('drift.threshold', Number(threshold.value)),
      putSetting('drift.features', normFeatures(featuresText.value)),
      putSetting('drift.auto.enabled', Boolean(autoEnabled.value)),
      putSetting('drift.auto.interval_sec', Number(autoInterval.value)),
    ]);
    const ok = oks.every(Boolean);
    status.value = ok ? '저장 완료' : '일부 실패';
    if (ok) emit('changed');
  } finally {
    saving.value = false;
  }
}

onMounted(load);
watch(() => props.refreshKey, () => load());

async function saveAndQuickScan() {
  await save();
  try {
    scanning.value = true;
    const feats = normFeatures(featuresText.value) || 'ret_1,ret_5,ret_10,rsi_14,rolling_vol_20,ma_20,ma_50';
    const params: any = { window: Number(windowSize.value), features: feats };
    const t = Number(threshold.value);
    if (Number.isFinite(t) && t > 0) params.threshold = t;
    const { data } = await http.get('/api/features/drift/scan', { params });
    if (data && data.summary) {
      scanSummary.value = {
        drift_count: Number(data.summary.drift_count || 0),
        total: Number(data.summary.total || 0),
        max_abs_z: typeof data.summary.max_abs_z === 'number' ? data.summary.max_abs_z : null,
      };
    } else {
      scanSummary.value = null;
    }
  } finally {
    scanning.value = false;
  }
}
</script>
