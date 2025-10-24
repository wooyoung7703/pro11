<template>
  <div class="timeline bg-brand-700/80 border border-slate-700 rounded-xl text-slate-200 w-full">
    <header>
      <h3>DB 시그널 타임라인</h3>
      <div class="controls">
        <label class="ctrl">
          <input type="checkbox" v-model="autoRefresh" /> 자동 새로고침
        </label>
        <button class="btn" @click="refresh" :disabled="loading">{{ loading ? '새로고침…' : '새로고침' }}</button>
      </div>
    </header>
    <div class="filters">
      <input class="inp" placeholder="signal_type (서버 필터)" v-model="filterSignalType" />
      <input class="inp" placeholder="symbol (클라 필터)" v-model="filterSymbol" />
      <select class="inp" v-model="filterEvent">
        <option value="">모든 이벤트</option>
        <option value="created">created</option>
        <option value="executed">executed</option>
        <option value="signal">signal</option>
        <option value="signal_clear">signal_clear</option>
      </select>
      <span class="tsnote" v-if="lastRefreshed">업데이트: {{ timeAgo(lastRefreshed) }}</span>
    </div>
    <ul ref="listRef" @scroll="onScroll" class="overflow-x-auto">
      <li v-for="row in filteredItems" :key="row.id ?? rowKey(row)" class="item bg-brand-800/70 border border-slate-700">
        <div class="meta">
          <span class="type">{{ row.event }}</span>
          <span class="src">{{ row.source }}</span>
          <span class="ts">{{ format(row.ts) }}</span>
        </div>
        <div class="sub">
          <span v-if="row.signal_type" class="tag">{{ row.signal_type }}</span>
          <span v-if="row.symbol" class="tag">{{ row.symbol }}</span>
        </div>
        <div class="details-line">
          <button class="btn small" @click="toggleDetails(row)">{{ isOpen(row) ? '접기' : '자세히' }}</button>
        </div>
        <pre v-if="isOpen(row)" class="payload">{{ pretty(row.details) }}</pre>
      </li>
    </ul>
    <div v-if="!filteredItems.length && !loading" class="empty">최근 저장된 시그널 타임라인이 없습니다.</div>
    <div class="more" v-if="hasMore && !loading">
      <button class="btn" @click="loadMore" :disabled="moreLoading">{{ moreLoading ? '불러오는 중…' : '더 보기' }}</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed, watch, nextTick } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import http from '@/lib/http';

interface TimelineRow {
  ts: number;
  source: string;
  event: string;
  signal_type?: string | null;
  symbol?: string | null;
  details?: Record<string, unknown> | null;
  id?: number;
}

const limit = 100;
const loading = ref(false);
const rows = ref<TimelineRow[]>([]);
const openKeys = ref<Set<string>>(new Set());
const moreLoading = ref(false);
const hasMore = ref(true);
const lastRefreshed = ref<number | null>(null);
const autoRefresh = ref(true);
let timer: any = null;
const listRef = ref<HTMLElement | null>(null);

// Filters
const filterSignalType = ref('');
const filterSymbol = ref('');
const filterEvent = ref('');

const route = useRoute();
const router = useRouter();

