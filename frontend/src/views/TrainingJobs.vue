<template>
  <div class="space-y-6">
    <ConfirmDialog
      :open="confirm.open"
      :title="confirm.title"
      :message="confirm.message"
      :requireText="confirm.requireText"
      :delayMs="confirm.delayMs"
      @confirm="confirm.onConfirm && confirm.onConfirm()"
      @cancel="confirm.open=false"
    />
    <section class="card">
      <div class="flex items-center justify-between mb-4">
        <h1 class="text-xl font-semibold">Training Jobs</h1>
        <div class="flex items-center gap-3 text-xs">
          <!-- Run training controls -->
          <div class="flex items-center gap-2 pr-3 mr-3 border-r border-neutral-700/60">
            <!-- Bottom-only parameters -->
            <template>
              <label class="flex items-center gap-1 text-neutral-300 text-[11px]">
                lookahead
                <input v-model.number="bottomParams.lookahead" type="number" min="10" max="100" 
                       class="bg-neutral-800 border border-neutral-700 rounded px-1 py-0.5 w-12 text-[11px]" />
              </label>
              <label class="flex items-center gap-1 text-neutral-300 text-[11px]">
                drawdown
                <input v-model.number="bottomParams.drawdown" type="number" step="0.0001" min="0.0001" max="0.1" 
                       class="bg-neutral-800 border border-neutral-700 rounded px-1 py-0.5 w-16 text-[11px]" />
              </label>
              <label class="flex items-center gap-1 text-neutral-300 text-[11px]">
                rebound
                <input v-model.number="bottomParams.rebound" type="number" step="0.0001" min="0.0001" max="0.1" 
                       class="bg-neutral-800 border border-neutral-700 rounded px-1 py-0.5 w-16 text-[11px]" />
              </label>
            </template>
            <label class="flex items-center gap-1 cursor-pointer select-none">
              <input type="checkbox" v-model="runSentiment" /> sentiment 포함
            </label>
            <label class="flex items-center gap-1 cursor-pointer select-none">
              <input type="checkbox" v-model="runForce" /> 강제(force)
            </label>
            <button class="btn bg-emerald-700 hover:bg-emerald-600" :disabled="runLoading" @click="confirmRunTraining">
              <span v-if="runLoading">실행중…</span>
              <span v-else>재학습 실행</span>
            </button>
            <span v-if="runMsg" class="px-2 py-0.5 rounded bg-neutral-800 text-neutral-200 border border-neutral-700">{{ runMsg }}</span>
          </div>
          <button class="btn" :disabled="loading" @click="fetchJobs">새로고침</button>
          <!-- Filters -->
          <div class="flex items-center gap-2">
            <label class="flex items-center gap-1 text-neutral-300">
              상태
              <select v-model="statusFilter" class="bg-neutral-800 border border-neutral-700 rounded px-1 py-0.5 text-[12px]">
                <option value="all">전체</option>
                <option value="running">running</option>
                <option value="success">success</option>
                <option value="error">error</option>
              </select>
            </label>
            <label class="flex items-center gap-1 text-neutral-300">
              트리거
              <select v-model="triggerFilter" class="bg-neutral-800 border border-neutral-700 rounded px-1 py-0.5 text-[12px]" title="manual_ui 등은 manual로 분류, drift_auto 등은 auto로 분류">
                <option value="all">전체</option>
                <option value="manual">manual</option>
                <option value="auto">auto</option>
                <option value="other">other</option>
              </select>
            </label>
            
          </div>
          <!-- Summary badges -->
          <div class="flex items-center gap-2 text-xs">
            <span class="px-2 py-0.5 rounded bg-neutral-800 border border-neutral-700 text-neutral-300">전체 {{ totalCount }}</span>
            <span class="px-2 py-0.5 rounded bg-emerald-900/30 border border-emerald-700 text-emerald-300">성공 {{ successCount }}</span>
            <span class="px-2 py-0.5 rounded bg-rose-900/30 border border-rose-700 text-rose-300">실패 {{ errorCount }}</span>
          </div>
          <label class="flex items-center gap-1 cursor-pointer select-none">
            <input type="checkbox" v-model="auto" /> 자동
          </label>
          <div class="flex items-center gap-1">
            간격 <input type="range" min="5" max="60" v-model.number="intervalSec" @change="onIntervalChange" /> <span>{{ intervalSec }}s</span>
          </div>
          <div class="flex items-center gap-1">
            표시 <input type="range" min="20" max="200" step="10" v-model.number="limit" /> <span>{{ limit }}</span>
          </div>
          <span v-if="error" class="px-2 py-0.5 rounded bg-brand-danger/20 text-brand-danger">{{ error }}</span>
        </div>
      </div>
      <div class="overflow-x-auto hidden md:block">
        <table class="min-w-full text-sm select-none">
          <thead class="text-left text-neutral-400 border-b border-neutral-700/60">
            <tr>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('id')">ID <SortIcon k="id" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('status')">Status <SortIcon k="status" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4">Trigger</th>
              <th class="py-1 pr-4">Feature</th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('version')">Version <SortIcon k="version" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4">Outcome</th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('auc')">AUC <SortIcon k="auc" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('accuracy')">ACC <SortIcon k="accuracy" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('brier')">Brier <SortIcon k="brier" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('ece')">ECE <SortIcon k="ece" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4">val_samples</th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('created_at')">Created <SortIcon k="created_at" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4">Finished</th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('duration')">Duration <SortIcon k="duration" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4">Artifact</th>
              <th class="py-1 pr-4">Error</th>
              <th class="py-1 pr-4">Details</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="j in filteredSorted" :key="j.id" class="border-b border-neutral-800/40 hover:bg-neutral-800/30">
              <td class="py-1 pr-4 font-mono">{{ j.id }}</td>
              <td class="py-1 pr-4"><span :class="statusColor(j.status)">{{ j.status }}</span></td>
              <td class="py-1 pr-4">{{ j.trigger }}</td>
              <td class="py-1 pr-4" :title="j.drift_z != null ? ('z=' + j.drift_z) : undefined">{{ j.drift_feature || '—' }}</td>
              <td class="py-1 pr-4">
                {{ j.version || '—' }}
                <span v-if="!j.metrics" class="ml-1 text-[10px] text-neutral-500" title="메트릭 없음 (레거시/실패)">legacy</span>
              </td>
              <td class="py-1 pr-4">
                <span v-if="outcome(j)" class="px-2 py-0.5 rounded bg-neutral-800 border border-neutral-700 text-neutral-300" :title="outcomeDetail(j)">{{ outcome(j) }}</span>
                <span v-else>—</span>
              </td>
              <td class="py-1 pr-4">{{ metric(j, 'auc') }}</td>
              <td class="py-1 pr-4">{{ metric(j, 'accuracy') }}</td>
              <td class="py-1 pr-4">{{ metric(j, 'brier') }}</td>
              <td class="py-1 pr-4">{{ metric(j, 'ece') }}</td>
              <td class="py-1 pr-4">{{ metric(j, 'val_samples') }}</td>
              <td class="py-1 pr-4" :title="j.created_at">{{ shortTime(j.created_at) }}</td>
              <td class="py-1 pr-4" :title="j.finished_at || undefined">{{ j.finished_at ? shortTime(j.finished_at) : '—' }}</td>
              <td class="py-1 pr-4 font-mono" :title="durationTitle(j)">{{ durationLabel(j) }}</td>
              <td class="py-1 pr-4 truncate max-w-[160px]" :title="j.artifact_path ?? undefined">{{ j.artifact_path ? (j.artifact_path.split('/').pop()) : '—' }}</td>
              <td class="py-1 pr-4 text-brand-danger max-w-[160px] truncate" :title="j.error ?? undefined">{{ j.error ? j.error : '—' }}</td>
              <td class="py-1 pr-4">
                <button class="btn btn-xs" @click="openDetails(j)">보기</button>
              </td>
            </tr>
            <tr v-if="!loading && filteredSorted.length === 0">
              <td :colspan="detailsColspan" class="py-4 text-center text-neutral-500">
                기록이 없습니다. 과거 데이터가 DB에 남아있다면 상단 표시 개수를 늘려보세요.
              </td>
            </tr>
          </tbody>
        </table>
  <!-- Details viewer -->
        <div v-if="selectedJob" class="mt-4 p-3 rounded border border-neutral-700 bg-neutral-900/60">
          <div class="flex items-center justify-between mb-2">
            <div class="text-sm font-semibold">선택한 Job 상세 (ID: {{ selectedJob.id }})</div>
            <div class="flex items-center gap-2">
              <span v-if="selectedJob.metrics?.status" class="text-xs text-neutral-400">status: {{ selectedJob.metrics.status }}</span>
              <button class="btn btn-xs" @click="selectedJob=null">닫기</button>
            </div>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <div class="text-xs text-neutral-400 mb-1">요약</div>
              <ul class="text-xs leading-5">
                <li><b>trigger:</b> <span class="font-mono">{{ selectedJob.trigger }}</span></li>
                <li><b>version:</b> <span class="font-mono">{{ selectedJob.version || '—' }}</span></li>
                <li><b>artifact:</b> <span class="font-mono">{{ selectedJob.artifact_path || '—' }}</span></li>
                <li><b>created:</b> <span class="font-mono">{{ shortTime(selectedJob.created_at) }}</span></li>
                <li><b>finished:</b> <span class="font-mono">{{ selectedJob.finished_at ? shortTime(selectedJob.finished_at) : '—' }}</span></li>
                <li><b>duration:</b> <span class="font-mono">{{ durationLabel(selectedJob) }}</span></li>
                <li v-if="selectedJob.metrics?.have != null && selectedJob.metrics?.required != null">
                  <b>labels:</b> have={{ selectedJob.metrics.have }}, required={{ selectedJob.metrics.required }}
                </li>
                <li v-if="selectedJob.metrics?.pos_ratio != null"><b>pos_ratio:</b> {{ selectedJob.metrics.pos_ratio }}</li>
                <li v-if="selectedJob.error" class="text-brand-danger"><b>error:</b> {{ selectedJob.error }}</li>
              </ul>
            </div>
            <div>
              <div class="text-xs text-neutral-400 mb-1">metrics JSON</div>
              <pre class="text-[11px] leading-4 whitespace-pre-wrap break-words max-h-64 overflow-auto">{{ pretty(selectedJob.metrics) }}</pre>
            </div>
          </div>
          <div class="text-[11px] text-neutral-500 mt-2">팁: insufficient_data는 학습 라벨 데이터가 부족할 때 나타납니다. Admin → Artifacts Verify 또는 Admin에서 Feature Backfill을 사용해 데이터를 채워보세요.</div>
        </div>
      </div>
      <!-- Mobile: stacked cards list -->
      <div class="block md:hidden">
        <div class="space-y-2">
          <div v-for="j in filteredSorted" :key="j.id" class="p-3 rounded border border-neutral-700 bg-neutral-900/40">
            <div class="flex items-start justify-between gap-2">
              <div class="text-[13px] font-semibold break-all leading-5">
                <span class="text-neutral-400">ID</span>
                <span class="ml-2 font-mono break-words">{{ j.id }}</span>
              </div>
              <span :class="statusColor(j.status)" class="px-2 py-0.5 rounded text-[11px]">{{ j.status }}</span>
            </div>
            <div class="mt-1 grid grid-cols-2 gap-2 text-[11px]">
              <div class="text-neutral-400">trigger</div>
              <div class="font-mono break-words">{{ j.trigger }}</div>
              <div class="text-neutral-400">version</div>
              <div class="font-mono break-words">{{ j.version || '—' }}<span v-if="!j.metrics" class="ml-1 text-[10px] text-neutral-500">legacy</span></div>
              <div class="text-neutral-400">feature</div>
              <div class="break-words" :title="j.drift_z != null ? ('z=' + j.drift_z) : undefined">{{ j.drift_feature || '—' }}</div>
              <div class="text-neutral-400">AUC</div>
              <div>{{ metric(j,'auc') }}</div>
              <div class="text-neutral-400">ECE</div>
              <div>{{ metric(j,'ece') }}</div>
              <div class="text-neutral-400">VAL</div>
              <div>{{ metric(j,'val_samples') }}</div>
              <div class="text-neutral-400">Created</div>
              <div class="font-mono" :title="j.created_at">{{ shortTime(j.created_at) }}</div>
              <div class="text-neutral-400">Finished</div>
              <div class="font-mono" :title="j.finished_at || undefined">{{ j.finished_at ? shortTime(j.finished_at) : '—' }}</div>
              <div class="text-neutral-400">Duration</div>
              <div class="font-mono" :title="durationTitle(j)">{{ durationLabel(j) }}</div>
              <div class="text-neutral-400">Outcome</div>
              <div>
                <span v-if="outcome(j)" class="px-1.5 py-0.5 rounded bg-neutral-800 border border-neutral-700 text-neutral-300 break-words" :title="outcomeDetail(j)">{{ outcome(j) }}</span>
                <span v-else>—</span>
              </div>
              <div class="text-neutral-400">Artifact</div>
              <div class="font-mono break-words">{{ j.artifact_path ?? '—' }}</div>
              <div class="text-neutral-400">Error</div>
              <div class="text-brand-danger break-words">{{ j.error || '—' }}</div>
            </div>
            <div class="mt-2 flex items-center justify-end">
              <button class="btn btn-xs" @click="openDetails(j)">상세</button>
            </div>
          </div>
          <div v-if="!loading && filteredSorted.length === 0" class="py-4 text-center text-neutral-500 text-[12px]">
            기록이 없습니다. 과거 데이터가 DB에 남아있다면 상단 표시 개수를 늘려보세요.
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useTrainingJobsStore } from '../stores/trainingJobs';
import ConfirmDialog from '../components/ConfirmDialog.vue';
import { buildApiKeyHeaders } from '../lib/apiKey';

