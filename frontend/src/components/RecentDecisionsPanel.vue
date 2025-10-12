<template>
  <div class="bg-neutral-900/60 border border-neutral-800 rounded-lg p-4 space-y-3" title="최신 추론 결정 로그">
    <div class="flex items-center justify-between">
      <h3 class="font-semibold text-sm tracking-wide flex items-center gap-2">
        최근 결정 기록
        <span class="text-[10px] text-neutral-400 font-normal" title="매수=1, 매도=-1, 유지=0 / 확률·임계값은 모델 출력 기반">도움말</span>
      </h3>
      <div class="flex items-center gap-2 text-[11px]">
          <label class="flex items-center gap-1">
            <span>제한</span>
            <input type="number" v-model.number="limitLocal" min="1" max="500" class="w-16 bg-neutral-800 border border-neutral-700 rounded px-1 py-0.5" />
          </label>
          <label class="flex items-center gap-1" title="10초마다 자동 새로고침">
            <input type="checkbox" v-model="auto" /> <span>자동 새로고침</span>
          </label>
        <button class="btn !py-1 !px-2" @click="refresh" :disabled="loading">{{ loading ? '로딩중' : '새로고침' }}</button>
      </div>
    </div>
    <div v-if="error" class="text-xs text-brand-danger">Error: {{ error }}</div>
    <div class="overflow-x-auto -mx-2 px-2">
      <table class="w-full text-xs table-fixed">
        <thead class="text-neutral-500">
          <tr class="border-b border-neutral-800">
            <th class="py-1 font-medium text-left w-[120px]" title="결정이 이루어진 시간">시간</th>
            <th class="py-1 font-medium text-left w-[72px]" title="모델에 의해 내려진 결정">결정</th>
            <th class="py-1 font-medium text-left w-[90px]" title="결정의 확률 (0~1)">확률</th>
            <th class="py-1 font-medium text-left w-[90px]" title="결정을 내리기 위한 임계값 (0~1)">임계값</th>
            <th class="py-1 font-medium text-left w-[200px]" title="사용된 모델의 이름">모델</th>
            <th class="py-1 font-medium text-left w-[96px]" title="프로덕션 여부">프로덕션</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="it in displayItems" :key="it.uid" class="border-b border-neutral-800/40 hover:bg-neutral-800/40">
            <td class="py-1 whitespace-nowrap">
              <span :title="fmtAbs(it.created_at)">{{ fmtRel(it.created_at) }}</span>
            </td>
            <td class="py-1 whitespace-nowrap">
              <span :class="decisionClass(it.decision)">{{ decisionLabel(it.decision) }}</span>
            </td>
            <td class="py-1 whitespace-nowrap">{{ fmtProb(it.probability) }}</td>
            <td class="py-1 whitespace-nowrap">{{ fmtThr(it.threshold) }}</td>
            <td class="py-1 whitespace-nowrap">
              <span class="text-neutral-300">{{ it.model_name || '-' }}</span>
              <span class="text-neutral-500 ml-1" v-if="it.model_version">v{{ it.model_version }}</span>
            </td>
            <td class="py-1 whitespace-nowrap">
              <span v-if="it.production" class="px-1.5 py-0.5 rounded bg-emerald-600/30 text-emerald-300">프로덕션</span>
              <span v-else class="px-1.5 py-0.5 rounded bg-neutral-700 text-neutral-300">-</span>
            </td>
          </tr>
      <tr v-show="displayItems.length === 0">
        <td colspan="6" class="text-center py-6 text-neutral-500">결정 없음</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="text-[10px] text-neutral-500 flex justify-between">
      <div>가져온 시간: {{ lastFetched ? new Date(lastFetched).toLocaleTimeString() : '-' }}</div>
      <div>총계: {{ items.length }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue';
import { useRecentDecisionsStore } from '../stores/recentDecisions';

const store = useRecentDecisionsStore();
const loading = computed(()=>store.loading);
const error = computed(()=>store.error);
const items = computed(()=>store.normalized);
const lastFetched = computed(()=>store.lastFetched);

// local controlled inputs
const limitLocal = ref(store.filters.limit);
const auto = ref(true);

watch(limitLocal, (v)=> { store.setLimit(v); debounceFetch(); });

let debounceTimer: any = null;
function debounceFetch() {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(()=> refresh(), 300);
}

function refresh() { store.fetchRecent(); }

let pollTimer: any = null;
function startPolling(){
  stopPolling();
  pollTimer = setInterval(()=> { if (auto.value && !loading.value) refresh(); }, 10000);
}
function stopPolling(){ if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }
watch(auto, (v)=> { if (v) refresh(); });
onMounted(()=> { refresh(); startPolling(); });
onBeforeUnmount(()=> stopPolling());

const displayItems = computed(()=> items.value);

function decisionLabel(d: any) {
  if (d === 1) return '매수';
  if (d === -1) return '매도';
  if (d === 0) return '유지';
  return String(d ?? '-');
}
function decisionClass(d: any) {
  if (d === 1) return 'text-brand-accent font-semibold';
  if (d === -1) return 'text-brand-danger font-semibold';
  return 'text-neutral-400';
}
function fmtProb(p: any) {
  if (p == null || isNaN(p)) return '-';
  return (p*100).toFixed(2)+'%';
}
function fmtThr(t: any) {
  if (t == null || isNaN(t)) return '-';
  return (t*100).toFixed(1)+'%';
}
function fmtRel(ts: number | null) {
  if (!ts) return '-';
  const diff = Date.now()/1000 - ts;
  if (diff < 60) return diff.toFixed(0)+'s ago';
  if (diff < 3600) return Math.floor(diff/60)+'m ago';
  return Math.floor(diff/3600)+'h ago';
}
function fmtAbs(ts: number | null) {
  if (!ts) return '-';
  return new Date(ts*1000).toLocaleString();
}

// initial load is handled in onMounted
</script>

<script lang="ts">
export default {
  name: 'RecentDecisionsPanel'
};
</script>

<style scoped>
.table-fixed th { font-weight:500; }
</style>