// Persist filters in localStorage + URL query (best-effort)
const LSK = 'dbTimelineFilters';
function loadFilters() {
  try {
    // 1) URL query has priority when present
    const q = route.query || {} as any;
    const qsType = typeof q.signal_type === 'string' ? q.signal_type : undefined;
    const qsSymbol = typeof q.symbol === 'string' ? q.symbol : undefined;
    const qsEvent = typeof q.event === 'string' ? q.event : undefined;
    const qsAuto = typeof q.auto === 'string' ? q.auto : undefined; // '1' or '0'
    if (qsType !== undefined) filterSignalType.value = qsType;
    if (qsSymbol !== undefined) filterSymbol.value = qsSymbol;
    if (qsEvent !== undefined) filterEvent.value = qsEvent;
    if (qsAuto !== undefined) autoRefresh.value = qsAuto === '1';

    // 2) Then localStorage as fallback for fields not set by query
    const raw = localStorage.getItem(LSK);
    if (!raw) return;
    const obj = JSON.parse(raw);
    if (qsType === undefined && typeof obj.signal_type === 'string') filterSignalType.value = obj.signal_type;
    if (qsSymbol === undefined && typeof obj.symbol === 'string') filterSymbol.value = obj.symbol;
    if (qsEvent === undefined && typeof obj.event === 'string') filterEvent.value = obj.event;
    if (qsAuto === undefined && typeof obj.autoRefresh === 'boolean') autoRefresh.value = obj.autoRefresh;
  } catch {}
}
function saveFilters() {
  try {
    const obj = { signal_type: filterSignalType.value, symbol: filterSymbol.value, event: filterEvent.value, autoRefresh: autoRefresh.value };
    localStorage.setItem(LSK, JSON.stringify(obj));
    // Update query string shallowly (no navigation)
    const q: any = {
      ...route.query,
      signal_type: filterSignalType.value || undefined,
      symbol: filterSymbol.value || undefined,
      event: filterEvent.value || undefined,
      auto: autoRefresh.value ? '1' : '0',
    };
    router.replace({ query: q });
  } catch {}
}
watch([filterSignalType, filterSymbol, filterEvent, autoRefresh], saveFilters, { deep: false });

async function refresh() {
  if (loading.value) return;
  loading.value = true;
  try {
    const params: Record<string, any> = { limit };
    if (filterSignalType.value.trim()) params.signal_type = filterSignalType.value.trim();
    const resp = await http.get('/api/trading/signals/timeline', { params });
    const data = (resp.data?.timeline ?? []) as any[];
    const mapped: TimelineRow[] = Array.isArray(data) ? data.map((r) => ({
      ts: Number(r.ts ?? 0),
      source: String(r.source ?? 'unknown'),
      event: String(r.event ?? 'unknown'),
      signal_type: r.signal_type ?? null,
      symbol: r.symbol ?? null,
      details: (typeof r.details === 'object' && r.details) ? r.details : null,
      ...(typeof r.id !== 'undefined' ? { id: Number(r.id) } : {}),
    })) : [];
    rows.value = mapped.filter((r) => Number.isFinite(r.ts) && r.ts > 0);
    lastRefreshed.value = Date.now() / 1000;
  } catch {
    // swallow for now; UI stays quiet
  } finally {
    loading.value = false;
  }
}

const items = computed(() => rows.value.slice(0, limit));
const filteredItems = computed(() => {
  return items.value.filter((r) => {
    if (filterEvent.value && r.event !== filterEvent.value) return false;
    if (filterSymbol.value && (r.symbol || '').toLowerCase() !== filterSymbol.value.toLowerCase()) return false;
    return true;
  });
});

const formatter = new Intl.DateTimeFormat('ko-KR', {
  hour: '2-digit', minute: '2-digit', second: '2-digit',
});
function format(ts: number) { return formatter.format(new Date(ts * 1000)); }
function pretty(obj?: Record<string, unknown> | null) { try { return JSON.stringify(obj ?? {}, null, 2); } catch { return String(obj ?? ''); } }
function timeAgo(tsSec: number) {
  const s = Math.max(0, Math.floor(Date.now()/1000 - tsSec));
  if (s < 60) return `${s}s 전`;
  const m = Math.floor(s/60);
  if (m < 60) return `${m}m 전`;
  const h = Math.floor(m/60);
  return `${h}h 전`;
}
function rowKey(r: TimelineRow) {
  // Construct a semi-stable key from fields; if DB provides id in details, prefer it
  const id = (r.details && (r.details as any).id) || '';
  return `${r.ts}-${r.source}-${r.event}-${r.signal_type || ''}-${r.symbol || ''}-${id}`;
}
function keyOf(r: TimelineRow) { return String((r as any).id ?? rowKey(r)); }
function toggleDetails(r: TimelineRow) {
  const k = keyOf(r);
  const s = new Set(openKeys.value);
  if (s.has(k)) s.delete(k); else s.add(k);
  openKeys.value = s;
}
function isOpen(r: TimelineRow) { return openKeys.value.has(keyOf(r)); }

