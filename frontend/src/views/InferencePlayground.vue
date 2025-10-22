<template>
  <div class="space-y-6">
    <section class="card space-y-4">
      <div class="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div class="flex items-center gap-2 md:gap-3 flex-wrap">
          <h1 class="text-xl font-semibold">추론 플레이그라운드</h1>
          <span class="text-[11px] text-neutral-400">컨텍스트: <span class="font-mono">{{ ohlcvSymbol }}</span> · <span class="font-mono">{{ ohlcvInterval || '—' }}</span></span>
          <span class="text-[10px] px-2 py-0.5 rounded border border-neutral-700" :class="selectedTarget==='bottom' ? 'bg-indigo-700/20 text-indigo-300' : 'bg-neutral-700/20 text-neutral-300'" title="현재 추론 타겟">
            {{ selectedTarget }}
          </span>
          <!-- bottom-only; target selector removed -->
          <span v-if="staleBadge" class="text-[10px] px-2 py-0.5 rounded bg-amber-500/20 text-amber-300" :title="staleTitle">입력 STALE</span>
          <span v-if="seedState.active" class="text-[10px] px-2 py-0.5 rounded bg-rose-600/20 text-rose-300" :title="seedTooltip">SEED Fallback</span>
          <span class="text-[10px] px-2 py-0.5 rounded" :class="autoLoop.enabled ? 'bg-emerald-700/20 text-emerald-300' : 'bg-neutral-700/30 text-neutral-300'" :title="autoLoopTooltip">
            Auto Loop: {{ autoLoop.enabled ? (autoLoop.interval + 's') : 'off' }}
          </span>
          <BackendStatusBadge />
        </div>
        <div class="grid grid-cols-2 gap-2 md:flex md:items-center md:gap-3 text-xs">
          <span v-if="error" class="px-2 py-0.5 rounded bg-brand-danger/20 text-brand-danger">{{ error }}</span>
          <span v-if="lastRequestId" class="px-2 py-0.5 rounded bg-neutral-800/50 text-neutral-300 border border-neutral-700">req: {{ lastRequestId }}
            <button class="ml-2 underline text-[11px]" @click="copyReqId(lastRequestId)">복사</button>
          </span>
          <span v-if="errorHelp" class="px-2 py-0.5 rounded bg-neutral-800/50 text-neutral-300 border border-neutral-700">{{ errorHelp }}</span>
        </div>
      </div>

      <div class="grid md:grid-cols-5 gap-6">
        <div class="md:col-span-2 space-y-4 min-w-0">
          <!-- Threshold controls moved to Admin > Inference Playground Controls -->
          <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-2">
            <div class="flex items-center justify-between">
              <h2 class="text-sm font-semibold">결과</h2>
              <button v-if="lastResult" class="text-[11px] px-2 py-0.5 rounded border border-neutral-700 hover:bg-neutral-700/40" @click="copyLastResult">JSON 복사</button>
            </div>
            <div v-if="!lastResult" class="text-neutral-500 text-xs">아직 결과 없음. 실행 버튼을 눌러 요청하세요.</div>
            <div v-else class="space-y-1 text-sm">
              <div class="flex items-center justify-between gap-2">
                <span class="text-neutral-400">{{ probLabel }}</span>
                <span class="font-mono" :title="typeof lastResult?.probability==='number' ? String(lastResult?.probability) : ''">{{ probDisplay }}</span>
              </div>
              <div class="relative h-2 w-full rounded bg-neutral-700 overflow-hidden">
                <!-- Probability fill -->
                <div class="h-full bg-brand-accent transition-all" :style="{ width: probBarWidth }"></div>
                <!-- Threshold marker (visual only) -->
                <div v-if="thresholdMarkerPct" class="absolute top-0 bottom-0 bg-amber-400/90" style="width:2px" :style="{ left: thresholdMarkerPct }" title="임계값 위치"></div>
              </div>
              <div class="flex items-center justify-between gap-2">
                <span class="text-neutral-400">결정</span>
                <span :class="decisionColor(lastResult?.decision)" class="font-semibold">{{ decisionDisplay }}</span>
              </div>
              <div class="flex items-center justify-between gap-2 flex-wrap">
                <span class="text-neutral-400">모델 버전</span>
                <span class="font-mono break-all">{{ lastResult?.model_version ?? '—' }}</span>
              </div>
              <div class="flex items-center justify-between gap-2">
                <span class="text-neutral-400">프로덕션</span>
                <span :class="lastResult?.used_production ? 'text-brand-accent':'text-neutral-500'">{{ lastResult?.used_production ? '예':'아니오' }}</span>
              </div>
              <div class="flex items-center justify-between gap-2">
                <span class="text-neutral-400">임계값</span>
                <span class="font-mono">{{ thresholdDisplay }}</span>
              </div>
              <div class="flex items-center justify-between gap-2" v-if="lastResult?.feature_close_time">
                <span class="text-neutral-400">피처 종료</span>
                <span class="font-mono" :title="String(lastResult?.feature_close_time)">{{ fmtMs(lastResult?.feature_close_time) }}</span>
              </div>
              <div class="flex items-center justify-between gap-2" v-if="lastResult?.feature_age_seconds != null">
                <span class="text-neutral-400">피처 연령</span>
                <span class="font-mono" :class="(lastResult?.feature_age_seconds ?? 0) > 180 ? 'text-amber-300' : ''">{{ fmtAge(lastResult?.feature_age_seconds) }}</span>
              </div>
              <div v-if="lastResult?.hint" class="text-[11px] text-amber-300 bg-amber-500/10 border border-amber-500/30 px-2 py-1 rounded">
                {{ lastResult.hint }}
              </div>
              <div v-if="lastResult?.label_params" class="text-[11px] text-neutral-300 bg-neutral-800/60 border border-neutral-700 px-2 py-1 rounded break-words">
                <span class="text-neutral-400 mr-2">label params</span>
                <span class="mr-2">lookahead={{ lastResult.label_params.lookahead }}</span>
                <span class="mr-2">drawdown={{ lastResult.label_params.drawdown }}</span>
                <span class="mr-2">rebound={{ lastResult.label_params.rebound }}</span>
              </div>
              <!-- Actionable remediation when data is missing/insufficient -->
              <div
                v-if="['no_data','insufficient_features','insufficient_data'].includes(String(lastResult?.status||'').toLowerCase())"
                class="mt-2 p-2 rounded border border-amber-500/30 bg-amber-500/5 text-[12px] space-y-2"
              >
                <div class="text-amber-200/90">
                  입력 데이터가 부족합니다. 아래 도구로 즉시 점검/생성을 시도할 수 있습니다.
                </div>
                <div class="flex flex-wrap gap-2">
                  <button class="btn btn-sm" @click="checkFeatureStatus" :disabled="opLoading">
                    피처 상태 확인
                  </button>
                  <button class="btn btn-sm" @click="computeNow" :disabled="opLoading">
                    피처 즉시 생성 (compute-now)
                  </button>
                </div>
                <div v-if="opMessage" class="text-[11px] text-neutral-300">
                  {{ opMessage }}
                </div>
                <details v-if="Array.isArray(lastResult?.next_steps) && lastResult.next_steps.length" class="text-[11px] opacity-80">
                  <summary class="cursor-pointer select-none">백엔드 제안된 다음 단계 보기</summary>
                  <ul class="list-disc ml-5 mt-1">
                    <li v-for="(s,idx) in lastResult.next_steps" :key="idx" class="font-mono">{{ s }}</li>
                  </ul>
                </details>
              </div>
              <!-- direction forecast removed (bottom-only) -->
            </div>
          </div>
        </div>
        <div class="md:col-span-3 min-w-0">
          <h2 class="text-sm font-semibold mb-2">히스토리 ({{ history.length }})</h2>
          <!-- Limit visible rows to ~20 with vertical scroll -->
          <div class="overflow-auto max-h-[420px] -mx-3 md:mx-0 px-3">
            <table class="min-w-full text-[10px] md:text-xs">
              <thead class="text-neutral-400 border-b border-neutral-700/60">
                <tr>
                  <th class="py-1 pr-3 text-left">시간</th>
                  <th class="py-1 pr-3 text-left">확률</th>
                  <th class="py-1 pr-3 text-left">결정</th>
                  <th class="py-1 pr-3 text-left">임계값</th>
                </tr>
              </thead>
              <tbody>
                <template v-if="hasModel">
                  <tr v-for="h in history" :key="h.ts" class="border-b border-neutral-800/40 hover:bg-neutral-800/30">
                    <td class="py-1 pr-3 font-mono">{{ timeFmt(h.ts) }}</td>
                    <td class="py-1 pr-3 font-mono" :title="h.probability==null ? '' : String(h.probability)">{{ formatProb(h.probability, 3) }}</td>
                    <td class="py-1 pr-3" :class="decisionColor(h.decision)">{{ decisionText(h.decision) }}</td>
                    <td class="py-1 pr-3 font-mono">{{ h.threshold.toFixed(2) }}</td>
                  </tr>
                  <tr v-if="history.length === 0">
                    <td colspan="4" class="py-3 text-center text-neutral-500">데이터 없음</td>
                  </tr>
                </template>
                <template v-else>
                  <tr>
                    <td colspan="4" class="py-3 text-center text-neutral-500">모델이 없습니다. 모델이 준비되면 히스토리가 기록됩니다.</td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
          <details class="mt-4 bg-neutral-800/40 border border-neutral-700 rounded">
            <summary class="cursor-pointer select-none px-3 py-2 text-sm text-neutral-300">고급 옵션 · 모델 선택</summary>
            <div class="p-3 space-y-3 text-xs">
              <div class="flex items-center gap-3 flex-wrap">
                <label class="flex items-center gap-1">
                  <input type="radio" name="selMode" value="production" :checked="selectionMode==='production'" @change="() => setSelectionModeLocal('production')"> 프로덕션
                </label>
                <label class="flex items-center gap-1">
                  <input type="radio" name="selMode" value="latest" :checked="selectionMode==='latest'" @change="() => setSelectionModeLocal('latest')"> 최신
                </label>
                <label class="flex items-center gap-1">
                  <input type="radio" name="selMode" value="specific" :checked="selectionMode==='specific'" @change="() => setSelectionModeLocal('specific')"> 특정 버전
                </label>
              </div>
              <div class="flex items-center gap-2" v-if="selectionMode==='specific'">
                <input class="input w-64" placeholder="예: 1760019360959-19f29f" v-model="forcedVersion" @change="() => setForcedVersionLocal(forcedVersion || '')" />
                <button class="btn btn-sm" :disabled="loading || !forcedVersion" @click="runOnce">이 버전으로 실행</button>
              </div>
              <div class="flex items-center gap-2 flex-wrap" v-if="selectionMode==='specific' && recentVersions.length>0">
                <div class="text-neutral-400">또는 최근 버전 선택:</div>
                <select class="input" :disabled="recentLoading" @change="onSelectRecentVersion">
                  <option value="" selected>선택…</option>
                  <option v-for="rv in recentVersions" :key="rv.version" :value="rv.version" :title="versionTooltip(rv)">
                    {{ rv.version }}{{ rv.version===prodVersion ? ' (prod)' : '' }}
                  </option>
                </select>
                <span v-if="prodVersion" class="text-[10px] px-2 py-0.5 rounded border border-neutral-700 bg-neutral-800/40 text-neutral-300">프로덕션: {{ prodVersion }}</span>
              </div>
              <div class="text-[11px] text-neutral-400">
                우선순위: version 지정 » use=latest » 프로덕션 우선
              </div>
              <!-- Quick feature recompute for current context -->
              <div class="mt-3 p-2 border border-neutral-700 rounded bg-neutral-800/40 space-y-2">
                <div class="text-neutral-300 text-[12px]">해당 심볼/인터벌 최근 N개 스냅샷 재계산</div>
                <div class="flex items-center gap-2 text-[12px]">
                  <label class="flex items-center gap-1">
                    N
                    <input class="input w-20" type="number" min="50" max="2000" v-model.number="recomputeN" />
                  </label>
                  <button class="btn btn-sm" :disabled="opLoading" @click="recomputeRecent">
                    재계산 실행
                  </button>
                </div>
                <div v-if="recomputeMsg" class="text-[11px] text-neutral-400">{{ recomputeMsg }}</div>
              </div>

              <!-- Quick orchestration helpers for dev: quick-validate and bootstrap -->
              <div class="mt-3 grid md:grid-cols-2 gap-3">
                <div class="p-2 border border-neutral-700 rounded bg-neutral-800/40 space-y-2">
                  <div class="text-neutral-300 text-[12px]">빠른 점검 (quick-validate)</div>
                  <div class="text-[11px] text-neutral-400">dev에서 데이터/큐/라벨러/보정 상태를 한 번에 점검합니다.</div>
                  <div class="flex items-center gap-2">
                    <button class="btn btn-sm" :disabled="qvLoading" @click="runQuickValidate">실행</button>
                    <span v-if="qvLoading" class="text-[11px] text-neutral-400">실행 중…</span>
                  </div>
                  <div class="mt-1 flex items-center gap-2">
                    <button class="btn btn-xs" :disabled="diagRunning" @click="autoDiagnose">자동 점검</button>
                    <span v-if="diagRunning" class="text-[11px] text-neutral-400">체크 중…</span>
                  </div>
                  <div v-if="qvMsg" class="text-[11px] text-neutral-300 break-words">{{ qvMsg }}</div>
                  <div v-if="diagMsg" class="text-[11px] text-neutral-400 break-words">{{ diagMsg }}</div>
                  <div v-if="qvBadges.visible" class="flex flex-wrap items-center gap-2 text-[10px] mt-1">
                    <span class="px-2 py-0.5 rounded border border-neutral-700 bg-neutral-900/60" :title="qvBadges.prodTip">prod_ece: <span :class="qvBadges.prodClass">{{ qvBadges.prodText }}</span></span>
                    <span class="px-2 py-0.5 rounded border border-neutral-700 bg-neutral-900/60" :title="qvBadges.gapTip">{{ qvBadges.gapLabel }}: <span :class="qvBadges.gapClass">{{ qvBadges.gapText }}</span></span>
                  </div>
                  <!-- Labeler helper: populate realized labels for live ECE -->
                  <div class="mt-2 p-2 rounded border border-neutral-700 bg-neutral-900/40 space-y-2">
                    <div class="text-[11px] text-neutral-300">
                      라이브 ECE 계산에는 최근 창에 <b>실현 라벨</b>이 필요합니다. 부족할 경우 라벨러를 즉시 실행하세요.
                    </div>
                    <div class="flex flex-wrap items-center gap-2 text-[10px]">
                      <span class="px-2 py-0.5 rounded border border-neutral-700 bg-neutral-950/60" :class="labelerStatus?.enabled ? 'text-emerald-300' : 'text-neutral-400'" title="env: AUTO_LABELER_ENABLED">
                        enabled: {{ labelerStatus?.enabled ? 'on' : 'off' }}
                      </span>
                      <span class="px-2 py-0.5 rounded border border-neutral-700 bg-neutral-950/60" :class="labelerStatus?.running ? 'text-emerald-300' : 'text-neutral-400'">
                        running: {{ labelerStatus?.running ? 'yes' : 'no' }}
                      </span>
                      <span class="px-2 py-0.5 rounded border border-neutral-700 bg-neutral-950/60 text-neutral-300" :title="labelerStatus?.last_success_ts ? new Date(labelerStatus.last_success_ts*1000).toLocaleString() : ''">
                        last_success: {{ labelerStatus?.last_success_ts ? timeFmt(labelerStatus.last_success_ts*1000) : '—' }}
                      </span>
                      <span class="px-2 py-0.5 rounded border border-neutral-700 bg-neutral-950/60 text-neutral-300">
                        backlog≈ {{ labelerStatus?.approx_backlog ?? '—' }}
                      </span>
                      <button class="btn btn-xxs" :disabled="labelerStatusLoading" @click="loadLabelerStatus">{{ labelerStatusLoading ? '조회중…' : '상태 새로고침' }}</button>
                    </div>
                    <div class="flex items-center gap-2 text-[11px] flex-wrap">
                      <label class="flex items-center gap-1" title="라벨 확정까지 최소 경과 시간(초)">
                        min_age(s)
                        <input class="input w-24" type="number" min="0" step="10" v-model.number="labelerMinAge" />
                      </label>
                      <label class="flex items-center gap-1" title="한 번에 처리할 최대 건수">
                        limit
                        <input class="input w-24" type="number" min="1" step="50" v-model.number="labelerBatch" />
                      </label>
                      <button class="btn btn-sm" :disabled="labelerRunning" @click="() => runLabelerNow()">라벨러 실행</button>
                      <span v-if="labelerRunning" class="text-neutral-400">실행 중…</span>
                    </div>
                    <div v-if="labelerMsg" class="text-[11px] text-neutral-400">{{ labelerMsg }}</div>
                  </div>
                </div>
                <div class="p-2 border border-neutral-700 rounded bg-neutral-800/40 space-y-2">
                  <div class="text-neutral-300 text-[12px]">원클릭 부트스트랩</div>
                  <div class="text-[11px] text-neutral-400">최초 모델 생성을 위한 안전한 기본 절차를 실행합니다.</div>
                  <div class="flex items-center gap-2">
                    <button class="btn btn-sm" :disabled="bsLoading" @click="runBootstrap">부트스트랩</button>
                    <span v-if="bsLoading" class="text-[11px] text-neutral-400">실행 중…</span>
                  </div>
                  <div v-if="bsMsg" class="text-[11px] text-neutral-300 break-words">{{ bsMsg }}</div>
                </div>
              </div>
            </div>
          </details>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, onBeforeUnmount, onActivated, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useInferenceStore } from '../stores/inference';
