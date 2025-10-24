<template>
  <div class="health bg-brand-700/80 border border-slate-700 rounded-xl text-slate-200">
    <header>
      <h3>시스템 상태</h3>
    </header>
    <dl v-if="entries.length">
      <div v-for="item in entries" :key="item.key" class="row">
        <dt>{{ item.key }}</dt>
        <dd>{{ item.value }}</dd>
      </div>
    </dl>
    <div v-else class="empty">상태 정보가 없습니다.</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{ health: Record<string, unknown> }>();

const entries = computed(() => Object.entries(props.health || {}).map(([key, value]) => ({ key, value: formatValue(value) })));

function formatValue(value: unknown) {
  if (typeof value === 'boolean') return value ? 'OK' : 'OFF';
  if (typeof value === 'number') return value.toString();
  if (value == null) return '-';
  return String(value);
}
</script>

<style scoped>
.health { border-radius: 10px; padding: 12px; min-height: 220px; display: flex; flex-direction: column; gap: 8px; }
header h3 {
  margin: 0 0 8px 0;
  font-size: 15px;
}
dl {
  margin: 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 12px;
  flex: 1;
}
.row {
  display: contents;
}
dt {
  font-size: 11px;
  color: #7f8ea8;
}
dd {
  margin: 0;
  font-size: 12px;
  color: #cbd7eb;
}
.empty {
  text-align: center;
  color: #6f7f98;
  padding: 20px 0;
  margin-top: auto;
}
</style>