const store = useTrainingJobsStore();
const route = useRoute();
const router = useRouter();
const { fetchJobs, start } = store as any;
// Reactive wrappers to avoid losing reactivity via destructuring
const loading = computed(() => (store as any).loading);
const error = computed(() => (store as any).error);
const statusFilter = computed({ get: () => (store as any).statusFilter, set: (v) => (store as any).statusFilter = v });
const triggerFilter = computed({ get: () => (store as any).triggerFilter, set: (v) => (store as any).triggerFilter = v });
// target filter removed (bottom-only)
const auto = computed({ get: () => (store as any).auto, set: (v: boolean) => (store as any).toggleAuto(v) });
const intervalSec = computed({ get: () => (store as any).intervalSec, set: (v: number) => (store as any).setIntervalSec(v) });
const filteredSorted = computed(() => (store as any).filteredSorted);
const sortKey = computed(() => (store as any).sortKey);
const sortDir = computed(() => (store as any).sortDir);
const setSort = (store as any).setSort as (k: string) => void;
const jobDurationMs = (store as any).jobDurationMs as (j: any) => number;
const statusColor = (s: string) => (store as any).statusColor(s);
const limit = computed<number>({ get: () => (store as any).limit as number, set: (v: number) => (store as any).setLimit(v) });
function onIntervalChange(){
  if (typeof (store as any).setIntervalSec === 'function') {
    (store as any).setIntervalSec(((intervalSec as any).value ?? intervalSec) as number);
  }
}
// v-model on computed handles setLimit, so no handler needed

