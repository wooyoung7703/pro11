<template>
  <div class="space-y-4">
    <!-- Global Confirm Dialog -->
    <ConfirmDialog
      :open="confirm.open"
      :title="confirm.title"
      :message="confirm.message"
      :requireText="confirm.requireText"
      :delayMs="confirm.delayMs"
      @confirm="confirm.onConfirm && confirm.onConfirm()"
      @cancel="confirm.open=false"
    />
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <!-- Backfill Card -->
      <section class="card p-4 space-y-2">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-semibold">Backfill</h2>
          <div class="flex items-center gap-2 text-[11px]">
            <label class="flex items-center gap-1 cursor-pointer select-none"><input type="checkbox" v-model="bf.live" /> live</label>
            <button class="btn btn-xs" @click="refreshBackfill">Refresh</button>
            <button class="btn btn-xs" title="Admin > Actions로 이동" @click="goAdminActions">Open in Admin</button>
            <div class="h-4 w-px bg-neutral-800 mx-1"></div>
            <select v-model="bf.symbol" class="bg-neutral-900 border border-neutral-700 rounded px-1 py-0.5">
              <option value="">SYMBOL</option>
              <option value="BTCUSDT">BTCUSDT</option>
              <option value="ETHUSDT">ETHUSDT</option>
              <option value="XRPUSDT">XRPUSDT</option>
            </select>
            <select v-model="bf.interval" class="bg-neutral-900 border border-neutral-700 rounded px-1 py-0.5">
              <option value="">INTERVAL</option>
              <option value="1m">1m</option>
              <option value="5m">5m</option>
              <option value="1h">1h</option>
            </select>
            <select v-model="bf.status" class="bg-neutral-900 border border-neutral-700 rounded px-1 py-0.5">
              <option value="">ALL</option>
              <option value="running">running</option>
              <option value="success">success</option>
              <option value="error">error</option>
            </select>
            <select v-model.number="bf.limit" class="bg-neutral-900 border border-neutral-700 rounded px-1 py-0.5" title="최대 표시 개수">
              <option :value="10">10</option>
              <option :value="25">25</option>
              <option :value="50">50</option>
              <option :value="100">100</option>
              <option :value="200">200</option>
            </select>
            <button class="btn btn-xs" @click="startYearBackfill" :disabled="startBusy">Year Backfill</button>
            <button class="btn btn-xs" @click="cancelYearBackfill" :disabled="cancelBusy">Cancel</button>
          </div>
        </div>
        <div class="text-xs text-neutral-400">
          <span class="mr-2">총 {{ bf.total || bf.items.length }}</span>
          <span class="mr-2">running {{ bf.counts.running }}</span>
          <span class="mr-2">success {{ bf.counts.success }}</span>
          <span>error {{ bf.counts.error }}</span>
          <span class="ml-2 text-[10px]" :class="freshnessClass">{{ freshnessText }}</span>
        </div>
        <div class="overflow-auto max-h-48">
          <table class="w-full text-[11px]">
            <thead>
              <tr class="text-neutral-500 border-b border-neutral-800">
                <th class="text-left py-1 px-1">ID</th>
                <th class="text-left py-1 px-1">Status</th>
                <th class="text-right py-1 px-1">Prog</th>
                <th class="text-left py-1 px-1">ETA</th>
                <th class="text-left py-1 px-1">Started</th>
                <th class="text-left py-1 px-1">Actions</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in bf.items.slice(0,5)" :key="r.id" class="border-b border-neutral-800/40">
                <td class="py-1 px-1 font-mono">{{ r.id }}</td>
                <td class="py-1 px-1"><span :class="statusClass(r.status)" class="px-2 py-0.5 rounded" :title="r.error || ''">{{ r.status }}</span></td>
                <td class="py-1 px-1 text-right">
                  <div class="flex items-center gap-2">
                    <div class="w-20 h-2 bg-neutral-800 rounded overflow-hidden border border-neutral-700">
                      <div v-if="progressPct(r) != null" class="h-full bg-emerald-600" :style="{ width: progressPct(r) + '%' }"></div>
                    </div>
                    <span class="min-w-[28px] text-right">
                      <span v-if="progressPct(r) != null">{{ progressPct(r) }}%</span>
                      <span v-else class="text-neutral-600">—</span>
                    </span>
                  </div>
                </td>
                <td class="py-1 px-1">{{ etaText(r) }}</td>
                <td class="py-1 px-1" :title="r.started_at">{{ shortTime(r.started_at) }}</td>
                <td class="py-1 px-1">
                  <button class="btn btn-xs" title="Admin Backfill 필터로 보기" @click="openAdminFiltered(r)">Open</button>
                </td>
              </tr>
              <tr v-if="bf.items.length===0"><td colspan="3" class="text-center text-neutral-600 py-2">No runs</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Training Card -->
      <section class="card p-4 space-y-2">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-semibold">Training</h2>
          <div class="flex items-center gap-2 text-[11px]">
            <label class="flex items-center gap-1 cursor-pointer select-none"><input type="checkbox" v-model="trainingAuto" /> auto</label>
            <button class="btn btn-xs" :disabled="trainingLoading" @click="fetchJobs">Refresh</button>
            <button class="btn btn-xs" title="Admin > Training으로 이동" @click="goTraining">Open in Training</button>
          </div>
        </div>
        <div class="text-xs text-neutral-400">
          <span class="mr-2">전체 {{ trainingTotal }}</span>
          <span class="mr-2 text-brand-accent">성공 {{ trainingSuccess }}</span>
          <span class="text-brand-danger">실패 {{ trainingError }}</span>
        </div>
        <div class="overflow-auto max-h-48">
          <table class="w-full text-[11px]">
            <thead><tr class="text-neutral-500 border-b border-neutral-800"><th class="text-left py-1 px-1">ID</th><th class="text-left py-1 px-1">Status</th><th class="text-left py-1 px-1">AUC</th></tr></thead>
            <tbody>
              <tr v-for="j in trainingList.slice(0,5)" :key="j.id" class="border-b border-neutral-800/40">
                <td class="py-1 px-1 font-mono">{{ j.id }}</td>
                <td class="py-1 px-1">{{ j.status }}</td>
                <td class="py-1 px-1">{{ j.metrics?.auc?.toFixed?.(3) ?? '—' }}</td>
              </tr>
              <tr v-if="trainingList.length===0"><td colspan="3" class="text-center text-neutral-600 py-2">No jobs</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Labeler / Promotion Card -->
      <section class="card p-4 space-y-3">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-semibold">Labeler & Promotion</h2>
          <div class="flex items-center gap-2 text-[11px]">
            <button class="btn btn-xs" @click="runLabeler">Run labeler</button>
            <button class="btn btn-xs" @click="refreshPromotion">Promotion status</button>
            <button class="btn btn-xs" title="Admin > Promotion으로 이동" @click="goPromotion">Open in Promotion</button>
          </div>
        </div>
        <div class="text-xs text-neutral-300 space-y-1">
          <div>Promotion: <span :class="promotion?.in_cooldown ? 'text-amber-400' : 'text-emerald-400'">{{ promotion?.in_cooldown ? 'cooldown' : 'idle' }}</span></div>
          <div class="text-neutral-500 text-[11px]">{{ promotionInfo }}</div>
          <div class="text-neutral-500 text-[10px]">자세한 작업/확인은 Admin > Actions에서 수행하세요.</div>
        </div>
      </section>
    </div>

    <!-- Live Event Feed -->
    <section class="card p-4">
      <h2 class="text-sm font-semibold mb-2">Live Events</h2>
      <div class="text-[11px] font-mono space-y-1 max-h-48 overflow-auto">
        <div v-for="(e,idx) in events" :key="idx" class="text-neutral-300">{{ e }}</div>
        <div v-if="events.length===0" class="text-neutral-500">No events yet.</div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed, watch } from 'vue';
