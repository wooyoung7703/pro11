<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold">DB Settings</h2>
      <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/40 hover:bg-sky-500/30 transition" @click="reloadAll">새로고침</button>
    </div>
    <section class="bg-neutral-900/40 rounded border border-neutral-800 overflow-hidden">
      <div class="p-3 border-b border-neutral-800 bg-neutral-900/60 text-xs text-neutral-400">
        모든 런타임 설정은 DB에서 관리됩니다. 왼쪽 메뉴에서 영역을 선택해 값을 변경하세요.
      </div>
      <div class="flex">
        <aside class="w-56 shrink-0 border-r border-neutral-800 p-2">
          <ul class="space-y-1 text-sm">
            <li><button :class="menuClass('trading')" @click="active='trading'">트레이딩</button></li>
            <li><button :class="menuClass('exit')" @click="active='exit'">익절/청산(Exit)</button></li>
            <li><button :class="menuClass('labeler')" @click="active='labeler'">라벨러</button></li>
            <li><button :class="menuClass('inference')" @click="active='inference'">추론</button></li>
            <li><button :class="menuClass('training')" @click="active='training'">트레이닝</button></li>
            <li><button :class="menuClass('calibration')" @click="active='calibration'">보정</button></li>
            <li><button :class="menuClass('risk')" @click="active='risk'">리스크</button></li>
            <li><button :class="menuClass('drift')" @click="active='drift'">드리프트</button></li>
            <li><button :class="menuClass('bootstrap')" @click="active='bootstrap'">부트스트랩</button></li>
            <li><button :class="menuClass('quick')" @click="active='quick'">퀵 데모</button></li>
            <li><button :class="menuClass('diagnostics')" @click="active='diagnostics'">진단</button></li>
            <li><button :class="menuClass('ohlcv')" @click="active='ohlcv'">OHLCV Controls</button></li>
          </ul>
        </aside>
        <div class="flex-1 p-3">
          <component :is="currentView" :refreshKey="refreshKey" @changed="reloadAll" />
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, defineAsyncComponent } from 'vue';

const Trading = defineAsyncComponent(() => import('./admin-settings/TradingSettings.vue'));
const Exit = defineAsyncComponent(() => import('./admin-settings/ExitSettings.vue'));
const Labeler = defineAsyncComponent(() => import('./admin-settings/LabelerSettings.vue'));
const Inference = defineAsyncComponent(() => import('./admin-settings/InferenceSettings.vue'));
const Calibration = defineAsyncComponent(() => import('./admin-settings/CalibrationSettings.vue'));
const Training = defineAsyncComponent(() => import('./admin-settings/TrainingSettings.vue'));
const Risk = defineAsyncComponent(() => import('./admin-settings/RiskSettings.vue'));
const Drift = defineAsyncComponent(() => import('./admin-settings/DriftSettings.vue'));
const Bootstrap = defineAsyncComponent(() => import('./admin-settings/BootstrapSettings.vue'));
const QuickDemo = defineAsyncComponent(() => import('./admin-settings/QuickDemo.vue'));
const Diagnostics = defineAsyncComponent(() => import('./admin-settings/Diagnostics.vue'));
const OhlcvControls = defineAsyncComponent(() => import('./admin-settings/OhlcvControls.vue'));

const active = ref<'trading'|'exit'|'labeler'|'inference'|'training'|'calibration'|'risk'|'drift'|'bootstrap'|'quick'|'diagnostics'|'ohlcv'>('trading');
const refreshKey = ref(0);
const currentView = computed(() => {
  switch(active.value) {
    case 'trading': return Trading;
    case 'exit': return Exit;
    case 'labeler': return Labeler;
    case 'inference': return Inference;
    case 'training': return Training;
    case 'calibration': return Calibration;
    case 'risk': return Risk;
    case 'drift': return Drift;
    case 'bootstrap': return Bootstrap;
    case 'quick': return QuickDemo;
    case 'diagnostics': return Diagnostics;
    case 'ohlcv': return OhlcvControls;
    default: return Trading;
  }
});

function menuClass(key: string): string {
  const on = active.value === key;
  return `w-full text-left px-3 py-2 rounded ${on ? 'bg-neutral-800 text-brand-accent' : 'text-neutral-300 hover:bg-neutral-800/60'}`;
}
function reloadAll() { refreshKey.value++; }
</script>

<style scoped>
button { cursor: pointer; }
</style>