// Run training controls/state
const runSentiment = ref(true);
const runForce = ref(false);
// bottom-only
// Bottom 파라미터 기본값(서버 설정과 일치하게 보수적 권장값으로 초기화)
const bottomParams = ref({
  lookahead: 60,
  drawdown: 0.015,
  rebound: 0.008,
});
// 최초 마운트 시 서버의 프리뷰 기본 파라미터를 읽어 동기화
async function loadBottomDefaultsFromServer() {
  try {
    const r = await fetch(`/api/training/bottom/preview?limit=1200`, { headers: buildApiKeyHeaders() });
    if (!r.ok) return;
    const j = await r.json();
    // API는 params: { lookahead, drawdown, rebound }를 반환
    const p = j?.params;
    if (p && typeof p.lookahead === 'number' && typeof p.drawdown === 'number' && typeof p.rebound === 'number') {
      bottomParams.value.lookahead = Math.max(1, Math.floor(p.lookahead));
      bottomParams.value.drawdown = Math.max(0, Number(p.drawdown));
      bottomParams.value.rebound = Math.max(0, Number(p.rebound));
    }
  } catch {
    // 네트워크/권한 오류 시 무시하고 기본값 유지
  }
}
const runLoading = ref(false);
const runMsg = ref<string | null>(null);