import { useRouter } from 'vue-router';
import { connectSSE } from '../lib/sse';
import http from '../lib/http';
import { useTrainingJobsStore } from '../stores/trainingJobs';
import ConfirmDialog from '../components/ConfirmDialog.vue';
import { confirmPresets } from '../lib/confirmPresets';

const router = useRouter();
function goAdminActions() { router.push({ path: '/admin', query: { tab: 'actions' } }); }
function goTraining() { router.push({ path: '/admin', query: { tab: 'training' } }); }
function goPromotion() { router.push({ path: '/admin', query: { tab: 'promotion_audit' } }); }

// ---------------- Backfill (SSE) ----------------
interface RunRow { id: number; status: string; started_at: string; requested_target?: number; inserted?: number; error?: string|null; symbol?: string; interval?: string }
const bf = ref<{ live: boolean; items: RunRow[]; total: number; lastUpdate: number | null; counts: {running:number;success:number;error:number}; _sse: any|null; symbol: string; interval: string; status: string; limit: number }>({
  live: true,
  items: [],
  total: 0,
  lastUpdate: null,
  counts: { running: 0, success: 0, error: 0 },
  _sse: null,
  symbol: '',
  interval: '',
  status: '',
  limit: 50,
});
// Track previous snapshot to estimate speed
const prevById = ref<Record<number, { inserted: number; ts: number }>>({});
function statusClass(s: string) {
  if (s === 'success') return 'bg-emerald-700/30 text-emerald-300 border border-emerald-700/40';
  if (s === 'running') return 'bg-indigo-700/30 text-indigo-300 border border-indigo-700/40';
  if (s === 'error') return 'bg-rose-700/30 text-rose-300 border border-rose-700/40';
  return 'bg-neutral-700/30 text-neutral-300 border border-neutral-700/40';
}
function refreshBackfill() { fetchBackfillOnce(); }
async function fetchBackfillOnce() {
  try {
  const r = await http.get('/api/features/backfill/runs', { params: { page: 1, page_size: Math.min(Math.max(bf.value.limit || 50, 1), 200), sort_by: 'started_at', order: 'desc', status: bf.value.status || undefined, symbol: bf.value.symbol || undefined, interval: bf.value.interval || undefined } });
    const items: any[] = Array.isArray(r.data) ? r.data : (Array.isArray(r.data?.items) ? r.data.items : []);
  bf.value.items = items.map((x) => ({ id: x.id, status: x.status, started_at: x.started_at, requested_target: x.requested_target, inserted: x.inserted, error: x.error, symbol: x.symbol, interval: x.interval }));
    bf.value.total = typeof r.data?.total === 'number' ? r.data.total : bf.value.items.length;
    bf.value.counts = countStatuses(bf.value.items.map((x:any) => x.status));
    bf.value.lastUpdate = Date.now();
    pushEvent(`[runs] fetched ${bf.value.items.length}`);
    updatePrev(bf.value.items);
  } catch { /* no-op */ }
}
function startRunsSSE() {
  stopRunsSSE();
  const params = new URLSearchParams();
  if (bf.value.symbol) params.set('symbol', bf.value.symbol);
  if (bf.value.interval) params.set('interval', bf.value.interval);
  if (bf.value.status) params.set('status', bf.value.status);
  params.set('limit', String(Math.min(Math.max(bf.value.limit || 50, 1), 200)));
  bf.value._sse = connectSSE({ url: '/stream/runs' + (params.toString()?('?' + params.toString()):''), heartbeatTimeoutMs: 20000 }, {
    onMessage: (evt) => {
      const d = evt?.data as any;
      if (d && Array.isArray(d.items)) {
        const items = d.items as any[];
  bf.value.items = items.map((x) => ({ id: x.id, status: x.status, started_at: x.started_at, requested_target: x.requested_target, inserted: x.inserted, error: x.error, symbol: x.symbol, interval: x.interval }));
        bf.value.total = Math.max(bf.value.total, bf.value.items.length);
        bf.value.counts = countStatuses(items.map((x:any) => x.status));
        bf.value.lastUpdate = Date.now();
        pushEvent(`[runs] ${items.length} snapshot`);
        updatePrev(bf.value.items);
      }
    }
  });
}
function stopRunsSSE() { try { bf.value._sse && bf.value._sse.close(); } catch {} bf.value._sse = null; }
watch(() => bf.value.live, (v) => { if (v) startRunsSSE(); else stopRunsSSE(); });
watch(() => [bf.value.symbol, bf.value.interval, bf.value.status, bf.value.limit], () => { if (bf.value.live) startRunsSSE(); }, { deep: true });

