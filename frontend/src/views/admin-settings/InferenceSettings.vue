추론 플레이그라운드<template>
  <div class="space-y-3">
    <div class="text-sm text-neutral-400">추론 설정</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1 flex items-center gap-2">
          <span>자동 루프 임계값 (0..1)</span>
          <span v-if="effective!=null" class="text-[11px] text-neutral-500">현재 적용: <span class="font-mono text-neutral-300">{{ effective }}</span></span>
        </div>
        <input type="number" step="0.001" min="0" max="1" v-model.number="threshold" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
        <div class="mt-1 text-[11px] text-neutral-500">
          <span v-if="sourceText">출처: {{ sourceText }}</span>
        </div>
      </label>
    </div>
    <div class="flex items-center gap-2">
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/40 hover:bg-sky-500/30 transition" @click="save">적용</button>
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

const threshold = ref<number>(0.5);
const status = ref('');
const effective = ref<number|null>(null);
const sourceText = ref<string>('');

async function getSetting(key: string): Promise<any|undefined> {
  try { const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`); return data?.item?.value; } catch { return undefined; }
}
async function putSetting(key: string, value: any): Promise<boolean> {
  try { const { data } = await http.put(`/admin/settings/${encodeURIComponent(key)}`, { value, apply: true }); return !!data?.status; } catch { return false; }
}
async function getEffective(): Promise<void> {
  try {
    const { data } = await http.get('/admin/inference/settings');
    const thr = data?.threshold || {};
    const eff = (typeof thr.effective_default === 'number') ? thr.effective_default : (typeof thr.effective === 'number' ? thr.effective : null);
    effective.value = eff ?? null;
    // Determine source: prefer DB setting if present
    const dbVal = await getSetting('inference.auto.threshold');
    if (typeof dbVal === 'number') sourceText.value = 'DB 설정';
    else sourceText.value = '환경/설정 기본값';
  } catch { effective.value = null; sourceText.value = ''; }
}

async function load() {
  status.value = '';
  const v = await getSetting('inference.auto.threshold');
  if (typeof v === 'number') threshold.value = v;
  await getEffective();
}
async function save() {
  status.value = '저장 중…';
  const ok = await putSetting('inference.auto.threshold', Number(threshold.value));
  status.value = ok ? '저장 완료' : '실패';
  if (ok) { emit('changed'); await getEffective(); }
}

// removed per request: copy-from-effective UX

onMounted(load);
watch(() => props.refreshKey, () => load());
</script>