type ConfirmFn = () => void | Promise<void>;
const confirm = ref<{ open: boolean; title: string; message: string; requireText?: string; delayMs?: number; onConfirm?: ConfirmFn | null }>({ open: false, title: '', message: '', requireText: undefined, delayMs: 800, onConfirm: null });
function openConfirm(opts: { title: string; message: string; requireText?: string; delayMs?: number; onConfirm: ConfirmFn }) {
  confirm.value.title = opts.title;
  confirm.value.message = opts.message;
  confirm.value.requireText = opts.requireText;
  confirm.value.delayMs = opts.delayMs ?? 800;
  confirm.value.onConfirm = async () => {
    try {
      await opts.onConfirm();
    } finally {
      confirm.value.open = false;
    }
  };
  confirm.value.open = true;
}

function confirmRunTraining() {
  if (runLoading.value) return;
  const summary: string[] = [
    `sentiment 포함: ${runSentiment.value ? '예' : '아니오'}`,
    `force: ${runForce.value ? '예' : '아니오'}`,
    `bottom 파라미터 → lookahead=${bottomParams.value.lookahead}, drawdown=${bottomParams.value.drawdown}, rebound=${bottomParams.value.rebound}`
  ];
  openConfirm({
    title: '재학습 실행 확인',
    message: `다음 설정으로 학습 잡을 실행할까요?\n${summary.map((line) => `• ${line}`).join('\n')}`,
    requireText: 'RUN',
    delayMs: 800,
    onConfirm: async () => { await runTraining(); },
  });
}