// Persist filters/settings to localStorage
const LS_KEY = 'jobcenter.backfill';
watch(bf, (v) => {
  try {
    const data = { live: v.live, symbol: v.symbol, interval: v.interval, status: v.status, limit: v.limit };
    localStorage.setItem(LS_KEY, JSON.stringify(data));
  } catch { /* ignore */ }
}, { deep: true });

// Freshness indicator based on lastUpdate age
// Read from global overrides if provided (e.g., window.VITE_RUNS_FRESH_MS)
const FRESH_MS = Number((window as any)?.VITE_RUNS_FRESH_MS ?? 10000);
const STALE_MS = Number((window as any)?.VITE_RUNS_STALE_MS ?? 30000);
const freshnessText = computed(() => {
  if (!bf.value.lastUpdate) return '업데이트 대기중';
  const age = Date.now() - (bf.value.lastUpdate as number);
  const sec = Math.floor(age / 1000);
  if (age <= FRESH_MS) return `실시간 · ${sec}s 전 업데이트`;
  if (age <= STALE_MS) return `보통 · ${sec}s 전 업데이트`;
  return `느림 · ${sec}s 전 업데이트`;
});
const freshnessClass = computed(() => {
  if (!bf.value.lastUpdate) return 'text-neutral-500';
  const age = Date.now() - (bf.value.lastUpdate as number);
  if (age <= FRESH_MS) return 'text-emerald-400';
  if (age <= STALE_MS) return 'text-amber-400';
  return 'text-rose-400';
});