onMounted(async () => {
  loadFilters();
  await refresh();
  timer = setInterval(() => {
    if (autoRefresh.value) refresh();
  }, 15000);
});
onBeforeUnmount(() => { if (timer) clearInterval(timer); });
watch([filterSignalType], async () => {
  // server-side filter needs a reload
  await nextTick();
  await refresh();
});

async function loadMore() {
  if (moreLoading.value) return;
  moreLoading.value = true;
  try {
    const current = rows.value;
    const oldestTs = current.length ? Math.min(...current.map((r) => r.ts)) : null;
    const params: Record<string, any> = { limit };
    if (filterSignalType.value.trim()) params.signal_type = filterSignalType.value.trim();
    if (oldestTs) params.to_ts = Math.max(0, oldestTs - 1);
    const resp = await http.get('/api/trading/signals/timeline', { params });
    const data = (resp.data?.timeline ?? []) as any[];
    const mapped: TimelineRow[] = Array.isArray(data) ? data.map((r) => ({
      ts: Number(r.ts ?? 0),
      source: String(r.source ?? 'unknown'),
      event: String(r.event ?? 'unknown'),
      signal_type: r.signal_type ?? null,
      symbol: r.symbol ?? null,
      details: (typeof r.details === 'object' && r.details) ? r.details : null,
      ...(typeof r.id !== 'undefined' ? { id: Number(r.id) } : {}),
    })) : [];
    const page = mapped.filter((r) => Number.isFinite(r.ts) && r.ts > 0);
    if (!page.length || page.length < limit) hasMore.value = false;
    rows.value = current.concat(page);
  } catch {
    // silent
  } finally {
    moreLoading.value = false;
  }
}

function onScroll(e: Event) {
  try {
    const el = e.target as HTMLElement;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (nearBottom && hasMore.value && !loading.value && !moreLoading.value) {
      loadMore();
    }
  } catch {}
}
</script>

<style scoped>
.timeline { border-radius: 10px; padding: 12px; display:flex; flex-direction:column; min-height:260px; max-height:260px; }
header { display:flex; justify-content: space-between; align-items:center; margin-bottom:8px; }
h3 { margin:0; font-size:15px; }
.controls .btn { background:#1b2a40; color:#e5ecf5; border:1px solid rgba(70, 96, 140, 0.35); padding:6px 10px; border-radius:6px; cursor:pointer; }
.controls { display:flex; gap:8px; align-items:center; }
.ctrl { display:flex; gap:6px; align-items:center; font-size:12px; color:#9cb2d6; }
.filters { display:flex; gap:8px; align-items:center; margin-bottom:8px; flex-wrap: wrap; }
.filters .inp { background:#0e1726; color:#d2ddf0; border:1px solid rgba(70, 96, 140, 0.35); padding:6px 8px; border-radius:6px; font-size:12px; }
.filters .tsnote { font-size:12px; color:#9cb2d6; margin-left:auto; }
ul { list-style:none; padding:0; margin:0; flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:6px; }
.item { border-radius: 6px; padding: 8px; }
.meta { display:flex; gap:8px; justify-content:space-between; font-size:12px; color:#9cb2d6; margin-bottom:4px; }
.sub { display:flex; gap:6px; margin-bottom: 4px; }
.tag { font-size: 11px; background:#1d2a3a; color:#9fb6d8; padding:2px 6px; border-radius: 999px; }
.payload { margin:0; white-space: pre-wrap; font-size: 11px; color: #aebed9; font-family: 'JetBrains Mono','Fira Code', monospace; word-break: break-word; overflow-x:auto; }
.empty { text-align:center; color:#6f7f98; padding:16px 0; margin-top:auto; }
.more { display:flex; justify-content:center; padding-top:8px; }
.btn.small { padding:4px 8px; font-size:12px; }
.details-line { display:flex; justify-content:flex-end; margin: 4px 0; }
@media (max-width: 768px) {
  .filters .inp { flex: 1 1 48%; min-width: 140px; }
  .filters .tsnote { flex: 1 1 100%; margin-left: 0; }
}
</style>
