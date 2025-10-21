<template>
  <div class="flex items-center gap-2 text-[11px] select-none">
    <span :class="dotClass" class="inline-block w-2.5 h-2.5 rounded-full"></span>
    <span :class="textClass" :title="tooltip">
      {{ label }}
    </span>
    <button class="px-1.5 py-0.5 text-[10px] rounded border border-neutral-700 hover:bg-neutral-700/30" @click="refresh" :disabled="loading">갱신</button>
  </div>
  
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { useBackendStatus } from '../composables/useBackendStatus';

const { serverOk, readyStatus, reasons, authOk, loading, start, pollOnce } = useBackendStatus(15000);

onMounted(() => {
  start();
});

const label = computed(() => {
  if (serverOk.value === false) return 'Backend down';
  if (readyStatus.value === 'not_ready') return 'Not ready';
  if (serverOk.value && authOk.value === false) return 'Auth needed';
  if (serverOk.value && readyStatus.value === 'ready' && authOk.value) return 'OK';
  return 'Checking…';
});

const tooltip = computed(() => {
  const items: string[] = [];
  if (readyStatus.value === 'not_ready' && reasons.value.length) items.push('reasons: ' + reasons.value.join(','));
  if (authOk.value === false) items.push('X-API-Key가 필요합니다. 우측 상단 API Key 입력란 확인.');
  return items.join(' | ');
});

const dotClass = computed(() => {
  if (serverOk.value === false) return 'bg-red-500';
  if (readyStatus.value === 'not_ready') return 'bg-amber-400';
  if (serverOk.value && authOk.value === false) return 'bg-amber-400';
  if (serverOk.value && readyStatus.value === 'ready' && authOk.value) return 'bg-emerald-500';
  return 'bg-neutral-400';
});

const textClass = computed(() => {
  if (serverOk.value === false) return 'text-red-400';
  if (readyStatus.value === 'not_ready') return 'text-amber-300';
  if (serverOk.value && authOk.value === false) return 'text-amber-300';
  if (serverOk.value && readyStatus.value === 'ready' && authOk.value) return 'text-emerald-300';
  return 'text-neutral-300';
});

function refresh() { pollOnce(); }
</script>

<style scoped>
</style>
