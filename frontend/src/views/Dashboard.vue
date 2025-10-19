<template>
  <div class="space-y-6">
    <!-- Unified System Status -->
    <section class="card">
      <h2 class="text-lg font-semibold mb-2 flex items-center gap-2">시스템 상태
        <span class="text-[10px] text-neutral-400 font-normal" title="실시간 핵심 구성요소 상태 및 Fast Startup 적용 여부">도움말</span>
        <span v-if="sysLoading" class="inline-block w-3 h-3 rounded-full border-2 border-t-transparent border-brand-accent animate-spin"></span>
      </h2>
      <div class="text-xs text-neutral-400 mb-3">주요 컴포넌트 통합 상태 (/api/system/status)</div>
      <div class="space-y-3 opacity-50" v-if="!systemLoaded">초기 로딩 중...</div>
      <div class="space-y-3" v-else>
        <div class="flex flex-wrap gap-2 text-[11px] items-center">
          <span class="px-2 py-0.5 rounded font-medium" :class="overallBadgeClass">OVERALL: {{ system.overall || '-' }}</span>
          <span v-if="system.fast_startup" class="px-2 py-0.5 rounded bg-neutral-600/60 border border-neutral-500/40">FAST_STARTUP</span>
          <span v-if="modelReady" class="px-2 py-0.5 rounded bg-emerald-700/30 text-emerald-300 border border-emerald-600/40" title="요청된 자동 부트스트랩 이후 모델이 확인되었습니다">MODEL CREATED</span>
          <span class="text-neutral-400">DB: <span :class="system.db?.has_pool ? 'text-brand-accent':'text-brand-danger'">{{ system.db?.has_pool ? 'ok':'no-pool' }}</span></span>
          <span v-if="system.skipped_components?.length" class="text-neutral-400">skipped: {{ system.skipped_components.join(', ') }}</span>
        </div>
        <SystemComponentGrid :components-raw="system.components" />
        <div class="flex items-center gap-3 text-[10px]">
          <button class="btn btn-sm" @click="fetchSystem" :disabled="sysLoading">새로고침</button>
          <label class="flex items-center gap-1 cursor-pointer">
            <input type="checkbox" v-model="sysAuto" class="accent-brand-primary" /> 자동 {{ sysInterval }}s
          </label>
          <input type="range" min="5" max="60" v-model.number="sysInterval" class="w-40" />
          <span class="text-neutral-500" v-if="sysUpdated">{{ new Date(sysUpdated).toLocaleTimeString() }}</span>
        </div>
        <details class="text-[10px] opacity-70 max-h-48 overflow-auto">
          <summary class="cursor-pointer">raw system JSON</summary>
          <pre>{{ system }}</pre>
        </details>
        <details class="mt-2 text-[11px] opacity-90">
          <summary class="cursor-pointer select-none">Auto bootstrap settings</summary>
          <div class="mt-2 grid grid-cols-1 md:grid-cols-2 gap-3">
            <label class="flex items-center gap-2">LA
              <input class="input w-full" v-model="autoCfg.LA" placeholder="30,40,60" />
            </label>
            <label class="flex items-center gap-2">DD
              <input class="input w-full" v-model="autoCfg.DD" placeholder="0.0075,0.01,0.015" />
            </label>
            <label class="flex items-center gap-2">RB
              <input class="input w-full" v-model="autoCfg.RB" placeholder="0.004,0.006,0.008" />
            </label>
            <label class="flex items-center gap-2">pos_ratio min
              <input class="input w-24" type="number" step="0.01" min="0" max="1" v-model.number="autoCfg.posMin" />
            </label>
            <label class="flex items-center gap-2">pos_ratio max
              <input class="input w-24" type="number" step="0.01" min="0" max="1" v-model.number="autoCfg.posMax" />
            </label>
            <label class="flex items-center gap-2">backfill target
              <input class="input w-28" type="number" min="100" max="5000" v-model.number="autoCfg.backfillTarget" />
            </label>
            <label class="flex items-center gap-2">min inserted before train
              <input class="input w-28" type="number" min="50" max="4000" v-model.number="autoCfg.minInserted" />
            </label>
            <label class="flex items-center gap-2">model wait (s)
              <input class="input w-24" type="number" min="5" max="300" v-model.number="autoCfg.modelWaitSec" />
            </label>
          </div>
          <div class="mt-2 text-[10px] text-neutral-400">입력은 세션에만 저장됩니다.</div>
        </details>
      </div>
    </section>
    <section class="card">
      <h1 class="text-xl font-semibold mb-2 flex items-center gap-2">대시보드
        <span class="text-[10px] text-neutral-400 font-normal" title="주요 거래/추론 활동과 인프라 상태 요약">도움말</span>
      </h1>
      <p class="text-sm text-neutral-300">기본 상태 요약</p>
          <!-- Trading Activity Panel (리팩토링 1차: MetricCard / disable_reason 표시) -->
          <div class="mt-4 p-3 rounded bg-neutral-800/40 border border-neutral-700/40 space-y-3" title="최근 1/5분 결정 빈도와 자동 루프 상태를 모니터링">
            <div class="flex items-center justify-between">
              <div class="text-sm font-semibold flex items-center gap-2">
                Trading Activity
                <StatusBadge :status="ta.summary?.auto_loop_enabled ? 'ok':'idle'">
                  {{ ta.summary?.auto_loop_enabled ? '자동' : '수동' }}
                </StatusBadge>
                <StatusBadge v-if="disableReasonHuman" status="warning">비활성: {{ disableReasonHuman }}</StatusBadge>
              </div>
              <div class="flex items-center gap-2 text-[10px]">
                <button class="btn btn-xs" @click="fetchActivity" :disabled="ta.refreshing">갱신</button>
                <span v-if="ta.refreshing" class="inline-block w-3 h-3 rounded-full border-2 border-t-transparent border-brand-accent animate-spin" />
                <label class="flex items-center gap-1 cursor-pointer select-none">
                  <input type="checkbox" v-model="taAuto" class="accent-brand-primary" /> poll 10s
                </label>
                <span v-if="ta.summary?.interval" class="text-neutral-500">loop {{ ta.summary.interval }}s</span>
              </div>
            </div>
            <div v-if="ta.initialLoading" class="text-xs text-neutral-400 animate-pulse">불러오는 중...</div>
            <div class="grid grid-cols-2 md:grid-cols-6 gap-3 text-[11px]" v-if="ta.summary">
              <MetricCard label="최근 결정 시각" :value="lastDecisionLabel" :status="lastDecisionStatus" />
              <MetricCard label="1분 결정 수" :value="ta.summary?.decisions_1m ?? '-'" :status="decisions1mStatus" />
              <MetricCard label="5분 결정 수" :value="ta.summary?.decisions_5m ?? '-'" :status="decisions5mStatus" />
              <MetricCard label="연속 유휴" :value="idleStreakLabel" :status="idleStatus" />
              <MetricCard label="자동 루프" :value="ta.summary?.auto_loop_enabled ? 'on':'off'" :status="ta.summary?.auto_loop_enabled ? 'ok':'idle'" />
              <MetricCard label="활동 상태" :value="activityOverall" :status="activityStatus" />
            </div>
          </div>
          <!-- Recent Decisions Panel -->
          <div class="mt-6">
            <RecentDecisionsPanel />
          </div>
  <div class="mt-4 grid grid-cols-2 md:grid-cols-6 gap-4 text-sm">
        <div class="p-3 rounded bg-neutral-700/40 flex flex-col justify-between">
          <div class="text-neutral-400">Enabled</div>
          <div class="font-semibold" :class="status.enabled ? 'text-brand-accent':'text-neutral-500'">{{ status.enabled ?? '-' }}</div>
        </div>
        <div class="p-3 rounded bg-neutral-700/40 flex flex-col justify-between">
          <div class="text-neutral-400">Running</div>
          <div class="font-semibold" :class="status.running ? 'text-brand-accent':'text-neutral-500'">{{ status.running ?? '-' }}</div>
        </div>
        <div class="p-3 rounded bg-neutral-700/40 flex flex-col justify-between">
          <div class="text-neutral-400">Total Msg</div>
          <div class="font-semibold">{{ status.total_messages ?? '-' }}</div>
        </div>
        <div class="p-3 rounded bg-neutral-700/40 flex flex-col justify-between">
          <div class="text-neutral-400">Reconnect</div>
          <div class="font-semibold">{{ status.reconnect_attempts ?? '-' }}</div>
        </div>
        <div class="p-3 rounded bg-neutral-700/40 flex flex-col justify-between">
          <div class="text-neutral-400 flex items-center justify-between">
            <span>Lag (s)</span>
            <span v-if="stale" class="text-xs px-1 rounded bg-brand-danger/20 text-brand-danger">STALE</span>
          </div>
          <div class="font-semibold">{{ lag }}</div>
          <div class="mt-1">
            <svg :width="sparkW" :height="sparkH" v-if="sparkPath">
              <path :d="sparkPath" fill="none" stroke="#10b981" stroke-width="2" />
            </svg>
            <div v-else class="h-6"></div>
          </div>
        </div>
        <div class="p-3 rounded bg-neutral-700/40 flex flex-col justify-between">
          <div class="text-neutral-400 flex items-center justify-between">
            <span>Buffer</span>
            <span class="text-xs" :class="bufferFillRatio > 0.8 ? 'text-brand-danger' : bufferFillRatio > 0.5 ? 'text-amber-400':'text-brand-accent'">{{ (bufferFillRatio*100).toFixed(0) }}%</span>
          </div>
          <div class="font-semibold">{{ status.buffer_size ?? 0 }}/{{ status.batch_size ?? '-' }}</div>
          <div class="mt-2 h-2 w-full rounded bg-neutral-600 overflow-hidden">
            <div class="h-full transition-all" :class="bufferBarClass" :style="{ width: (bufferFillRatio*100).toFixed(0)+'%' }"></div>
          </div>
          <div class="mt-1 text-[10px] text-neutral-400">flush {{ flushAgeLabel }}</div>
        </div>
      </div>
      <div class="mt-4 flex flex-wrap items-center gap-3 text-xs">
        <button class="btn" @click="refresh" :disabled="loading">수동 새로고침</button>
        <label class="flex items-center gap-1 cursor-pointer select-none">
          <input type="checkbox" v-model="auto" class="accent-brand-primary" /> 자동 갱신
        </label>
        <div class="flex items-center gap-1">
          간격
          <input type="range" min="2" max="15" v-model.number="intervalSec" />
          <span>{{ intervalSec }}s</span>
        </div>
        <span v-if="lastUpdated" class="text-neutral-400">갱신: {{ new Date(lastUpdated).toLocaleTimeString() }}</span>
        <span v-if="error" class="px-2 py-0.5 rounded bg-brand-danger/20 text-brand-danger">에러: {{ error }}</span>
      </div>
  <FastStartupAlert :fast="fast" :ingestion-enabled="status.enabled ?? null" :ingestion-running="(status.running === undefined ? null : status.running)" :upgrade-loading="upgradeLoading" @upgrade="upgrade" />
      <details class="mt-4 text-[10px] opacity-70 max-h-48 overflow-auto">
        <summary class="cursor-pointer">raw status JSON</summary>
        <pre>{{ status }}</pre>
      </details>
      <details class="mt-2 text-[10px] opacity-70 max-h-48 overflow-auto" v-if="fast">
        <summary class="cursor-pointer">fast_startup status</summary>
        <pre>{{ fast }}</pre>
      </details>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref, watch, onBeforeUnmount, computed } from 'vue';
