<template>
  <div class="space-y-3">
    <div class="text-sm text-neutral-400">부트스트랩 기본값</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">feature target</div>
        <input type="number" step="50" min="100" v-model.number="featureTarget" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">feature window (override)</div>
        <input type="number" step="1" min="0" v-model.number="featureWindow" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">min AUC</div>
        <input type="number" step="0.01" min="0" max="1" v-model.number="minAuc" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">max ECE</div>
        <input type="number" step="0.01" min="0" max="1" v-model.number="maxEce" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-3 gap-3">
      <label class="flex items-center gap-2 text-sm"><input type="checkbox" v-model="backfillYear" /> year backfill</label>
      <label class="flex items-center gap-2 text-sm"><input type="checkbox" v-model="fillGaps" /> fill gaps</label>
      <label class="flex items-center gap-2 text-sm"><input type="checkbox" v-model="retryFillGaps" /> retry gaps</label>
      <label class="flex items-center gap-2 text-sm"><input type="checkbox" v-model="trainSentiment" /> sentiment</label>
      <label class="flex items-center gap-2 text-sm"><input type="checkbox" v-model="skipPromotion" /> skip promotion</label>
    </div>
    <div class="flex items-center gap-2">
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/40 hover:bg-sky-500/30 transition" @click="save">저장</button>
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-neutral-800 text-neutral-300 border border-neutral-700 hover:bg-neutral-700/80 transition" @click="load">초기화</button>
      <span v-if="status" class="text-xs text-neutral-400">{{ status }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, watch } from 'vue';
import http from '../../lib/http';

const props = defineProps<{ refreshKey?: number }>();
const emit = defineEmits<{ (e: 'changed'): void }>();

const featureTarget = ref<number>(400);
const featureWindow = ref<number|null>(null);
const minAuc = ref<number>(0.65);
const maxEce = ref<number>(0.05);
const backfillYear = ref<boolean>(false);
const fillGaps = ref<boolean>(true);
const retryFillGaps = ref<boolean>(true);
const trainSentiment = ref<boolean>(false);
const skipPromotion = ref<boolean>(false);
const status = ref('');

async function getSetting(key: string): Promise<any|undefined> {
  try { const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`); return data?.item?.value; } catch { return undefined; }
}
async function putSetting(key: string, value: any): Promise<boolean> {
  try { const { data } = await http.put(`/admin/settings/${encodeURIComponent(key)}`, { value, apply: false }); return !!data?.status; } catch { return false; }
}

async function load() {
  status.value = '';
  const ft = await getSetting('bootstrap.feature_target'); if (typeof ft === 'number') featureTarget.value = ft;
  const fw = await getSetting('bootstrap.feature_window'); if (typeof fw === 'number' || fw === null) featureWindow.value = fw;
  const a = await getSetting('bootstrap.min_auc'); if (typeof a === 'number') minAuc.value = a;
  const e = await getSetting('bootstrap.max_ece'); if (typeof e === 'number') maxEce.value = e;
  const y = await getSetting('bootstrap.backfill_year'); if (typeof y === 'boolean') backfillYear.value = y;
  const fg = await getSetting('bootstrap.fill_gaps'); if (typeof fg === 'boolean') fillGaps.value = fg;
  const rfg = await getSetting('bootstrap.retry_fill_gaps'); if (typeof rfg === 'boolean') retryFillGaps.value = rfg;
  const s = await getSetting('bootstrap.train_sentiment'); if (typeof s === 'boolean') trainSentiment.value = s;
  const sp = await getSetting('bootstrap.skip_promotion'); if (typeof sp === 'boolean') skipPromotion.value = sp;
}

async function save() {
  status.value = '저장 중…';
  const oks = await Promise.all([
    putSetting('bootstrap.feature_target', Number(featureTarget.value)),
    putSetting('bootstrap.feature_window', featureWindow.value === null ? null : Number(featureWindow.value)),
    putSetting('bootstrap.min_auc', Number(minAuc.value)),
    putSetting('bootstrap.max_ece', Number(maxEce.value)),
    putSetting('bootstrap.backfill_year', Boolean(backfillYear.value)),
    putSetting('bootstrap.fill_gaps', Boolean(fillGaps.value)),
    putSetting('bootstrap.retry_fill_gaps', Boolean(retryFillGaps.value)),
    putSetting('bootstrap.train_sentiment', Boolean(trainSentiment.value)),
    putSetting('bootstrap.skip_promotion', Boolean(skipPromotion.value)),
  ]);
  const ok = oks.every(Boolean);
  status.value = ok ? '저장 완료' : '일부 실패';
  if (ok) emit('changed');
}

onMounted(load);
watch(() => props.refreshKey, () => load());
</script>
