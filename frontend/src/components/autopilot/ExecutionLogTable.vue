<template>
  <div class="log-table">
    <header>
      <h3>실행 로그</h3>
    </header>
    <div v-if="rows.length" class="table-wrapper">
      <table>
        <thead>
          <tr>
            <th>시간</th>
            <th>타입</th>
            <th>요청</th>
            <th>응답</th>
            <th>세부 정보</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="`${row.ts}-${row.type}`">
            <td>{{ formatTime(row.ts) }}</td>
            <td>{{ row.type }}</td>
            <td><code>{{ row.request }}</code></td>
            <td><code>{{ row.response }}</code></td>
            <td><code>{{ row.detail }}</code></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="empty">로그가 없습니다.</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import type { AutopilotEvent } from '@/types/autopilot';

const props = defineProps<{ events: AutopilotEvent[] }>();

interface LogRow {
  ts: number;
  type: string;
  request: string;
  response: string;
  detail: string;
}

const rows = computed<LogRow[]>(() => props.events
  .slice(-50)
  .reverse()
  .map((evt) => buildRow(evt))
);

const timeFormatter = new Intl.DateTimeFormat('ko-KR', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

function formatTime(ts: number) {
  return timeFormatter.format(new Date(ts * 1000));
}

function buildRow(evt: AutopilotEvent): LogRow {
  const payload = evt.payload || {};
  const [reqVal, reqKey] = pickField(payload, ['request', 'req', 'input', 'params', 'order']);
  const [resVal, resKey] = pickField(payload, ['response', 'res', 'result', 'data', 'error']);
  const detailPayload = { ...payload } as Record<string, unknown>;
  if(reqKey) delete detailPayload[reqKey];
  if(resKey) delete detailPayload[resKey];
  const requestText = formatValue(reqVal);
  const responseText = formatValue(resVal);
  const fallbackDetail = formatValue(payload);
  let detailText = formatValue(isEmpty(detailPayload) ? null : detailPayload);
  if(detailText === '—' && requestText === '—' && responseText === '—') {
    detailText = fallbackDetail;
  }

  return {
    ts: evt.ts,
    type: evt.type,
    request: requestText,
    response: responseText,
    detail: detailText,
  };
}

function pickField(payload: Record<string, unknown>, keys: string[]): [unknown, string | null] {
  for(const key of keys) {
    if(Object.prototype.hasOwnProperty.call(payload, key)) {
      return [payload[key], key];
    }
  }
  return [null, null];
}

function isEmpty(obj: Record<string, unknown>) {
  for(const key in obj) {
    if(Object.prototype.hasOwnProperty.call(obj, key)) return false;
  }
  return true;
}

function formatValue(value: unknown): string {
  if(value == null) return '—';
  if(typeof value === 'string') {
    return value.length > 120 ? `${value.slice(0, 117)}…` : value;
  }
  try {
    const json = JSON.stringify(value);
    return json.length > 120 ? `${json.slice(0, 117)}…` : json;
  } catch (err) {
    return String(value);
  }
}
</script>

<style scoped>
.log-table {
  background: #111d2e;
  border-radius: 10px;
  padding: 12px;
  border: 1px solid rgba(60, 86, 125, 0.4);
  color: #d2ddf0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 220px;
}
header h3 {
  margin: 0 0 8px 0;
  font-size: 15px;
}
.table-wrapper {
  max-height: 240px;
  overflow-y: auto;
}
.table-wrapper table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.table-wrapper thead th {
  position: sticky;
  top: 0;
  background: #141f33;
  z-index: 1;
}
th, td {
  padding: 8px;
  text-align: left;
  border-bottom: 1px solid rgba(70, 96, 140, 0.2);
}
th {
  color: #8fa3c0;
  font-weight: 500;
}
code {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  color: #aec6ff;
  white-space: pre-wrap;
  word-break: break-word;
  display: block;
}
.empty {
  text-align: center;
  color: #6f7f98;
  padding: 20px 0;
}
</style>