import { storeToRefs } from 'pinia';
import { usePollingManager } from '../composables/usePollingManager';
import StatusBadge from '../components/StatusBadge.vue';
import MetricCard from '../components/MetricCard.vue';
import { classifyDecisionAge, classifyDecisionThroughput, humanDisableReason } from '../utils/status';
import SystemComponentGrid from '../components/SystemComponentGrid.vue';
import FastStartupAlert from '../components/FastStartupAlert.vue';
import RecentDecisionsPanel from '../components/RecentDecisionsPanel.vue';
import { useRecentDecisionsStore } from '../stores/recentDecisions';
import { useIngestionStore } from '../stores/ingestion';
import http from '../lib/http';
import { useToastStore } from '../stores/toast';
import { useTradingActivityStore } from '../stores/tradingActivity';
import { validateResponse } from '../validation/validate';
import { systemStatusSpec, tradingSummarySpec } from '../validation/specs';

const store = useIngestionStore();
const { status, lag, stale, sparkPath, bufferFillRatio, bufferBarClass, flushAgeLabel, loading, error, lastUpdated, auto, intervalSec } = storeToRefs(store);
const sparkW = 100; const sparkH = 24; // dimensions sourced in store path computation

function refresh() { store.fetchStatus(); }

onMounted(() => {
  // Immediate fetch and start dashboard-managed polling for ingestion status
  store.fetchStatus();
  startIngestionLoop();
  // fetch fast status first, then orchestrate to avoid race with upgrade_possible flag
  fetchFast().finally(() => {
    try { autoOrchestrateOnce(); } catch { /* no-op */ }
  });
  fetchSystem();
  startSysLoop();
  fetchActivity();
  startActivityLoop();
  // auto start recent decisions polling
  startRecentDecisionsLoop();
  // Auto-orchestrate startup happens after fetchFast()
});

