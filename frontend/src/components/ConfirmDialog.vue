<template>
  <div v-if="open" class="fixed inset-0 z-50 flex items-center justify-center">
    <div class="absolute inset-0 bg-black/60" @click="onCancel"></div>
    <div class="relative w-[380px] max-w-[90vw] rounded border border-neutral-700 bg-neutral-900 p-4 shadow-xl">
      <div class="text-sm font-semibold mb-1">{{ title }}</div>
      <div class="text-[11px] text-neutral-300 whitespace-pre-wrap">{{ message }}</div>
      <div v-if="requireText" class="mt-3">
        <label class="text-[11px] text-neutral-400">확인 단어 입력: <span class="font-mono">{{ requireText }}</span></label>
        <input class="input w-full mt-1" v-model.trim="inputText" @keyup.enter="tryConfirm" />
      </div>
      <div class="flex items-center justify-end gap-2 mt-4 text-[12px]">
        <button class="btn !px-3 !py-1" @click="onCancel">취소</button>
        <button class="btn !px-3 !py-1 !bg-rose-700 hover:!bg-rose-600" :disabled="!canConfirm" @click="tryConfirm">확인</button>
      </div>
    </div>
  </div>
  <!-- buttonless component; controlled by props/emit -->
</template>

<script setup lang="ts">
defineOptions({ name: 'ConfirmDialog' });
import { computed, ref, watch } from 'vue';

const props = defineProps<{ open: boolean; title?: string; message?: string; requireText?: string; delayMs?: number }>();
const emit = defineEmits<{ (e: 'confirm'): void; (e: 'cancel'): void }>();

const inputText = ref('');
const ready = ref(true);
const canConfirm = computed(() => {
  const textOk = props.requireText ? inputText.value === props.requireText : true;
  return ready.value && textOk;
});

watch(() => props.open, (v) => {
  if (v) {
    inputText.value = '';
    ready.value = false;
    const d = Math.max(0, props.delayMs ?? 1200);
    setTimeout(() => { ready.value = true; }, d);
  }
});

function onCancel() { emit('cancel'); }
function tryConfirm() { if (canConfirm.value) emit('confirm'); }
</script>

<style scoped>
.input { background:#111827; border:1px solid #374151; border-radius:6px; padding:6px 8px; color:#e5e7eb; }
.btn { background:#374151; color:#e5e7eb; border-radius:6px; padding:6px 10px; }
.btn:hover{ filter:brightness(1.1); }
</style>