import { useOhlcvStore } from '../stores/ohlcv';
import { defineAsyncComponent } from 'vue';
const BackendStatusBadge = defineAsyncComponent(() => import('../components/BackendStatusBadge.vue'));
import { useBackendStatus } from '../composables/useBackendStatus';

const store = useInferenceStore();
// reactive refs
const { selectedTarget, selectionMode, forcedVersion, lastResult, history, loading, error, lastRequestId, auto } = storeToRefs(store);
// methods (not refs)
const { runOnce, setSelectionModeLocal, setForcedVersionLocal, decisionColor } = store;

// Context from OHLCV
const ohlcv = useOhlcvStore();
const ohlcvSymbol = computed(() => ohlcv.symbol);
const ohlcvInterval = computed(() => ohlcv.interval);
const staleBadge = computed(() => lastResult.value?.feature_stale === true || (lastResult.value?.feature_age_seconds ?? 0) > 180);
const staleTitle = computed(() => `feature_age_seconds=${lastResult.value?.feature_age_seconds ?? 'N/A'}`);
const hasModel = computed(() => Boolean(lastResult.value?.used_production || lastResult.value?.model_version));

// Seed fallback and auto inference loop status
const seedState = ref<{active:boolean; duration_seconds:number; last_exit_ts?:number; started_at?:number}>({active:false, duration_seconds:0});
const autoLoop = ref<{enabled:boolean; interval:number|null; decisions1m:number; decisions5m:number; disable_reason?:string}>({enabled:false, interval:null, decisions1m:0, decisions5m:0});
const seedTooltip = computed(() => {
  const s = seedState.value;
  const secs = Math.floor(s.duration_seconds || 0);
  const lastExit = s.last_exit_ts ? new Date(s.last_exit_ts*1000).toLocaleString() : 'N/A';
  const started = s.started_at ? new Date(s.started_at*1000).toLocaleString() : 'N/A';
  return `Seed fallback active ${secs}s\nStarted: ${started}\nLast exit: ${lastExit}`;
});
const autoLoopTooltip = computed(() => {
  const a = autoLoop.value;
  if (!a.enabled) return `Auto loop disabled${a.disable_reason? ' ('+a.disable_reason+')' : ''}`;
  return `Decisions: 1m=${a.decisions1m}, 5m=${a.decisions5m}`;
});
let statusTimer: any = null;
// --- Quick-validate badge thresholds from env with safe defaults ---
// Access Vite env at runtime without tripping TS parser in some setups
function __safeEnv(): any {
  try {
    return (new Function('return import.meta.env'))();
  } catch {
    return {};
  }
}
const __env: any = __safeEnv();
function numEnv(key: string, fallback: number): number {
  const v = Number(__env?.[key]);
  return Number.isFinite(v) ? v : fallback;
}
// ECE thresholds: green <= ECE_GREEN, amber <= ECE_AMBER, else red
const ECE_GREEN = numEnv('VITE_QV_ECE_GREEN', 0.08);
const ECE_AMBER = numEnv('VITE_QV_ECE_AMBER', 0.12);
// Gap thresholds (relative preferred): green <= GAP_REL_GREEN, amber <= GAP_REL_AMBER, else red
const GAP_REL_GREEN = numEnv('VITE_QV_GAP_REL_GREEN', 0.05);
const GAP_REL_AMBER = numEnv('VITE_QV_GAP_REL_AMBER', 0.10);

