<template>
  <div class="w-full min-h-[60vh] flex items-center justify-center">
    <div class="flex flex-col items-center gap-4 text-center">
      <div class="w-12 h-12 rounded-full border-4 border-t-transparent border-brand-accent animate-spin" />
      <div class="space-y-1">
        <h2 class="text-lg font-semibold">초기화 중…</h2>
        <p class="text-sm text-neutral-400">모델 생성 대기 중입니다. 잠시만 기다려 주세요.</p>
        <p v-if="hint" class="text-xs text-neutral-500">{{ hint }}</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ hint?: string | null }>();
</script>

<style scoped>
</style>