const fast = reactive<any>({ fast_startup: false, skipped_components: [], degraded_components: [], upgrade_possible: false });
const upgradeLoading = ref(false);
const modelReady = ref(false);
// Auto settings (session-persisted)
const autoCfg = reactive<{ LA: string; DD: string; RB: string; posMin: number; posMax: number; backfillTarget: number; minInserted: number; modelWaitSec: number }>({
  LA: (sessionStorage.getItem('auto.LA') || '30,40,60'),
  DD: (sessionStorage.getItem('auto.DD') || '0.0075,0.01,0.015'),
  RB: (sessionStorage.getItem('auto.RB') || '0.004,0.006,0.008'),
  posMin: Number(sessionStorage.getItem('auto.posMin') || 0.08),
  posMax: Number(sessionStorage.getItem('auto.posMax') || 0.4),
  backfillTarget: Number(sessionStorage.getItem('auto.backfillTarget') || 600),
  minInserted: Number(sessionStorage.getItem('auto.minInserted') || 300),
  modelWaitSec: Number(sessionStorage.getItem('auto.modelWaitSec') || 60),
});
watch(() => ({...autoCfg}), (v:any) => {
  try {
    sessionStorage.setItem('auto.LA', String(v.LA));
    sessionStorage.setItem('auto.DD', String(v.DD));
    sessionStorage.setItem('auto.RB', String(v.RB));
    sessionStorage.setItem('auto.posMin', String(v.posMin));
    sessionStorage.setItem('auto.posMax', String(v.posMax));
    sessionStorage.setItem('auto.backfillTarget', String(v.backfillTarget));
    sessionStorage.setItem('auto.minInserted', String(v.minInserted));
    sessionStorage.setItem('auto.modelWaitSec', String(v.modelWaitSec));
  } catch { /* ignore */ }
}, { deep: true });