// --- Recent model versions (for Advanced selection) ---
type RecentVersion = { id?: number; version: string; status?: string; created_at?: string; promoted_at?: string };
const recentVersions = ref<RecentVersion[]>([]);
const prodVersion = ref<string | null>(null);
const recentLoading = ref(false);
const recentError = ref<string | null>(null);
// Bottom-only model name
const MODEL_NAME = 'bottom_predictor';
async function loadRecentVersions(limit: number = 8) {
  recentError.value = null;
  recentLoading.value = true;
  try {
    const http = (await import('../lib/http')).default;
    const params = { name: MODEL_NAME, model_type: 'supervised', limit } as any;
    const r = await http.get('/api/models/latest', { params });
    const arr = Array.isArray(r.data) ? r.data : [];
    recentVersions.value = arr.map((it: any) => ({ id: it.id, version: String(it.version), status: it.status, created_at: it.created_at, promoted_at: it.promoted_at }));
    const prod = arr.find((it: any) => it.status === 'production');
    prodVersion.value = prod ? String(prod.version) : null;
  } catch (e: any) {
    recentError.value = e?.message || '최근 버전 조회 실패';
  } finally {
    recentLoading.value = false;
  }
}
async function pollStatuses(){
  try {
    const seedRes = await import('../lib/http').then(m => m.default.get('/api/inference/seed/status'));
    seedState.value = seedRes.data;
  } catch {}
  try {
    const actRes = await import('../lib/http').then(m => m.default.get('/api/inference/activity/summary'));
    const d = actRes.data || {};
    autoLoop.value = {
      enabled: !!d.auto_loop_enabled,
      interval: d.interval ?? null,
      decisions1m: d.decisions_1m ?? 0,
      decisions5m: d.decisions_5m ?? 0,
      disable_reason: d.disable_reason,
    };
  } catch {}
}

