<template>
  <div class="fixed top-2 right-2 z-50 flex flex-col gap-2 w-72" role="region" aria-label="Notifications">
    <transition-group name="toast-fade" tag="div">
      <div v-for="t in toasts" :key="t.id"
           class="flex items-start gap-2 rounded border px-3 py-2 shadow text-sm"
           :class="wrapperClass(t.type)"
           :aria-live="t.type==='error' ? 'assertive' : 'polite'">
        <div class="mt-0.5 text-xs font-semibold uppercase tracking-wide" :class="badgeClass(t.type)">{{ label(t.type) }}</div>
        <div class="flex-1">
          <div class="font-medium" v-text="t.message" />
          <div v-if="t.detail" class="text-xs opacity-80 whitespace-pre-line" v-text="t.detail" />
        </div>
        <button class="text-neutral-400 hover:text-neutral-200" @click="close(t.id)" aria-label="Dismiss">✕</button>
      </div>
    </transition-group>
  </div>
</template>
<script setup lang="ts">
import { storeToRefs } from 'pinia';
import { useToastStore } from '../stores/toast';

const toastStore = useToastStore();
const { items: toasts } = storeToRefs(toastStore);

function close(id: string) { toastStore.remove(id); }
function label(type: string) {
  switch(type){
    case 'success': return '성공';
    case 'warning': return '경고';
    case 'error': return '오류';
    default: return '알림';
  }
}
function wrapperClass(type: string){
  const base = 'bg-neutral-900/95 border-neutral-700';
  const map: Record<string,string> = {
    info: base,
    success: base + ' border-emerald-600/60',
    warning: base + ' border-amber-600/60',
    error: base + ' border-rose-600/70',
  };
  return map[type] || base;
}
function badgeClass(type: string){
  const map: Record<string,string> = {
    info: 'text-brand-primary',
    success: 'text-emerald-400',
    warning: 'text-amber-400',
    error: 'text-rose-400',
  };
  return map[type] || 'text-brand-primary';
}
</script>
<script lang="ts">
export default {};
</script>
<style>
.toast-fade-enter-active, .toast-fade-leave-active { transition: all 0.18s ease; }
.toast-fade-enter-from { opacity: 0; transform: translateY(-6px); }
.toast-fade-leave-to { opacity: 0; transform: translateY(-6px); }
</style>