async function fetchFast() {
  try {
    const { data } = await http.get('/admin/fast_startup/status');
    Object.assign(fast, data || {});
    // 간단 검증
    if (typeof fast.fast_startup !== 'boolean') toast.warn('fast_startup 필드 이상', typeof fast.fast_startup as any);
    if (fast.skipped_components && !Array.isArray(fast.skipped_components)) toast.warn('skipped_components 타입 이상', typeof fast.skipped_components as any);
  } catch (e) {
    toast.error('Fast Startup 상태 조회 실패', (e as any)?.message || '알 수 없는 오류');
  }
}

// One-shot auto orchestration on dashboard load
async function autoOrchestrateOnce() {
  const KEY = 'dashboard_auto_chain_v1';
  const val = sessionStorage.getItem(KEY);
  if (val === 'done' || val === 'running') return;
  // mark as running to avoid duplicate triggers during hot-reload/navigation
  sessionStorage.setItem(KEY, 'running');
  try {
    // Ensure fresh fast status
    await fetchFast();
    let didUpgrade = false;
    if (fast && fast.upgrade_possible === true) {
      try {
        const { data } = await http.post('/admin/fast_startup/upgrade');
        if (data?.started && Array.isArray(data.started) && data.started.length > 0) {
          didUpgrade = true;
          toast.info('자동 업그레이드 시작', data.started.join(', '));
        }
        // Refresh panels quickly
        await Promise.allSettled([fetchFast(), store.fetchStatus(), fetchSystem()])
      } catch (e: any) {
        // Don't block the rest
        toast.warn('자동 업그레이드 실패', e?.message || 'upgrade error');
      }
    }

    // Chain: Year backfill when upgrade actually ran
    if (didUpgrade) {
      try {
        const { data } = await http.post('/api/ohlcv/backfill/year/start');
        if (data?.status) toast.info('연간 백필 시작', String(data.status));
        // give the backfill runner a brief head start
        await new Promise(res => setTimeout(res, 1500));
      } catch (e: any) {
        toast.warn('연간 백필 시작 실패', e?.message || 'backfill error');
      }
    }

    // If there's no model yet, first backfill features, then auto-trigger one training run (bottom-only)
    try {
      const { data: ms } = await http.get('/api/models/summary', { params: { limit: 1, name: 'bottom_predictor', model_type: 'supervised' } });
      const hasModel = !!(ms && ms.has_model);
      if (!hasModel) {
        // 1) Suggest/auto-run a short feature backfill to ensure snapshots exist
        try {
          const params: any = { target: Math.max(50, Math.min(Number(autoCfg.backfillTarget)||600, 5000)) };
          await http.post('/admin/features/backfill', null, { params });
          toast.info('피처 백필 시작', `target=${params.target}`);
          // allow some time for initial inserts before training
          await new Promise(res => setTimeout(res, 1500));
        } catch (e: any) {
          toast.warn('피처 백필 시작 실패', e?.message || 'features/backfill error');
        }
        // 1.2) Wait until a minimum inserted snapshots is observed (best-effort)
        try { await waitForBackfillInserted(autoCfg.minInserted, Math.max(5000, autoCfg.modelWaitSec*1000/2)); } catch { /* ignore */ }
        // 1.5) Verify artifacts to surface any missing/corrupt items
        try {
          const { data } = await http.get('/admin/models/artifacts/verify');
          const summary = (data && data.summary) ? JSON.stringify(data.summary) : 'ok';
          toast.info('Artifacts 검증 완료', summary.slice(0, 120));
        } catch (e: any) {
          toast.warn('Artifacts 검증 실패', e?.message || 'artifacts verify error');
        }
        // 2) Choose bottom params via small sweep to avoid insufficient_labels
        async function previewOne(L: number, D: number, R: number) {
          try {
            const { data } = await http.get('/api/training/bottom/preview', { params: { limit: 3000, lookahead: L, drawdown: D, rebound: R } });
            return { ok: true, data, L, D, R };
          } catch (e: any) { return { ok: false, err: e, L, D, R }; }
        }
        // Parse arrays from settings
        function parseNums(s: string): number[] { return String(s).split(',').map(x => Number(x.trim())).filter(v => isFinite(v)); }
        const LA = parseNums(autoCfg.LA); if (LA.length===0) LA.push(30,40,60);
        const DD = parseNums(autoCfg.DD); if (DD.length===0) DD.push(0.0075,0.01,0.015);
        const RB = parseNums(autoCfg.RB); if (RB.length===0) RB.push(0.004,0.006,0.008);
        const results: any[] = [];
        for (const L of LA) {
          for (const D of DD) {
            for (const R of RB) {
              const r = await previewOne(L, D, R);
              if (r.ok) results.push(r);
            }
          }
        }
        function pickBest(rows: any[]) {
          const ok = rows.filter(r => r.data && (r.data.status === 'ok' || r.data.status === 'insufficient_labels'))
            .map(r => {
              const d = r.data; const have = d.have || 0; const req = d.required || 150; const pr = d.pos_ratio;
              return { ...r, have, req, delta: have - req, pr };
            });
          // Exclude extreme pos_ratio (0% or 100%), apply configured bounds when present
          let cands = ok.filter(r => {
            const pr = r.pr;
            if (pr === 0 || pr === 1) return false;
            const lo = (isFinite(autoCfg.posMin) ? autoCfg.posMin : 0.08);
            const hi = (isFinite(autoCfg.posMax) ? autoCfg.posMax : 0.4);
            return r.have >= r.req && (pr == null || (pr >= lo && pr <= hi));
          });
          if (cands.length === 0) cands = ok.slice();
          if (cands.length === 0) return null;
          cands.sort((a,b) => {
            const da = (a.have >= a.req ? a.delta : Math.abs(a.delta)+10000);
            const db = (b.have >= b.req ? b.delta : Math.abs(b.delta)+10000);
            const pa = Math.abs(((a.pr ?? 0.25) - 0.2));
            const pb = Math.abs(((b.pr ?? 0.25) - 0.2));
            return da - db || pa - pb;
          });
          return cands[0];
        }
        const best = pickBest(results);
        let lookahead = 60, drawdown = 0.015, rebound = 0.008;
        if (best) { lookahead = best.L; drawdown = best.D; rebound = best.R; }
        toast.info('학습 파라미터 선택', `L=${lookahead}, D=${drawdown}, R=${rebound}`);
        // 2) Trigger initial training
        try {
          await http.post('/api/training/run', {
            trigger: 'auto_dashboard',
            sentiment: true,
            force: false,
            bottom_lookahead: lookahead,
            bottom_drawdown: drawdown,
            bottom_rebound: rebound,
            limit: 3000,
            store: true,
          });
          toast.info('초기 모델 학습 시작', `lookahead=${lookahead}, dd=${drawdown}, rb=${rebound}`);
          // 3) Wait for model to appear and surface a badge
          try { const ok = await waitForModelReady(autoCfg.modelWaitSec * 1000); modelReady.value = ok; } catch { /* ignore */ }
        } catch (e: any) {
          toast.warn('초기 모델 학습 실패', e?.message || 'training error');
        }
      }
    } catch { /* ignore */ }
  } catch { /* ignore */ }
  finally {
    // mark completion regardless of success to avoid infinite retries in a single session
    sessionStorage.setItem(KEY, 'done');
  }
}

