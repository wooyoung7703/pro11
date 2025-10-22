<template>
  <div class="space-y-3">
    <div class="text-sm text-neutral-400">라벨러 설정</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">주기(초)</div>
        <input type="number" step="0.1" v-model.number="interval" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">최소 대기(초)</div>
        <input type="number" step="1" v-model.number="minAge" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">배치 제한</div>
        <input type="number" step="1" v-model.number="batchLimit" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">Lookahead</div>
        <input type="number" step="1" v-model.number="lookahead" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">Drawdown</div>
        <input type="number" step="0.0001" v-model.number="drawdown" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">Rebound</div>
        <input type="number" step="0.0001" v-model.number="rebound" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
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

const interval = ref<number>(30.0);
const minAge = ref<number>(900);
const batchLimit = ref<number>(200);
const lookahead = ref<number>(30);
const drawdown = ref<number>(0.005);
const rebound = ref<number>(0.003);
const status = ref('');

async function getSetting(key: string): Promise<any|undefined> {
  try { const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`); return data?.item?.value; } catch { return undefined; }
}
async function putSetting(key: string, value: any): Promise<boolean> {
  try { const { data } = await http.put(`/admin/settings/${encodeURIComponent(key)}`, { value, apply: true }); return !!data?.status; } catch { return false; }
}

async function load() {
  status.value = '';
  const v1 = await getSetting('labeler.interval'); if (typeof v1 === 'number') interval.value = v1;
  const v2 = await getSetting('labeler.min_age_seconds'); if (typeof v2 === 'number') minAge.value = v2;
  const v3 = await getSetting('labeler.batch_limit'); if (typeof v3 === 'number') batchLimit.value = v3;
  const v4 = await getSetting('labeler.bottom.lookahead'); if (typeof v4 === 'number') lookahead.value = v4;
  const v5 = await getSetting('labeler.bottom.drawdown'); if (typeof v5 === 'number') drawdown.value = v5;
  const v6 = await getSetting('labeler.bottom.rebound'); if (typeof v6 === 'number') rebound.value = v6;
}

async function save() {
  status.value = '저장 중…';
  const oks = await Promise.all([
    putSetting('labeler.interval', Number(interval.value)),
    putSetting('labeler.min_age_seconds', Number(minAge.value)),
    putSetting('labeler.batch_limit', Number(batchLimit.value)),
    putSetting('labeler.bottom.lookahead', Number(lookahead.value)),
    putSetting('labeler.bottom.drawdown', Number(drawdown.value)),
    putSetting('labeler.bottom.rebound', Number(rebound.value)),
  ]);
  const ok = oks.every(Boolean);
  status.value = ok ? '저장 완료' : '일부 실패';
  if (ok) emit('changed');
}

onMounted(load);
watch(() => props.refreshKey, () => load());
</script>