function formatProb(prob: number | null | undefined, fixed: number = 4): string {
  if (prob == null) return '—';
  const p = Number(prob);
  if (!isFinite(p)) return '—';
  if (p === 0) return '0';
  // Use fixed decimals for values >= 1e-3, otherwise scientific notation
  return p >= 1e-3 ? p.toFixed(fixed) : p.toExponential(2);
}

const probDisplay = computed(() => {
  const p = lastResult.value?.probability;
  return formatProb(typeof p === 'number' ? p : null, 4);
});
const probLabel = computed(() => selectedTarget.value === 'bottom' ? '저점 확률' : '예측 확률');
const probBarWidth = computed(() => {
  const p = lastResult.value?.probability;
  return typeof p === 'number' && p >= 0 && p <= 1 ? (p * 100).toFixed(1) + '%' : '0%';
});
// Visual threshold position for the bar (yellow marker)
const thresholdMarkerPct = computed(() => {
  const t = typeof lastResult.value?.threshold === 'number' ? lastResult.value!.threshold : effectiveThreshold.value;
  return typeof t === 'number' && t > 0 && t < 1 ? (t * 100).toFixed(1) + '%' : '';
});
const decisionDisplay = computed(() => {
  const d = lastResult.value?.decision;
  if (selectedTarget.value === 'bottom') return d === 1 ? 'TRIGGER' : 'NO TRIGGER';
  return d === 1 ? '+1' : d === -1 ? '-1' : '—';
});
// const thresholdDisplay = computed(() => threshold.value.toFixed(2)); // not used in UI
// Applied threshold shown in UI: prefer value returned by predict API; fallback to diagnostics effective
const effectiveThreshold = ref<number | null>(null);
const thresholdDisplay = computed(() => {
  const t = typeof lastResult.value?.threshold === 'number' ? lastResult.value!.threshold : effectiveThreshold.value;
  return typeof t === 'number' && isFinite(t) ? t.toFixed(2) : '—';
});