// ----- Unified System Status -----
const system = reactive<any>({});
const sysLoading = ref(false);
const sysUpdated = ref<number| null>(null);
const sysAuto = ref(true);
const sysInterval = ref(15);
// Polling Manager 사용
const polling = usePollingManager();
let sysTimer: any = null; // 삭제 예정 (레거시 유지 후 제거)

async function fetchSystem() {
  sysLoading.value = true;
  try {
    const { data } = await http.get('/api/system/status');
    Object.assign(system, data || {});
    const vres = validateResponse(system, systemStatusSpec);
    if (vres.errors.length) toast.error('System 응답 오류', vres.errors.slice(0,2).join('\n'));
    if (vres.warnings.length) toast.warn('System 응답 경고', vres.warnings.slice(0,2).join('\n'));
    sysUpdated.value = Date.now();
  } catch (e) {
    toast.error('System 상태 조회 실패', (e as any)?.message || '알 수 없는 오류');
  } finally {
    sysLoading.value = false;
  }
}
function startSysLoop() {
  polling.register({
    id: 'system-status',
    interval: sysInterval.value * 1000,
    enabled: () => sysAuto.value,
    immediate: true,
    run: () => fetchSystem(),
  });
}
function stopSysLoop() { polling.remove('system-status'); }

watch(sysInterval, () => startSysLoop());
watch(sysAuto, (v: boolean) => { if (v) fetchSystem(); });
onBeforeUnmount(() => {
  // stop all background polling started from dashboard
  stopSysLoop();
  stopIngestionLoop();
  stopActivityLoop();
  stopRecentDecisionsLoop();
});
// Trading Activity logic
const ta = useTradingActivityStore();
const taAuto = ref(true);
function fetchActivity() { ta.fetchSummary(); }
function startActivityLoop() {
  polling.register({
    id: 'trading-activity',
    interval: 10000,
    enabled: () => taAuto.value,
    immediate: true,
    run: () => fetchActivity(),
  });
}
function stopActivityLoop() { polling.remove('trading-activity'); }
watch(taAuto, (v: boolean) => { if (v) fetchActivity(); });
// Trading Activity summary 완료 후 필수 필드 검증 (store 내부 반영 지연 가능 → nextTick 지연 대신 watch 사용)
watch(() => ta.summary, (val) => {
  if (!val) return;
  const vres = validateResponse(val, tradingSummarySpec);
  if (vres.errors.length) toast.error('Trading Summary 오류', vres.errors.slice(0,2).join('\n'));
  if (vres.warnings.length) toast.warn('Trading Summary 경고', vres.warnings.slice(0,2).join('\n'));
  if ((val as any).disable_reason && typeof (val as any).disable_reason !== 'string') {
    toast.warn('disable_reason 형식 이상', typeof (val as any).disable_reason as any);
  }
});
onBeforeUnmount(() => stopActivityLoop());

