<template>
  <div class="space-y-3">
    <div class="text-sm text-neutral-400">트레이닝 런타임 기본값</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">min labels (프로모션 기준)</div>
        <input type="number" step="10" min="10" v-model.number="minLabels" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">min train labels (학습 최소)</div>
        <input type="number" step="10" min="10" v-model.number="minTrainLabels" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">OHLCV fetch cap</div>
        <input type="number" step="100" min="300" v-model.number="ohlcvCap" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
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

const minLabels = ref<number>(150);
const minTrainLabels = ref<number>(80);
const ohlcvCap = ref<number>(5000);
const status = ref('');

async function getSetting(key: string): Promise<any|undefined> {
  try { const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`); return data?.item?.value; } catch { return undefined; }
}
async function putSetting(key: string, value: any): Promise<boolean> {
  try { const { data } = await http.put(`/admin/settings/${encodeURIComponent(key)}`, { value, apply: true }); return !!data?.status; } catch { return false; }
}

async function load() {
  status.value = '';
  const a = await getSetting('training.bottom.min_labels'); if (typeof a === 'number') minLabels.value = a;
  const b = await getSetting('training.bottom.min_train_labels'); if (typeof b === 'number') minTrainLabels.value = b;
  const c = await getSetting('training.bottom.ohlcv_fetch_cap'); if (typeof c === 'number') ohlcvCap.value = c;
}

async function save() {
  status.value = '저장 중…';
  const oks = await Promise.all([
    putSetting('training.bottom.min_labels', Number(minLabels.value)),
    putSetting('training.bottom.min_train_labels', Number(minTrainLabels.value)),
    putSetting('training.bottom.ohlcv_fetch_cap', Number(ohlcvCap.value)),
  ]);
  const ok = oks.every(Boolean);
  status.value = ok ? '저장 완료' : '일부 실패';
  if (ok) emit('changed');
}

onMounted(load);
watch(() => props.refreshKey, () => load());
</script>