function decisionText(d: number | null) {
  if (selectedTarget.value === 'bottom') return d === 1 ? 'TRIGGER' : 'NO TRIGGER';
  return d === 1 ? '+1' : d === -1 ? '-1' : '—';
}
function timeFmt(ts: number) { return new Date(ts).toLocaleTimeString(); }
function fmtMs(ms: any) {
  const n = typeof ms === 'number' ? ms : Number(ms);
  if (!isFinite(n) || n <= 0) return '—';
  try { return new Date(n).toLocaleString(); } catch { return String(ms); }
}
function fmtAge(sec: any) {
  const s = typeof sec === 'number' ? sec : Number(sec);
  if (!isFinite(s) || s < 0) return '—';
  if (s < 60) return s.toFixed(0) + 's';
  if (s < 3600) return Math.floor(s/60) + 'm ' + Math.floor(s%60) + 's';
  const h = Math.floor(s/3600);
  const m = Math.floor((s%3600)/60);
  return h + 'h ' + m + 'm';
}

// --- Remediation actions for no_data/insufficient_features ---
const opLoading = ref(false);
const opMessage = ref('');
const recomputeN = ref<number>(600);
const recomputeMsg = ref<string>('');
async function checkFeatureStatus(){
  opMessage.value = '';
  opLoading.value = true;
  try {
    const http = (await import('../lib/http')).default;
    const params = { symbol: ohlcvSymbol.value, interval: ohlcvInterval.value } as any;
    const r = await http.get('/admin/features/status', { params });
    const data = r.data || {};
    const latest = data.latest_snapshot ? ` latest.close=${data.latest_snapshot?.close_time ?? '—'}` : '';
    opMessage.value = `상태: ok symbol=${data.symbol} interval=${data.interval} lag=${Math.round(data.lag_seconds ?? 0)}s${latest}`;
  } catch (e: any) {
    opMessage.value = `상태 조회 실패: ${e?.message || String(e)}`;
  } finally {
    opLoading.value = false;
  }
}

