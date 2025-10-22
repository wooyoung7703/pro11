<template>
  <div class="space-y-3">
    <div class="text-sm text-neutral-400">리스크 한도 설정</div>
    <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">최대 익스포저 (max_notional)</div>
        <input type="number" step="1" min="0" v-model.number="maxNotional" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">일일 손실 한도 (max_daily_loss)</div>
        <input type="number" step="1" min="0" v-model.number="maxDailyLoss" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">최대 드로우다운 비율 (0-1)</div>
        <input type="number" step="0.01" min="0" max="1" v-model.number="maxDrawdown" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">ATR 배수</div>
        <input type="number" step="0.1" min="0" v-model.number="atrMultiple" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
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

const maxNotional = ref<number>(50000);
const maxDailyLoss = ref<number>(2000);
const maxDrawdown = ref<number>(0.2);
const atrMultiple = ref<number>(2.0);
const status = ref('');

async function getSetting(key: string): Promise<any|undefined> {
  try { const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`); return data?.item?.value; } catch { return undefined; }
}
async function putSetting(key: string, value: any): Promise<boolean> {
  try { const { data } = await http.put(`/admin/settings/${encodeURIComponent(key)}`, { value, apply: true }); return !!data?.status; } catch { return false; }
}

async function load() {
  status.value = '';
  const mn = await getSetting('risk.max_notional'); if (typeof mn === 'number') maxNotional.value = mn;
  const dl = await getSetting('risk.max_daily_loss'); if (typeof dl === 'number') maxDailyLoss.value = dl;
  const md = await getSetting('risk.max_drawdown'); if (typeof md === 'number') maxDrawdown.value = md;
  const am = await getSetting('risk.atr_multiple'); if (typeof am === 'number') atrMultiple.value = am;
}

async function save() {
  status.value = '저장 중…';
  const oks = await Promise.all([
    putSetting('risk.max_notional', Number(maxNotional.value)),
    putSetting('risk.max_daily_loss', Number(maxDailyLoss.value)),
    putSetting('risk.max_drawdown', Number(maxDrawdown.value)),
    putSetting('risk.atr_multiple', Number(atrMultiple.value)),
  ]);
  const ok = oks.every(Boolean);
  status.value = ok ? '저장 완료' : '일부 실패';
  if (ok) emit('changed');
}

onMounted(load);
watch(() => props.refreshKey, () => load());
</script>
