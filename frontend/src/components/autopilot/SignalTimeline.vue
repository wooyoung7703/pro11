<template>
  <div class="timeline">
    <header>
      <h3>시그널 타임라인</h3>
      <span class="count">{{ items.length }} events</span>
    </header>
    <ul>
      <li v-for="evt in items" :key="`${evt.ts}-${evt.type}`">
        <div class="meta">
          <span class="type">{{ evt.type }}</span>
          <span class="ts">{{ format(evt.ts) }}</span>
        </div>
        <pre class="payload">{{ prettyPayload(evt.payload) }}</pre>
      </li>
    </ul>
    <div v-if="!items.length" class="empty">최근 시그널이 없습니다.</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { AutopilotEvent } from '@/types/autopilot';

const props = defineProps<{ events: AutopilotEvent[] }>();

const items = computed(() => [...props.events].slice(-20).reverse());

const formatter = new Intl.DateTimeFormat('ko-KR', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

function format(ts: number) {
  return formatter.format(new Date(ts * 1000));
}

function prettyPayload(payload: Record<string, unknown>) {
  try {
    return JSON.stringify(payload, null, 2);
  } catch (err) {
    return String(payload ?? '');
  }
}
</script>

<style scoped>
.timeline {
  background: #111d2e;
  border-radius: 10px;
  padding: 12px;
  border: 1px solid rgba(60, 86, 125, 0.4);
  color: #d2ddf0;
  display: flex;
  flex-direction: column;
  min-height: 260px;
  max-height: 260px;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 8px;
}
header h3 {
  margin: 0;
  font-size: 15px;
}
.count {
  font-size: 11px;
  color: #7f8ea8;
}
ul {
  list-style: none;
  padding: 0;
  margin: 0;
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
li {
  background: rgba(19, 32, 51, 0.85);
  border-radius: 6px;
  padding: 8px;
  border: 1px solid rgba(70, 96, 140, 0.35);
}
.meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #9cb2d6;
  margin-bottom: 4px;
}
.type {
  font-weight: 600;
  text-transform: uppercase;
}
.payload {
  margin: 0;
  white-space: pre-wrap;
  font-size: 11px;
  color: #aebed9;
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
}
.empty {
  text-align: center;
  color: #6f7f98;
  padding: 16px 0;
  margin-top: auto;
}
</style>
