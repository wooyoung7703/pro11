<template>
  <div class="space-y-3">
    <div class="text-sm text-neutral-400">원클릭 퀵 데모</div>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">Feature backfill target</div>
        <input type="number" step="50" min="200" v-model.number="featureTarget" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">임계값(저장)</div>
        <input type="number" step="0.01" min="0" max="1" v-model.number="threshold" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
      <label class="block">
        <div class="text-xs text-neutral-400 mb-1">ML 쿨다운(초)</div>
        <input type="number" step="1" min="0" v-model.number="cooldown" class="w-full bg-neutral-900 border border-neutral-800 rounded px-2 py-1.5 text-sm" />
      </label>
    </div>
    <div class="flex items-center gap-2">
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 hover:bg-emerald-500/30 transition" @click="runDemo" :disabled="running">
        <span v-if="!running">실행</span>
        <span v-else>진행 중…</span>
      </button>
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-neutral-800 text-neutral-300 border border-neutral-700 hover:bg-neutral-700/80 transition" @click="resetStatus" :disabled="running">초기화</button>
      <span v-if="status" class="text-xs text-neutral-400">{{ status }}</span>
    </div>
    <div v-if="log.length" class="bg-neutral-950/40 border border-neutral-800 rounded p-2 text-xs text-neutral-300 max-h-64 overflow-auto">
      <div v-for="(line,i) in log" :key="i" class="font-mono">{{ line }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import http from '../../lib/http';

const featureTarget = ref<number>(600);
const threshold = ref<number>(0.6);
const cooldown = ref<number>(15);
const status = ref('');
const running = ref(false);
const log = ref<string[]>([]);

function push(msg: string) { log.value.push(`[${new Date().toLocaleTimeString()}] ${msg}`); }
function resetStatus() { status.value=''; log.value = []; }

async function runDemo() {
  if (running.value) return;
  running.value = true; status.value = '시작'; log.value = [];
  try {
    // 0) 옵션 적용: threshold & cooldown 저장/적용
    push('임계값/쿨다운 저장…');
    await Promise.all([
      http.put(`/admin/settings/${encodeURIComponent('inference.auto.threshold')}`, { value: Number(threshold.value), apply: true }),
      http.put(`/admin/settings/${encodeURIComponent('ml.signal.cooldown_sec')}`, { value: Number(cooldown.value), apply: true }),
    ]);

    // 1) 피처 백필
    push(`피처 백필 target=${featureTarget.value}…`);
    const bf = await http.post('/admin/features/backfill', null, { params: { target: Number(featureTarget.value) } });
    push(`백필 상태: ${bf.data?.status || 'unknown'}`);

    // 2) 트레이닝 실행(sync)
    push('트레이닝 실행…');
    const tr = await http.post('/api/training/run', { trigger: 'quick_demo', store: true, sync: true });
    push(`트레이닝 결과: ${tr.data?.status || 'unknown'}`);

    // 3) 프로모션 결과 로그
    const promo = tr.data?.promotion;
    if (promo) push(`프로모션: ${JSON.stringify(promo)}`);

    // 4) 미리보기 한 번 호출
    push('ML 미리보기…');
    const pv = await http.post('/api/trading/ml/preview', {});
    push(`미리보기 p=${pv.data?.probability} thr=${pv.data?.threshold} decision=${pv.data?.decision}`);

    status.value = '완료';
  } catch (e: any) {
    status.value = '실패';
    push(`에러: ${e?.message || e}`);
  } finally {
    running.value = false;
  }
}
</script>