// --- Quick orchestration helpers ---
const qvLoading = ref(false);
const qvMsg = ref('');
const qvBadges = ref<{visible:boolean; prodText:string; prodClass:string; prodTip:string; gapText:string; gapClass:string; gapLabel:string; gapTip:string}>({visible:false, prodText:'', prodClass:'', prodTip:'', gapText:'', gapClass:'', gapLabel:'gap', gapTip:''});
async function runQuickValidate() {
  qvMsg.value = '';
  qvLoading.value = true;
  qvBadges.value = { visible:false, prodText:'', prodClass:'', prodTip:'', gapText:'', gapClass:'', gapLabel:'gap', gapTip:'' };
  try {
    const http = (await import('../lib/http')).default;
    const params: any = {
      compact: true,
      count: 5,
      // health guardrails with safe defaults; backend has defaults too
      ece_target: 0.08,
      min_samples: 200,
    };
    const r = await http.post('/admin/inference/quick-validate', undefined, { params });
    const d = r.data || {};
    // Prefer compact summary if present
    const code = d?.result_code || d?.status || 'ok';
    const prodEceNum = typeof d?.production_ece === 'number' ? d.production_ece : null;
    const prodEce = prodEceNum != null ? prodEceNum.toFixed(3) : d?.production_ece;
    const hasRel = typeof d?.ece_gap_to_prod_rel === 'number';
    const hasAbs = typeof d?.ece_gap_to_prod === 'number';
    const liveGapNum = hasRel ? d.ece_gap_to_prod_rel : (hasAbs ? d.ece_gap_to_prod : null);
    const liveGap = liveGapNum != null ? liveGapNum.toFixed(3) : (d?.ece_gap_to_prod_rel ?? d?.ece_gap_to_prod);
    const hint = d?.hint || d?.hints?.[0];
    qvMsg.value = [`결과: ${code}`, prodEce!=null?`prod_ece=${prodEce}`:'', liveGap!=null?`gap=${liveGap}`:'', hint?`hint=${hint}`:'']
      .filter(Boolean).join(' | ');
    // color badges
    const prodClass = prodEceNum==null ? 'text-neutral-300' : (prodEceNum<=ECE_GREEN ? 'text-emerald-300' : (prodEceNum<=ECE_AMBER ? 'text-amber-300' : 'text-rose-300'));
    const gapClass = liveGapNum==null ? 'text-neutral-300' : (liveGapNum<=GAP_REL_GREEN ? 'text-emerald-300' : (liveGapNum<=GAP_REL_AMBER ? 'text-amber-300' : 'text-rose-300'));
    const gapLabel = hasRel ? 'gap_rel' : (hasAbs ? 'gap_abs' : 'gap');
    const prodTip = `프로덕션 ECE (낮을수록 좋음). green ≤ ${ECE_GREEN}, amber ≤ ${ECE_AMBER}`;
    const gapTip = hasRel
      ? `라이브 vs 프로덕션 ECE 상대 격차 (낮을수록 좋음). green ≤ ${GAP_REL_GREEN}, amber ≤ ${GAP_REL_AMBER}`
      : `라이브 vs 프로덕션 ECE 절대 격차 (낮을수록 좋음). 상대값이 없을 경우 절대값 표시`;
    qvBadges.value = { visible: !!(prodEce!=null || liveGap!=null), prodText: prodEce!=null?String(prodEce):'—', prodClass, prodTip, gapText: liveGap!=null?String(liveGap):'—', gapClass, gapLabel, gapTip };
  } catch (e: any) {
    qvMsg.value = `quick-validate 실패: ${e?.__friendlyMessage || e?.message || String(e)}`;
  } finally {
    qvLoading.value = false;
  }
}