async function runTraining() {
  if (runLoading.value) return;
  runLoading.value = true; runMsg.value = null;
  try {
    const r = await fetch('/api/training/run', {
      method: 'POST',
      headers: buildApiKeyHeaders({
        'Content-Type': 'application/json',
      }),
      body: JSON.stringify({ 
        trigger: 'manual_ui', 
        sentiment: runSentiment.value, 
        force: runForce.value,
        bottom_lookahead: bottomParams.value.lookahead,
        bottom_drawdown: bottomParams.value.drawdown,
        bottom_rebound: bottomParams.value.rebound
      }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    runMsg.value = `시작됨: job ${j.job_id}${j.sentiment_requested ? ' +sentiment':''}`;
    // Quick refresh and keep polling
    await fetchJobs();
    start();
  } catch (e: any) {
    runMsg.value = `시작 실패: ${e?.message || String(e)}`;
  } finally {
    runLoading.value = false;
    // Clear message after a short delay
    window.setTimeout(() => { runMsg.value = null; }, 5000);
  }
}

function metric(j: any, k: string) {
  const v = j.metrics?.[k];
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return v.toFixed(3);
  return String(v);
}
function shortTime(ts: string) {
  // If ISO -> Date parse; if already numeric, new Date(Number*1000)
  try {
    const d = /^\d+(?:\.\d+)?$/.test(ts) ? new Date(parseFloat(ts) * 1000) : new Date(ts);
    // Format YYYY-MM-DD HH:mm in local time
    const pad = (n: number) => (n < 10 ? '0' + n : '' + n);
    const yyyy = d.getFullYear();
    const mm = pad(d.getMonth() + 1);
    const dd = pad(d.getDate());
    const HH = pad(d.getHours());
    const MM = pad(d.getMinutes());
    return `${yyyy}-${mm}-${dd} ${HH}:${MM}`;
  } catch { return ts; }
}

function durationLabel(j: any) {
  const ms = jobDurationMs(j);
  const s = Math.floor(ms / 1000);
  if (s < 60) return s + 's';
  const m = Math.floor(s/60);
  if (m < 60) return m + 'm';
  const h = Math.floor(m/60); return h + 'h';
}
function durationTitle(j: any) {
  return (jobDurationMs(j)/1000).toFixed(1) + 's';
}

// Show a human-friendly outcome when metrics are missing or minimal (insufficient_* cases)
function outcome(j: any): string | null {
  const st = j?.metrics?.status;
  if (!st) return null;
  // Map known statuses to compact labels
  const m: Record<string,string> = {
    insufficient_labels: 'insufficient_labels',
    insufficient_data: 'insufficient_data',
    insufficient_features: 'insufficient_features',
    insufficient_samples: 'insufficient_samples',
    insufficient_samples_postfilter: 'insufficient_samples_postfilter',
  };
  return m[st] || String(st);
}
function outcomeDetail(j: any): string | undefined {
  const s = outcome(j);
  if (!s) return undefined;
  const have = (j?.metrics?.have ?? j?.metrics?.labels_have ?? null);
  const req = (j?.metrics?.required ?? j?.metrics?.labels_required ?? null);
  const pr = (j?.metrics?.pos_ratio ?? null);
  const parts: string[] = [s];
  if (have != null && req != null) parts.push(`have=${have}, required=${req}`);
  if (pr != null) parts.push(`pos_ratio=${pr}`);
  return parts.join(' | ');
}

// Sort indicator component (inline minimal)
const SortIcon = (props: { k: string; sortKey: string; sortDir: string }) => {
  if (props.sortKey !== props.k) return null as any;
  return props.sortDir === 'asc' ? '▲' : '▼';
};

onMounted(() => { fetchJobs(); start(); loadBottomDefaultsFromServer(); });

// URL query sync for filters
onMounted(() => {
  const qs = route.query || {};
  const s = String(qs.status || '').toLowerCase();
  const t = String(qs.trigger || '').toLowerCase();
  const statusOpts = ['all','running','success','error'];
  const triggerOpts = ['all','manual','auto','other'];
  if (statusOpts.includes(s)) (store as any).statusFilter = s;
  if (triggerOpts.includes(t)) (store as any).triggerFilter = t;
});

watch([statusFilter, triggerFilter], ([s, t]) => {
  const q = { ...route.query } as any;
  if (s && s !== 'all') q.status = s; else delete q.status;
  if (t && t !== 'all') q.trigger = t; else delete q.trigger;
  router.replace({ query: q });
});

// Summary badges
const totalCount = computed(() => store.jobs.length);
const successCount = computed(() => store.jobs.filter(j => j.status === 'success').length);
const errorCount = computed(() => store.jobs.filter(j => j.status === 'error').length);

// Details viewer state and helpers
const selectedJob = ref<any | null>(null);
function openDetails(j: any) { selectedJob.value = j; }
const detailsColspan = computed(() => 17); // keep in sync with header column count
function pretty(v: any) { try { return JSON.stringify(v ?? {}, null, 2); } catch { return String(v); } }
</script>

<style scoped>
table { border-collapse: collapse; }
.break-words { word-break: break-word; overflow-wrap: anywhere; }
</style>