// Recent decisions polling (auto)
const decStore = useRecentDecisionsStore();
const decAuto = ref(true);
function fetchRecentDecisions() { decStore.fetchRecent(); }
function startRecentDecisionsLoop() {
  polling.register({
    id: 'recent-decisions',
    interval: 10000,
    enabled: () => decAuto.value,
    immediate: true,
    run: () => fetchRecentDecisions(),
  });
}
function stopRecentDecisionsLoop() { polling.remove('recent-decisions'); }

// Idle alert: decisions_5m == 0 5분 이상
const toast = useToastStore();
watch(() => ta.summary?.decisions_5m, (newVal) => {
  if (newVal && newVal > 0) return; // reset handled via idleStreakMinutes
});
// Periodic check every minute for alert (reuse taTimer alternative) => attach separate interval
function startIdleAlertLoop() {
  polling.register({
    id: 'idle-alert',
    interval: 60000,
    enabled: () => true,
    immediate: false,
    run: () => {
      if ((ta.summary?.decisions_5m || 0) === 0 && ta.idleStreakMinutes >= 5) {
        const bucket = Math.floor(ta.idleStreakMinutes / 5);
        if ((window as any).__lastIdleBucket !== bucket) {
          (window as any).__lastIdleBucket = bucket;
          toast.warn('Trading idle', `최근 ${ta.idleStreakMinutes.toFixed(1)}분 의사결정 없음`);
        }
      }
    },
  });
}
startIdleAlertLoop();
onBeforeUnmount(() => polling.remove('idle-alert'));