const bsLoading = ref(false);
const bsMsg = ref('');
async function runBootstrap(){
  bsMsg.value = '';
  bsLoading.value = true;
  try {
    const http = (await import('../lib/http')).default;
    // safe, minimal bootstrap suitable for dev
    const payload = {
      backfill_year: false,
      fill_gaps: true,
      feature_target: 600,
      train_sentiment: false,
      min_auc: 0.60,
      max_ece: 0.08,
      dry_run: false,
      retry_fill_gaps: true,
      skip_promotion: false,
      skip_features: false,
    } as const;
    const r = await http.post('/admin/bootstrap', payload);
    const status = r.data?.status || 'ok';
    const notes = Array.isArray(r.data?.notes) ? r.data.notes.join(',') : '';
    bsMsg.value = `bootstrap: ${status}${notes?` (${notes})`:''}`;
  } catch (e: any) {
    bsMsg.value = `bootstrap 실패: ${e?.__friendlyMessage || e?.message || 'error'}`;
  } finally {
    bsLoading.value = false;
  }
}

// --- Labeler helper controls (to populate realized labels for live ECE) ---
const labelerMinAge = ref<number>(120);
const labelerBatch = ref<number>(1000);
const labelerRunning = ref(false);
const labelerMsg = ref('');
async function runLabelerNow(skipAutoRevalidate: boolean = false){
  labelerMsg.value = '';
  labelerRunning.value = true;
  try {
    const http = (await import('../lib/http')).default;
    const params = new URLSearchParams({
      force: 'true',
      min_age_seconds: String(Math.max(0, labelerMinAge.value||0)),
      limit: String(Math.max(1, labelerBatch.value||1)),
    });
    const r = await http.post('/api/inference/labeler/run?' + params.toString());
    const status = r.data?.status || 'ok';
    labelerMsg.value = `labeler: ${status}`;
    // Optionally re-run quick-validate after a short delay to reflect new labels
    if (!skipAutoRevalidate) setTimeout(() => { runQuickValidate(); }, 1500);
  } catch (e:any) {
    labelerMsg.value = e?.__friendlyMessage || e?.message || 'labeler error';
  } finally {
    labelerRunning.value = false;
  }
}

// --- Auto diagnose: quick-validate → (if needed) run labeler → quick-validate ---
const diagRunning = ref(false);
const diagMsg = ref('');
function sleep(ms: number) { return new Promise(res => setTimeout(res, ms)); }
async function autoDiagnose(){
  if (diagRunning.value) return;
  diagRunning.value = true; diagMsg.value = '';
  try {
    await runQuickValidate();
    const msg = String(qvMsg.value || '').toLowerCase();
    const missing = !qvBadges.value.visible || msg.includes('no_data') || msg.includes('no data') || msg.includes('label');
    if (missing) {
      await runLabelerNow(true);
      await sleep(2000);
      await runQuickValidate();
      diagMsg.value = `자동 점검 완료 (라벨러 실행 포함): ${qvMsg.value || ''}`;
    } else {
      diagMsg.value = `빠른 점검 완료: ${qvMsg.value || ''}`;
    }
  } catch (e:any) {
    diagMsg.value = `자동 점검 실패: ${e?.__friendlyMessage || e?.message || 'error'}`;
  } finally {
    diagRunning.value = false;
  }
}

// Recompute N recent feature snapshots for current context (uses features/backfill API with target=N)
async function recomputeRecent(){
  recomputeMsg.value = '';
  opLoading.value = true;
  try {
    const http = (await import('../lib/http')).default;
    const params: any = { target: Math.max(50, Math.min(Number(recomputeN.value) || 600, 2000)), symbol: ohlcvSymbol.value, interval: ohlcvInterval.value };
    const r = await http.post('/admin/features/backfill', null, { params });
    recomputeMsg.value = `재계산 요청 완료: ${r.data?.status || 'ok'} (inserted=${r.data?.inserted ?? '?'})`;
    // Optionally re-run inference to reflect updates
    await runOnce();
  } catch (e: any) {
    recomputeMsg.value = `재계산 실패: ${e?.message || 'error'}`;
  } finally {
    opLoading.value = false;
  }
}
async function computeNow(){
  opMessage.value = '';
  opLoading.value = true;
  try {
    const http = (await import('../lib/http')).default;
    const params = { symbol: ohlcvSymbol.value, interval: ohlcvInterval.value } as any;
    const r = await http.post('/admin/features/compute-now', null, { params });
    const res = r.data?.result || r.data || {};
    opMessage.value = `즉시 생성 완료: ${res.status || 'ok'}`;
    // re-run inference to reflect new snapshot
    await runOnce();
  } catch (e: any) {
    opMessage.value = `즉시 생성 실패: ${e?.message || String(e)}`;
  } finally {
    opLoading.value = false;
  }
}

onMounted(() => {
  // Enforce DB-only threshold: clear any old client override and ensure we never send a client threshold.
  try { localStorage.removeItem('inference_use_client_threshold'); } catch {}
  try { store.setUseClientThreshold(false); } catch {}
  // Ensure interval has a sensible default to align backend scoping
  try { if (!ohlcv.interval) ohlcv.initDefaults(ohlcv.symbol, '1m'); } catch {}
  // Respect persisted auto setting (store already loads it). Do not force-enable.
  if (auto.value) {
    // Start auto loop without toggling state if already true
    store.startAuto();
  } else {
    // Run once so user sees an initial result
    if (!loading.value) runOnce();
  }
  pollStatuses();
  statusTimer = setInterval(pollStatuses, 15000);
  // Preload recent versions
  loadRecentVersions();
  // initial labeler status fetch
  loadLabelerStatus();
  // fetch current effective threshold for display fallback
  loadEffectiveThreshold();
});

