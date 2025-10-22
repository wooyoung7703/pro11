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

const windowSec = ref<number>(3600);
const bins = ref<number>(10);
const pollSec = ref<number>(10);
const status = ref('');

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
}

async function save() {
  status.value = '저장 중…';
  const ok1 = await putSetting('calibration.live.window_seconds', Number(windowSec.value));
  const ok2 = await putSetting('calibration.live.bins', Number(bins.value));
  const ok3 = await putSetting('ui.calibration.poll_interval_sec', Number(pollSec.value));
  const ok = ok1 && ok2 && ok3;
  status.value = ok ? '저장 완료' : '일부 실패';
  if (ok) emit('changed');
}

onMounted(load);
watch(() => props.refreshKey, () => load());
</script>
