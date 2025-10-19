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
          <label class="flex items-center gap-1 text-[11px] text-neutral-400">
            <span>인터벌</span>
            <select class="input py-0.5" v-model="ohlcv.interval" @change="onIntervalChange">
              <option v-for="iv in intervals" :key="iv" :value="iv">{{ iv }}</option>
            </select>
          </label>
          <!-- bottom-only; target selector removed -->
          <span v-if="staleBadge" class="text-[10px] px-2 py-0.5 rounded bg-amber-500/20 text-amber-300" :title="staleTitle">입력 STALE</span>
          <span v-if="seedState.active" class="text-[10px] px-2 py-0.5 rounded bg-rose-600/20 text-rose-300" :title="seedTooltip">SEED Fallback</span>
          <span class="text-[10px] px-2 py-0.5 rounded" :class="autoLoop.enabled ? 'bg-emerald-700/20 text-emerald-300' : 'bg-neutral-700/30 text-neutral-300'" :title="autoLoopTooltip">
            Auto Loop: {{ autoLoop.enabled ? (autoLoop.interval + 's') : 'off' }}
          </span>
        </div>
        <div class="grid grid-cols-2 gap-2 md:flex md:items-center md:gap-3 text-xs">
          <button class="btn w-full md:w-auto" :disabled="loading" @click="runOnce">실행</button>
          <label class="flex items-center gap-1 cursor-pointer select-none" :class="loading ? 'opacity-50 cursor-not-allowed' : ''" :title="loading ? '요청 중에는 토글 비활성화' : (auto ? '자동 실행 중' : '자동 실행 시작')">
            <input type="checkbox" :disabled="loading" v-model="auto" @change="toggleAuto()" /> 자동
          </label>
          <div class="flex items-center gap-1 col-span-2 md:col-span-1">
            주기 <input class="w-24 md:w-40" type="range" min="1" max="30" v-model.number="intervalSec" @change="setIntervalSec(intervalSec)" /> <span>{{ intervalSec }}s</span>
          </div>
          <span v-if="error" class="px-2 py-0.5 rounded bg-brand-danger/20 text-brand-danger col-span-2 md:col-span-1">{{ error }}</span>
        </div>
      </div>

      <div class="grid md:grid-cols-5 gap-6">
        <div class="md:col-span-2 space-y-4 min-w-0">
          <div>
            <label class="block text-xs mb-1 text-neutral-400">임계값 {{ thresholdDisplay }}</label>
            <div class="flex items-center gap-2">
              <input type="range" min="0" max="1" step="0.01" v-model.number="threshold" @input="setThreshold(threshold)" class="flex-1 min-w-0" />
              <input type="number" step="0.01" min="0" max="1" v-model.number="threshold" @change="setThreshold(threshold)" class="w-16 md:w-20 shrink-0 bg-neutral-800 border border-neutral-700 rounded px-1 py-0.5 text-xs" />
            </div>
          </div>
          <div class="p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-2">
            <h2 class="text-sm font-semibold">결과</h2>
            <div v-if="!lastResult" class="text-neutral-500 text-xs">아직 결과 없음. 실행 버튼을 눌러 요청하세요.</div>
            <div v-else class="space-y-1 text-sm">
              <div class="flex items-center justify-between gap-2">
                <span class="text-neutral-400">{{ probLabel }}</span>
                <span class="font-mono" :title="typeof lastResult?.probability==='number' ? String(lastResult?.probability) : ''">{{ probDisplay }}</span>
              </div>
              <div class="h-2 w-full rounded bg-neutral-700 overflow-hidden">
                <div class="h-full bg-brand-accent transition-all" :style="{ width: probBarWidth }"></div>
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
              <div class="flex items-center justify-between gap-2" v-if="lastResult?.overridden_threshold">
                <span class="text-neutral-400">적용 임계값</span>
                <span class="font-mono">{{ lastResult?.threshold }}</span>
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
                <tr v-if="hasModel" v-for="h in history" :key="h.ts" class="border-b border-neutral-800/40 hover:bg-neutral-800/30">
                  <td class="py-1 pr-3 font-mono">{{ timeFmt(h.ts) }}</td>
                  <td class="py-1 pr-3 font-mono" :title="h.probability==null ? '' : String(h.probability)">{{ formatProb(h.probability, 3) }}</td>
                  <td class="py-1 pr-3" :class="decisionColor(h.decision)">{{ decisionText(h.decision) }}</td>
                  <td class="py-1 pr-3 font-mono">{{ h.threshold.toFixed(2) }}</td>
                </tr>
                <tr v-if="!hasModel">
                  <td colspan="4" class="py-3 text-center text-neutral-500">모델이 없습니다. 모델이 준비되면 히스토리가 기록됩니다.</td>
                </tr>
                <tr v-else-if="history.length === 0">
                  <td colspan="4" class="py-3 text-center text-neutral-500">데이터 없음</td>
                </tr>
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

const store = useInferenceStore();
// reactive refs
const { threshold, selectedTarget, selectionMode, forcedVersion, lastResult, history, loading, error, auto, intervalSec } = storeToRefs(store);
// methods (not refs)
const { runOnce, toggleAuto, setThreshold, setSelectionModeLocal, setForcedVersionLocal, setIntervalSec, decisionColor } = store;

// Context from OHLCV
const ohlcv = useOhlcvStore();
const ohlcvSymbol = computed(() => ohlcv.symbol);
const ohlcvInterval = computed(() => ohlcv.interval);
const intervals = ['1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d'];
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
const decisionDisplay = computed(() => {
  const d = lastResult.value?.decision;
  if (selectedTarget.value === 'bottom') return d === 1 ? 'TRIGGER' : 'NO TRIGGER';
  return d === 1 ? '+1' : d === -1 ? '-1' : '—';
});
const thresholdDisplay = computed(() => threshold.value.toFixed(2));

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
});

onActivated(() => {
  // Refresh statuses and, if auto is on, ensure loop is running
  pollStatuses();
  if (auto.value) store.startAuto();
});

onBeforeUnmount(() => { if (statusTimer) clearInterval(statusTimer); statusTimer = null; });

function onIntervalChange(){
  // keep it simple: just re-run once to reflect context change
  if (!loading.value) runOnce();
}

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
</script>

<style scoped>
table { border-collapse: collapse; }
</style>