function countStatuses(arr: string[]) {
  const c = { running: 0, success: 0, error: 0 } as any;
  for (const s of arr) { if (c[s] !== undefined) c[s]++; }
  return c as { running:number; success:number; error:number };
}

// ---------------- Training (store) ----------------
const tstore = useTrainingJobsStore();
const trainingLoading = computed(() => (tstore as any).loading);
const trainingList = computed(() => (tstore as any).jobs as any[]);
const trainingTotal = computed(() => (tstore as any).jobs.length);
const trainingSuccess = computed(() => (tstore as any).jobs.filter((j:any)=>j.status==='success').length);
const trainingError = computed(() => (tstore as any).jobs.filter((j:any)=>j.status==='error').length);
const trainingAuto = computed({ get: () => (tstore as any).auto, set: (v: boolean) => (tstore as any).toggleAuto(v) });
const fetchJobs = (tstore as any).fetchJobs as () => Promise<void>;

// ---------------- Labeler & Promotion ----------------
const promotion = ref<any | null>(null);
const promotionInfo = computed(() => {
  if (!promotion.value) return '';
  if (promotion.value.in_cooldown) {
    const next = promotion.value.next_allowed_ts ? new Date(promotion.value.next_allowed_ts*1000).toLocaleString() : 'N/A';
    return `쿨다운 진행중. 다음 허용: ${next}`;
  }
  return '대기 상태';
});
async function refreshPromotion() {
  try { const r = await fetch('/api/models/promotion/alert/status'); if (r.ok) promotion.value = await r.json(); } catch { /* ignore */ }
}
async function runLabeler() {
  try {
    const preset = confirmPresets.labelerRun(120, 1000);
    openConfirm({
      ...preset,
      onConfirm: async () => {
        const params = new URLSearchParams({ force: 'true', min_age_seconds: '120', limit: '1000' });
        await http.post('/api/inference/labeler/run?' + params.toString());
        pushEvent('[labeler] run requested');
      }
    });
  } catch { /* ignore */ }
}

// ---------------- Live events feed ----------------
const events = ref<string[]>([]);
function pushEvent(s: string) {
  const ts = new Date().toLocaleTimeString();
  events.value.unshift(`${ts} ${s}`);
  if (events.value.length > 50) events.value.pop();
}

function shortTime(ts: string | number | null) {
  if (!ts) return '-';
  try {
    const d = typeof ts === 'number' ? new Date(ts) : (/^\d/.test(String(ts)) && !String(ts).includes('T') ? new Date(parseFloat(String(ts)) * 1000) : new Date(ts));
    const pad = (n: number) => (n < 10 ? '0' + n : '' + n);
    const yyyy = d.getFullYear(); const mm = pad(d.getMonth()+1); const dd = pad(d.getDate());
    const HH = pad(d.getHours()); const MM = pad(d.getMinutes()); const SS = pad(d.getSeconds());
    return `${yyyy}-${mm}-${dd} ${HH}:${MM}:${SS}`;
  } catch { return String(ts); }
}

function progressPct(r: RunRow): number | null {
  const a = r.inserted; const b = r.requested_target;
  if (typeof a === 'number' && typeof b === 'number' && b > 0) {
    const pct = Math.max(0, Math.min(100, Math.floor((a / b) * 100)));
    return pct;
  }
  return null;
}

function updatePrev(rows: RunRow[]) {
  const now = Date.now();
  const map = prevById.value;
  for (const r of rows) {
    if (typeof r.inserted === 'number') {
      map[r.id] = { inserted: r.inserted, ts: now };
    }
  }
  prevById.value = { ...map };
}