// Ingestion status polling (Dashboard-managed)
function startIngestionLoop() {
  polling.register({
    id: 'ingestion-status-dashboard',
    interval: intervalSec.value * 1000,
    enabled: () => auto.value === true,
    immediate: true,
    run: () => store.fetchStatus(),
  });
}
function stopIngestionLoop() { polling.remove('ingestion-status-dashboard'); }
watch(intervalSec, () => startIngestionLoop());
watch(auto, (v: boolean) => { if (v) store.fetchStatus(); });

const lastDecisionLabel = computed(() => {
  if (!ta.summary || !ta.summary.last_decision_ts) return '-';
  const ageSec = (Date.now()/1000) - ta.summary.last_decision_ts;
  if (ageSec < 60) return '<1m';
  if (ageSec < 3600) return Math.floor(ageSec/60)+ 'm';
  return (ageSec/3600).toFixed(1)+'h';
});
const lastDecisionAgeSec = computed(() => {
  if (!ta.summary || !ta.summary.last_decision_ts) return null;
  return (Date.now()/1000) - ta.summary.last_decision_ts;
});
const lastDecisionStatus = computed(() => classifyDecisionAge(lastDecisionAgeSec.value ?? null));

const decisions1mStatus = computed(() => {
  const v = ta.summary?.decisions_1m || 0;
  if (v === 0) return 'idle';
  if (v < 2) return 'warning';
  return 'ok';
});
const decisions5mStatus = computed(() => classifyDecisionThroughput(ta.summary?.decisions_5m));
const idleStatus = computed(() => {
  if (ta.idleStreakMinutes >= 10) return 'danger';
  if (ta.idleStreakMinutes >= 5) return 'warning';
  if (ta.idleStreakMinutes === 0) return 'neutral';
  return 'ok';
});
const decisions1mClass = computed(() => {
  const v = ta.summary?.decisions_1m || 0;
  if (v === 0) return 'text-neutral-500';
  if (v < 2) return 'text-amber-300';
  return 'text-green-400';
});
const decisions5mClass = computed(() => {
  const v = ta.summary?.decisions_5m || 0;
  if (v === 0) return 'text-neutral-500';
  if (v < 5) return 'text-amber-300';
  return 'text-green-400';
});
const idleClass = computed(() => {
  if (ta.idleStreakMinutes >= 10) return 'text-brand-danger';
  if (ta.idleStreakMinutes >= 5) return 'text-amber-300';
  return 'text-neutral-300';
});
const idleStreakLabel = computed(() => ta.idleStreakMinutes ? ta.idleStreakMinutes.toFixed(1)+'m' : '-');
const activityOverall = computed(() => {
  if (!ta.summary) return 'unknown';
  if (ta.summary.decisions_5m === 0) return 'idle';
  if (ta.summary.decisions_5m < 5) return 'warming';
  return 'active';
});
const activityStatus = computed(() => {
  const st = activityOverall.value;
  if (st === 'active') return 'ok';
  if (st === 'warming') return 'warning';
  if (st === 'idle') return 'idle';
  return 'unknown';
});

