<template>
  <div :class="outerClass" class="w-full rounded border px-3 py-2 text-xs flex gap-2 items-start">
    <div class="font-semibold tracking-wide uppercase text-[10px] mt-0.5">{{ typeLabel }}</div>
    <div class="flex-1 space-y-0.5">
      <div class="text-sm font-medium">{{ title }}</div>
      <div v-if="description" class="opacity-80 leading-relaxed whitespace-pre-line">{{ description }}</div>
      <slot />
    </div>
  <button v-if="dismissible" @click="emit('close')" class="opacity-60 hover:opacity-100" aria-label="닫기">✕</button>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue';

// Emit declaration for lint rule vue/require-explicit-emits
const emit = defineEmits<{ (e: 'close'): void }>();

interface Props { type?: 'info'|'warning'|'danger'; title: string; description?: string; dismissible?: boolean }
const props = withDefaults(defineProps<Props>(), { type: 'info', dismissible: false, description: '' });

const outerClass = computed(() => {
  if (props.type === 'warning') return 'bg-amber-500/10 border-amber-500/40 text-amber-300';
  if (props.type === 'danger') return 'bg-brand-danger/10 border-brand-danger/40 text-brand-danger';
  return 'bg-neutral-700/30 border-neutral-600/50 text-neutral-200';
});

const typeLabel = computed(() => {
  if (props.type === 'warning') return '주의';
  if (props.type === 'danger') return '위험';
  return '정보';
});
</script>
<script lang="ts">export default {};</script>