function etaText(r: RunRow): string {
  const pct = progressPct(r);
  if (pct === null || pct >= 100) return '—';
  const target = r.requested_target as number | undefined;
  const inserted = r.inserted as number | undefined;
  if (!(typeof target === 'number' && typeof inserted === 'number' && target > inserted)) return '—';
  const prev = prevById.value[r.id];
  if (!prev) return '—';
  const dt = (Date.now() - prev.ts) / 1000;
  const di = inserted - prev.inserted;
  if (dt <= 0 || di <= 0) return '—';
  const rate = di / dt; // rows per second
  if (rate <= 0) return '—';
  const remaining = target - inserted;
  const sec = Math.max(1, Math.floor(remaining / rate));
  return formatSeconds(sec);
}

function formatSeconds(s: number): string {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return `${m}m ${rem}s`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h}h ${mm}m`;
}

function openAdminFiltered(r: RunRow) {
  const q: any = { tab: 'actions' };
  if (r.symbol) q.bf_symbol = r.symbol;
  if (r.interval) q.bf_interval = r.interval;
  if (r.status) q.bf_status = r.status;
  q.bf_live = '1';
  router.push({ path: '/admin', query: q });
}

// ---------------- Backfill actions (Year) ----------------
const startBusy = ref(false);
const cancelBusy = ref(false);
async function startYearBackfill() {
  if (startBusy.value) return;
  startBusy.value = true;
  try {
    const params = new URLSearchParams();
    if (bf.value.symbol) params.set('symbol', bf.value.symbol);
    if (bf.value.interval) params.set('interval', bf.value.interval);
    const r = await http.post('/api/ohlcv/backfill/year/start' + (params.toString()?('?' + params.toString()):''));
    if (r?.data) pushEvent(`[year_backfill] start: ${r.data.status}`);
    await fetchBackfillOnce();
  } catch (e:any) {
    pushEvent(`[year_backfill] start failed: ${e?.message||'error'}`);
  } finally { startBusy.value = false; }
}
async function cancelYearBackfill() {
  if (cancelBusy.value) return;
  openConfirm({
    ...confirmPresets.yearBackfillCancel(),
    onConfirm: async () => {
      cancelBusy.value = true;
      try {
        const params = new URLSearchParams();
        if (bf.value.symbol) params.set('symbol', bf.value.symbol);
        if (bf.value.interval) params.set('interval', bf.value.interval);
        const r = await http.post('/api/ohlcv/backfill/year/cancel' + (params.toString()?('?' + params.toString()):''));
        if (r?.data) pushEvent(`[year_backfill] cancel: ${r.data.status}`);
        await fetchBackfillOnce();
      } catch (e:any) {
        pushEvent(`[year_backfill] cancel failed: ${e?.message||'error'}`);
      } finally { cancelBusy.value = false; }
    }
  });
}

onMounted(() => {
  // Backfill: initial
  // Restore persisted settings
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) {
      const s = JSON.parse(raw);
      if (typeof s.live === 'boolean') bf.value.live = s.live;
      if (typeof s.symbol === 'string') bf.value.symbol = s.symbol;
      if (typeof s.interval === 'string') bf.value.interval = s.interval;
      if (typeof s.status === 'string') bf.value.status = s.status;
      if (typeof s.limit === 'number') bf.value.limit = Math.min(Math.max(s.limit, 1), 200);
    }
  } catch { /* ignore */ }
  fetchBackfillOnce();
  if (bf.value.live) startRunsSSE();
  // Training: warmup + auto
  fetchJobs();
  (tstore as any).start();
  // Promotion: warmup + interval
  refreshPromotion();
  const p = setInterval(refreshPromotion, 30000);
  (window as any).__jobCenter_promoTimer = p;
});
onBeforeUnmount(() => {
  stopRunsSSE();
  try { clearInterval((window as any).__jobCenter_promoTimer); } catch {}
});

// ---------- Confirm dialog helper ----------
type ConfirmFn = () => void | Promise<void>;
const confirm = ref<{ open: boolean; title: string; message: string; requireText?: string; delayMs?: number; onConfirm?: ConfirmFn }>({ open: false, title: '', message: '' });
function openConfirm(opts: { title: string; message: string; requireText?: string; delayMs?: number; onConfirm: ConfirmFn }) {
  confirm.value.title = opts.title;
  confirm.value.message = opts.message;
  confirm.value.requireText = opts.requireText;
  confirm.value.delayMs = opts.delayMs;
  confirm.value.onConfirm = async () => {
    try { await opts.onConfirm(); } finally { confirm.value.open = false; }
  };
  confirm.value.open = true;
}
</script>

<style scoped>
table { border-collapse: collapse; }
</style>