onActivated(() => {
  // Refresh statuses and, if auto is on, ensure loop is running
  pollStatuses();
  if (auto.value) store.startAuto();
});

onBeforeUnmount(() => { if (statusTimer) clearInterval(statusTimer); statusTimer = null; });

// interval selector moved to Admin

// bottom-only; no target change handler

// Keep forcedVersion in sync if it no longer exists in recent list
watch(recentVersions, () => {
  // no-op safety; if user had a forced version not in the list that's okay, we won't clear it automatically
});

function onSelectRecentVersion(e: Event) {
  const el = e.target as HTMLSelectElement;
  const v = el?.value || '';
  setSelectionModeLocal('specific');
  if (v) setForcedVersionLocal(v);
}

function fmtDateStr(input?: string | number | null): string {
  if (!input) return '-';
  try {
    const d = new Date(input as any);
    if (isNaN(d.getTime())) return String(input);
    return d.toLocaleString();
  } catch {
    return String(input);
  }
}

function versionTooltip(rv: { created_at?: string; promoted_at?: string; status?: string }): string {
  const parts: string[] = [];
  if (rv.status) parts.push(`status=${rv.status}`);
  if (rv.created_at) parts.push(`created=${fmtDateStr(rv.created_at)}`);
  if (rv.promoted_at) parts.push(`promoted=${fmtDateStr(rv.promoted_at)}`);
  return parts.join(' \n ');
}

// --- Inline error helper using backend status ---
const { serverOk, readyStatus, reasons, authOk, start: startStatus } = useBackendStatus(15000);
startStatus();
const errorHelp = computed(() => {
  if (authOk.value === false) return '인증 필요: 좌측 사이드바의 API Key를 확인하세요.';
  if (serverOk.value === false) return '백엔드에 연결할 수 없습니다. VITE_BACKEND_URL/VITE_WS_BASE 설정과 서버 상태를 확인하세요.';
  if (readyStatus.value === 'not_ready') {
    const rs = Array.isArray(reasons.value) && reasons.value.length ? ` (사유: ${reasons.value.join(',')})` : '';
    return `백엔드가 준비되지 않았습니다${rs}`;
  }
  return '';
});

function copyReqId(id?: string | null) {
  const v = id ? String(id) : '';
  if (!v) return;
  try {
    if (navigator?.clipboard?.writeText) navigator.clipboard.writeText(v);
  } catch {}
}

function copyLastResult() {
  try {
    const data = lastResult?.value ? JSON.stringify(lastResult.value, null, 2) : '';
    if (!data) return;
    if (navigator?.clipboard?.writeText) navigator.clipboard.writeText(data);
  } catch {}
}

// --- Effective threshold fetch for fallback display ---
async function loadEffectiveThreshold(){
  try {
    const http = (await import('../lib/http')).default;
    // Prefer public API (non-admin) when available
    try {
      const r1 = await http.get('/api/inference/effective_threshold');
      const t1 = r1.data?.threshold;
      if (typeof t1 === 'number' && isFinite(t1)) { effectiveThreshold.value = t1; return; }
    } catch { /* fall back to admin endpoint */ }
    // Fallback to admin diagnostics (requires admin auth in some envs)
    try {
      const r2 = await http.get('/admin/inference/settings');
      const e = r2.data?.threshold?.effective_default;
      if (typeof e === 'number' && isFinite(e)) { effectiveThreshold.value = e; return; }
    } catch {}
  } catch { /* ignore */ }
}

// --- Labeler status (observability) ---
const labelerStatusLoading = ref(false);
const labelerStatus = ref<{
  enabled: boolean;
  running: boolean;
  last_success_ts?: number | null;
  last_error_ts?: number | null;
  approx_backlog?: number | null;
  interval?: number;
  min_age_seconds?: number;
  batch_limit?: number;
} | null>(null);
async function loadLabelerStatus() {
  if (labelerStatusLoading.value) return;
  labelerStatusLoading.value = true;
  try {
    const http = (await import('../lib/http')).default;
    const r = await http.get('/admin/inference/labeler/status');
    const d = r.data || {};
    labelerStatus.value = {
      enabled: !!d.enabled,
      running: !!d.running,
      last_success_ts: typeof d.last_success_ts === 'number' ? d.last_success_ts : null,
      last_error_ts: typeof d.last_error_ts === 'number' ? d.last_error_ts : null,
      approx_backlog: typeof d.approx_backlog === 'number' ? d.approx_backlog : null,
      interval: typeof d.interval === 'number' ? d.interval : undefined,
      min_age_seconds: typeof d.min_age_seconds === 'number' ? d.min_age_seconds : undefined,
      batch_limit: typeof d.batch_limit === 'number' ? d.batch_limit : undefined,
    };
  } catch {
    // swallow; keep previous
  } finally {
    labelerStatusLoading.value = false;
  }
}
</script>

<style scoped>
table { border-collapse: collapse; }
</style>