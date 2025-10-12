<template>
  <div class="space-y-4">
    <div class="flex flex-wrap items-center gap-3 text-xs">
      <button class="btn" @click="refresh" :disabled="loading">새로고침</button>
      <label class="flex items-center gap-1">
        <input type="checkbox" v-model="auto" /> Auto
      </label>
      <label class="flex items-center gap-1">
        Interval
        <select v-model.number="intervalSec" @change="changeInterval" class="bg-neutral-800 rounded px-1 py-0.5">
          <option :value="3">3s</option>
          <option :value="5">5s</option>
          <option :value="10">10s</option>
          <option :value="15">15s</option>
        </select>
      </label>
      <span v-if="lastUpdated" class="text-neutral-500">업데이트 {{ new Date(lastUpdated).toLocaleTimeString() }}</span>
      <span v-if="error" class="text-brand-danger">에러: {{ error }}</span>
    </div>

    <div class="grid md:grid-cols-3 gap-4">
      <div class="md:col-span-2 space-y-4">
        <div class="p-3 rounded border border-neutral-700 bg-neutral-900/60">
          <div class="flex justify-between text-xs text-neutral-400 mb-2">
            <span>Ingestion 상태</span>
            <span v-if="stale" class="px-1 rounded bg-brand-danger/20 text-brand-danger">STALE</span>
          </div>
          <dl class="text-[11px] grid grid-cols-2 gap-y-1">
            <div class="flex justify-between"><dt class="text-neutral-500">Enabled</dt><dd :class="status.enabled?'text-brand-accent':'text-neutral-500'">{{ status.enabled? 'yes':'no' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Running</dt><dd :class="status.running?'text-brand-accent':'text-neutral-500'">{{ status.running? 'yes':'no' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Total Msg</dt><dd>{{ status.total_messages ?? '-' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Reconnect</dt><dd>{{ status.reconnect_attempts ?? '-' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Buffer</dt><dd>{{ status.buffer_size ?? 0 }}/{{ status.batch_size ?? '-' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Lag(s)</dt><dd>{{ lag }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Last Msg</dt><dd>{{ status.last_message_ts ? new Date(status.last_message_ts*1000).toLocaleTimeString() : '-' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Last Flush</dt><dd>{{ flushAgeLabel }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Batch Size</dt><dd>{{ status.batch_size ?? '-' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Flush Interval</dt><dd>{{ status.flush_interval ?? '-' }}</dd></div>
          </dl>
          <div class="mt-3">
            <div class="text-[10px] text-neutral-400 mb-1 flex items-center justify-between">
              <span>Lag Spark</span>
              <span v-if="stale" class="text-brand-danger">확인 필요</span>
            </div>
            <svg v-if="sparkPath" :width="100" :height="24">
              <path :d="sparkPath" fill="none" stroke="#10b981" stroke-width="2" />
            </svg>
            <div v-else class="h-6"></div>
          </div>
        </div>
      </div>
      <div class="space-y-4">
        <div class="p-3 rounded border border-neutral-700 bg-neutral-900/60">
          <div class="text-xs font-semibold text-neutral-300 mb-2">Controls</div>
          <button class="btn w-full mb-2" @click="refresh" :disabled="loading">수동 새로고침</button>
          <button class="btn w-full mb-2" @click="toggleAuto" :disabled="loading">Auto {{ auto? 'Off':'On' }}</button>
          <div class="grid grid-cols-2 gap-2 text-[11px]">
            <div>
              <div class="text-neutral-400 mb-0.5">Batch Size</div>
              <input class="input w-full" type="number" min="1" max="500" v-model.number="batchSizeInput" />
            </div>
            <div>
              <div class="text-neutral-400 mb-0.5">Flush Interval(s)</div>
              <input class="input w-full" type="number" step="0.1" min="0.05" v-model.number="flushIntervalInput" />
            </div>
          </div>
          <div class="flex gap-2 mt-2">
            <button class="btn flex-1" @click="applyBatch" :disabled="loading">적용(Batch)</button>
            <button class="btn flex-1" @click="applyFlush" :disabled="loading">적용(Flush)</button>
          </div>
          <div class="flex gap-2 mt-2">
            <button class="btn !bg-amber-700 hover:!bg-amber-600 flex-1" @click="forceFlush" :disabled="loading">Force Flush</button>
            <button class="btn !bg-indigo-700 hover:!bg-indigo-600 flex-1" @click="reconnect" :disabled="loading">Reconnect</button>
          </div>
        </div>
        <div class="p-3 rounded border border-neutral-700 bg-neutral-900/60">
          <div class="text-xs font-semibold text-neutral-300 mb-2">설명</div>
          <ul class="text-[11px] text-neutral-400 space-y-1 list-disc ml-4">
            <li>Lag(s) = 현재시간 - 마지막 수신 메시지 시간</li>
            <li>STALE: Lag > 60s</li>
            <li>Buffer = 배치 DB flush 대기 메시지 수</li>
            <li>Batch/Flush는 런타임에 조정됩니다. Flush interval 변경은 다음 사이클부터 반영됩니다.</li>
          </ul>
        </div>
      </div>
    </div>
    <details class="mt-2">
      <summary class="cursor-pointer text-xs text-neutral-400">Raw status debug</summary>
      <pre class="text-[10px] bg-neutral-900/70 border border-neutral-700 rounded p-2 overflow-auto">{{ JSON.stringify(status, null, 2) }}</pre>
    </details>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onActivated, computed, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useIngestionStore } from '../stores/ingestion';
import http from '../lib/http';

const store = useIngestionStore();
const { status, lag, stale, sparkPath, bufferBarClass, flushAgeLabel, loading, error, lastUpdated, auto, intervalSec } = storeToRefs(store);

// Controls state
const batchSizeInput = ref<number | null>(null);
const flushIntervalInput = ref<number | null>(null);
watch(status, (s) => {
  if (s) {
    if (typeof s.batch_size === 'number') batchSizeInput.value = s.batch_size;
    if (typeof s.flush_interval === 'number') flushIntervalInput.value = s.flush_interval as any;
  }
}, { immediate: true, deep: true });

function refresh() { store.fetchStatus(); }
function toggleAuto() { store.toggleAuto(); }
function changeInterval() { store.setIntervalSec((intervalSec as any).value ?? intervalSec.value); }

async function applyBatch(){
  if (!batchSizeInput.value || batchSizeInput.value < 1) return;
  try { await http.post('/api/admin/ohlcv/set_batch_size', { size: batchSizeInput.value }); } catch {}
  refresh();
}
async function applyFlush(){
  if (!flushIntervalInput.value || flushIntervalInput.value <= 0) return;
  try { await http.post('/api/admin/ohlcv/set_flush_interval', { interval: flushIntervalInput.value }); } catch {}
  refresh();
}
async function forceFlush(){
  try { await http.post('/api/admin/ohlcv/force_flush'); } catch {}
  refresh();
}
async function reconnect(){
  try { await http.post('/api/admin/ohlcv/reconnect'); } catch {}
  setTimeout(refresh, 1000);
}

onMounted(() => {
  store.fetchStatus();
  store.startPolling();
});
onActivated(() => { store.fetchStatus(); });
</script>
