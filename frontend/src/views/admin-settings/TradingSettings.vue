<template>
  <div class="space-y-3">
    <div class="text-sm text-neutral-400">라이브 트레이딩 설정</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">활성화</div>
        <select v-model="enabledStr" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm">
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">쿨다운(초)</div>
        <input type="number" step="1" v-model.number="cooldown" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">기본 크기</div>
        <input type="number" step="0.0001" v-model.number="baseSize" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">트레일링 TP(%)</div>
        <input type="number" step="0.01" v-model.number="trailingTpPct" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">최대 보유(초)</div>
        <input type="number" step="1" v-model.number="maxHoldingSec" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
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
import { ref, watch, onMounted } from 'vue';
import http from '../../lib/http';

const props = defineProps<{ refreshKey?: number }>();
const emit = defineEmits<{ (e: 'changed'): void }>();

const enabledStr = ref<'true'|'false'>('false');
const cooldown = ref<number>(60);
const baseSize = ref<number>(1.0);
const trailingTpPct = ref<number>(0.0);
const maxHoldingSec = ref<number>(0);
const status = ref('');

async function getSetting(key: string): Promise<any|undefined> {
  try {
    const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`);
    return data?.item?.value;
  } catch { return undefined; }
}
async function putSetting(key: string, value: any): Promise<boolean> {
  try {
    const { data } = await http.put(`/admin/settings/${encodeURIComponent(key)}`, { value, apply: true });
    return !!data?.status;
  } catch { return false; }
}

async function load() {
  status.value = '';
  const en = await getSetting('live_trading.enabled');
  if (typeof en === 'boolean') enabledStr.value = en ? 'true' : 'false';
  const cd = await getSetting('live_trading.cooldown_sec');
  if (typeof cd === 'number') cooldown.value = cd;
  const bs = await getSetting('live_trading.base_size');
  if (typeof bs === 'number') baseSize.value = bs;
  const tp = await getSetting('live_trading.trailing_take_profit_pct');
  if (typeof tp === 'number') trailingTpPct.value = tp * 100.0; // display in %
  const mh = await getSetting('live_trading.max_holding_seconds');
  if (typeof mh === 'number') maxHoldingSec.value = mh;
}

async function save() {
  status.value = '저장 중…';
  const ok1 = await putSetting('live_trading.enabled', enabledStr.value === 'true');
  const ok2 = await putSetting('live_trading.cooldown_sec', Number(cooldown.value));
  const ok3 = await putSetting('live_trading.base_size', Number(baseSize.value));
  const ok4 = await putSetting('live_trading.trailing_take_profit_pct', Number(trailingTpPct.value) / 100.0);
  const ok5 = await putSetting('live_trading.max_holding_seconds', Number(maxHoldingSec.value));
  const ok = ok1 && ok2 && ok3 && ok4 && ok5;
  status.value = ok ? '저장 완료' : '일부 실패';
  if (ok) emit('changed');
}

onMounted(load);
watch(() => props.refreshKey, () => load());
</script>