// 타입 정의에 아직 disable_reason 이 없을 수 있어 any 캐스팅
const disableReasonHuman = computed(() => humanDisableReason((ta.summary as any)?.disable_reason));

const overallBadgeClass = computed(() => {
  const ov = system.overall;
  if (ov === 'ok') return 'bg-green-600/30 text-green-300';
  if (ov === 'degraded') return 'bg-amber-600/30 text-amber-300';
  if (ov === 'error') return 'bg-brand-danger/30 text-brand-danger';
  return 'bg-neutral-600/40 text-neutral-200';
});

// componentList 로직은 SystemComponentGrid 로 이동

async function upgrade() {
  if (upgradeLoading.value) return;
  upgradeLoading.value = true;
  const toast = useToastStore();
  try {
    const { data } = await http.post('/admin/fast_startup/upgrade');
    if (data.started?.length) {
      toast.success('Fast Startup 업그레이드 완료', `시작됨: ${data.started.join(', ')}`);
    } else {
      toast.info('변경 없음', '이미 업그레이드 되었거나 시작할 항목이 없습니다');
    }
    await fetchFast();
    // ingestion 상태 재조회
    await store.fetchStatus();
    // 시스템 상태도 즉시 갱신하여 상단 "시스템 상태" 패널이 바로 반영되도록 함
    await fetchSystem();
  } catch (e: any) {
    const friendly = e?.__friendlyMessage || '업그레이드 실패';
    toast.error(friendly, 'fast_startup/upgrade');
  } finally {
    upgradeLoading.value = false;
  }
}

// FastStartup 관련 컴포넌트로 이동함

const intervalCompare = computed(() => ta.compareIntervalWithEnv());
const intervalCompareText = computed(() => {
  if (!intervalCompare.value) return '';
  const { backend, env, match } = intervalCompare.value;
  if (match === null) return '';
  if (match) return `간격 일치(${backend}s)`;
  return `간격 불일치: 서버 ${backend ?? '-'}s / 설정 ${env ?? '-' }s`;
});
const intervalCompareClass = computed(() => {
  if (!intervalCompare.value) return '';
  if (intervalCompare.value.match) return 'border-emerald-600/40 text-emerald-300 bg-emerald-600/10';
  return 'border-amber-600/40 text-amber-300 bg-amber-600/10';
});

const systemLoaded = computed(() => {
  // consider loaded when we have at least one key in components or overall present
  return !!(system && (system.overall || (system.components && Object.keys(system.components).length))) ;
});

// Helpers: poll backfill inserted threshold and model readiness
async function waitForBackfillInserted(minInserted: number, timeoutMs: number = 15000): Promise<boolean> {
  const started = Date.now();
  const cap = Math.max(2000, timeoutMs|0);
  while (Date.now() - started < cap) {
    try {
      const r = await http.get('/api/features/backfill/runs', { params: { page: 1, page_size: 1, sort_by: 'started_at', order: 'desc' } });
      const items: any[] = Array.isArray(r.data) ? r.data : (Array.isArray(r.data?.items) ? r.data.items : []);
      if (items.length > 0) {
        const inserted = Number(items[0]?.inserted || 0);
        if (inserted >= Math.max(1, minInserted|0)) return true;
      }
    } catch { /* ignore */ }
    await new Promise(res => setTimeout(res, 2000));
  }
  return false;
}
async function waitForModelReady(timeoutMs: number = 60000): Promise<boolean> {
  const started = Date.now();
  const cap = Math.max(5000, timeoutMs|0);
  while (Date.now() - started < cap) {
    try {
      const { data: ms } = await http.get('/api/models/summary', { params: { limit: 1, name: 'bottom_predictor', model_type: 'supervised' } });
      if (ms && ms.has_model) return true;
    } catch { /* ignore */ }
    await new Promise(res => setTimeout(res, 2000));
  }
  return false;
}
</script>

<style>
/* add spinner animation if not present */
@keyframes spin { to { transform: rotate(360deg);} }
.animate-spin { animation: spin 0.6s linear infinite; }
</style>
