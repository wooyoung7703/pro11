<template>
  <div class="space-y-6">
    <section class="card">
      <div class="flex items-center justify-between mb-2">
        <h1 class="text-xl font-semibold">Feature Drift</h1>
        <div class="flex items-center gap-3 text-xs">
          <label class="flex items-center gap-1">
            Window
            <input class="w-20 bg-neutral-700/50 rounded px-1 py-0.5" type="number" v-model.number="window" min="50" />
          </label>
          <label class="flex items-center gap-1">
            Threshold(|Z|)
            <input class="w-20 bg-neutral-700/50 rounded px-1 py-0.5" type="number" step="0.1" v-model.number="threshold" min="0.1" />
          </label>
          <label class="flex items-center gap-1">
            Auto
            <input type="checkbox" v-model="auto" />
          </label>
          <label v-if="auto" class="flex items-center gap-1">
            Every
            <select v-model.number="intervalSec" class="bg-neutral-700/50 rounded px-1 py-0.5">
              <option :value="30">30s</option>
              <option :value="60">60s</option>
              <option :value="120">120s</option>
              <option :value="300">300s</option>
            </select>
          </label>
          <button class="btn" @click="load" :disabled="loading">스캔</button>
          <button class="px-2 py-0.5 rounded bg-neutral-700 hover:bg-neutral-600" @click="resetDefaults" :disabled="loading">추천값 재설정</button>
        </div>
      </div>
      <p class="text-sm text-neutral-300">선택된 피쳐들의 최근 구간 vs 기준 구간 Z-score 기반 드리프트 지표</p>
      <div class="mt-4 overflow-x-auto">
        <table class="w-full text-sm align-middle">
          <thead class="text-neutral-400 text-left">
            <tr>
              <th class="py-1 pr-4">Feature</th>
              <th class="py-1 pr-4">Z</th>
              <th class="py-1 pr-4">|Z| Bar</th>
              <th class="py-1 pr-4">Baseline μ</th>
              <th class="py-1 pr-4">Recent μ</th>
              <th class="py-1 pr-4">Δμ</th>
              <th class="py-1 pr-4">N(base)</th>
              <th class="py-1 pr-4">N(recent)</th>
              <th class="py-1 pr-4">Status</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.feature" class="border-t border-neutral-700/50 transition" :class="highlightClass(row.feature, row.drift)">
              <td class="py-1 pr-4 font-medium">{{ row.feature }}</td>
              <td class="py-1 pr-4" :class="row.drift ? 'text-brand-danger font-semibold':'text-neutral-200'">{{ fmt(row.z_score) }}</td>
              <td class="py-1 pr-4">
                <div class="h-2 w-40 bg-neutral-700 rounded overflow-hidden relative">
                  <div class="absolute inset-y-0 left-1/2 w-px bg-neutral-500/60"></div>
                  <div
                    class="h-full"
                    :class="row.drift ? 'bg-brand-danger':'bg-brand-accent'"
                    :style="{ width: Math.min(100, Math.abs(row.z_score) / driftThreshold * 100) + '%', transform: row.z_score < 0 ? 'translateX(50%) scaleX(-1)' : 'translateX(0)' }"
                  ></div>
                </div>
              </td>
              <td class="py-1 pr-4">{{ fmt(row.baseline_mean) }}</td>
              <td class="py-1 pr-4">{{ fmt(row.recent_mean) }}</td>
              <td class="py-1 pr-4">{{ fmt(row.recent_mean - row.baseline_mean) }}</td>
              <td class="py-1 pr-4">{{ row.n_baseline }}</td>
              <td class="py-1 pr-4">{{ row.n_recent }}</td>
              <td class="py-1 pr-4">
                <span v-if="row.drift" class="text-xs bg-brand-danger/20 text-brand-danger px-1 rounded">DRIFT</span>
                <span v-else class="text-xs bg-neutral-600/40 text-neutral-300 px-1 rounded">OK</span>
              </td>
            </tr>
            <tr v-if="!loading && rows.length === 0">
              <td colspan="9" class="py-4 text-center text-neutral-400">데이터가 부족하거나 결과가 없습니다.</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="mt-3 text-xs text-neutral-400 flex flex-wrap gap-4 items-center">
        <div>
          임계값(|Z|) ≥ <span class="text-neutral-300">{{ driftThreshold }}</span> ⇒ DRIFT
          <span v-if="thresholdMismatch" class="ml-2 text-[10px] text-amber-400">(요청 {{ safeThresh ?? '-' }}, 적용 {{ driftThreshold }})</span>
        </div>
        <div>
          Window 요청 {{ window }}
          <span v-if="usedBase!=null && usedRecent!=null"> → 사용 base {{ usedBase }}, recent {{ usedRecent }}</span>
        </div>
        <div v-if="summary">Drift: <span :class="summary.drift_count>0?'text-brand-danger':'text-neutral-300'">{{ summary.drift_count }}</span>/<span>{{ summary.total }}</span></div>
        <div v-if="summary && summary.max_abs_z!=null">max|Z|={{ summary.max_abs_z.toFixed(2) }} ({{ summary.top_feature }})</div>
        <div v-if="timestamp">마지막 스캔: {{ new Date(timestamp).toLocaleTimeString() }}</div>
      </div>
    </section>

    <!-- Drift History Panel -->
    <section class="card">
      <div class="flex items-center justify-between mb-2">
        <h2 class="text-lg font-semibold">Drift History</h2>
        <div class="flex items-center gap-2 text-[10px] text-neutral-500">
          <span>최근 {{ historyItems.length }}개 스캔</span>
          <button class="px-2 py-0.5 rounded bg-neutral-700 hover:bg-neutral-600 text-[10px]" @click="fetchHistory" :disabled="historyLoading">갱신</button>
        </div>
      </div>
      <div class="grid lg:grid-cols-3 gap-4">
        <div class="space-y-2 lg:col-span-2">
          <div v-if="historyItems.length === 0" class="text-xs text-neutral-500">히스토리가 없습니다.</div>
          <div v-else class="space-y-2">
            <svg v-if="sparkPath" :viewBox="sparkViewBox" class="w-full h-20 bg-neutral-800/40 rounded">
              <path :d="sparkPath" fill="none" stroke="#60a5fa" stroke-width="1.5" />
            </svg>
            <table class="w-full text-[11px]">
              <thead class="text-neutral-400">
                <tr>
                  <th class="py-1 pr-2 text-left">Time</th>
                  <th class="py-1 pr-2 text-right">Drift</th>
                  <th class="py-1 pr-2 text-right">Total</th>
                  <th class="py-1 pr-2 text-right">max|Z|</th>
                  <th class="py-1 pr-2 text-left">Top Feature</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="h in historyItems.slice().reverse()" :key="h.ts" class="border-t border-neutral-700/40">
                  <td class="py-1 pr-2">{{ formatTime(h.ts) }}</td>
                  <td class="py-1 pr-2 text-right" :class="h.drift_count>0?'text-brand-danger font-medium':'text-neutral-300'">{{ h.drift_count }}</td>
                  <td class="py-1 pr-2 text-right">{{ h.total }}</td>
                  <td class="py-1 pr-2 text-right">{{ h.max_abs_z!=null ? h.max_abs_z.toFixed(2) : '-' }}</td>
                  <td class="py-1 pr-2 text-left truncate max-w-[140px]" :title="h.top_feature || '-'">{{ h.top_feature || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <div class="space-y-2">
          <div class="text-xs text-neutral-400">설명</div>
          <ul class="text-[11px] list-disc ml-4 space-y-1 text-neutral-300">
            <li>sparkline: 최근 스캔별 drift_count (좌→우 시간 진행)</li>
            <li>max|Z|는 각 스캔에서 가장 큰 절대 Z-score</li>
            <li>Top Feature는 그 시점 최대 변동 피쳐</li>
          </ul>
          <div class="text-[10px] text-neutral-500 pt-1">* 메모리 상 최대 200개 유지</div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, computed } from 'vue';
import http from '../lib/http';
import { useToastStore } from '../stores/toast';

interface DriftRow {
  feature: string;
  z_score: number;
  baseline_mean: number;
  recent_mean: number;
  n_baseline: number;
  n_recent: number;
  drift: boolean;
  status: string;
}

// Recommended defaults (env -> fallback)
// 권장 기본값 (백엔드 기본과 일치): window=200, |Z| threshold=3.0
// Access Vite env via any-cast to satisfy TS in Vue SFC (pattern used elsewhere in the project)
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore - Vite provides import.meta.env at build time
const ENV: any = (import.meta as any).env || {};
const ENV_WINDOW = Number(ENV.VITE_DRIFT_WINDOW_DEFAULT ?? '');
const ENV_THRESH = Number(ENV.VITE_DRIFT_Z_THRESHOLD_DEFAULT ?? '');
const DEFAULT_WINDOW = Number.isFinite(ENV_WINDOW) && ENV_WINDOW >= 50 ? ENV_WINDOW : 200;
const DEFAULT_THRESHOLD = Number.isFinite(ENV_THRESH) && ENV_THRESH > 0 ? ENV_THRESH : 3.0;

const window = ref<number>(DEFAULT_WINDOW);
const threshold = ref<number | null>(DEFAULT_THRESHOLD); // 기본 추천값 주입
const features = ref<string[]>(['ret_1','ret_5','ret_10','rsi_14','rolling_vol_20','ma_20','ma_50']);
const rows = ref<DriftRow[]>([]);
const loading = ref(false);
const driftThreshold = ref<number>(DEFAULT_THRESHOLD); // API 응답의 applied threshold로 갱신됨
const timestamp = ref<number | null>(null);
const summary = ref<any | null>(null);
// Drift 자동 스캔 기본값 (env -> fallback)
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore - Vite provides import.meta.env at build time
const ENV_ANY: any = (import.meta as any).env || {};
const DEFAULT_AUTO = String(ENV_ANY.VITE_DRIFT_AUTO_DEFAULT ?? '1').toLowerCase();
const DEFAULT_AUTO_BOOL = DEFAULT_AUTO === '1' || DEFAULT_AUTO === 'true' || DEFAULT_AUTO === 'yes';
const ENV_INTERVAL = Number(ENV_ANY.VITE_DRIFT_AUTO_INTERVAL_SEC ?? '');
const DEFAULT_INTERVAL = Number.isFinite(ENV_INTERVAL) && ENV_INTERVAL >= 10 ? ENV_INTERVAL : 60;

const auto = ref<boolean>(DEFAULT_AUTO_BOOL);
const intervalSec = ref<number>(DEFAULT_INTERVAL);
// 기존 저장값 무시하고 추천 기본값 강제 적용을 위해 키 버전 업그레이드
const PREF_KEY = 'feature_drift_prefs_v2';
let timer: any = null;
const lastDriftSet = new Set<string>();
const enterDriftMap = new Map<string, number>(); // feature -> timestamp when entered drift

// Drift history state
interface DriftHistEntry { ts: number; drift_count: number; total: number; max_abs_z: number|null; top_feature?: string|null; applied_threshold?: number; }
const historyItems = ref<DriftHistEntry[]>([]);
const historyLoading = ref(false);

// Derived helpers for UI consistency/mismatch hints
const usedBase = computed<number | null>(() => {
  if (!rows.value.length) return null;
  let n = Infinity;
  for (const r of rows.value) {
    if (typeof r.n_baseline === 'number' && r.n_baseline > 0) n = Math.min(n, r.n_baseline);
  }
  return n === Infinity ? null : n;
});
const usedRecent = computed<number | null>(() => {
  if (!rows.value.length) return null;
  let n = Infinity;
  for (const r of rows.value) {
    if (typeof r.n_recent === 'number' && r.n_recent > 0) n = Math.min(n, r.n_recent);
  }
  return n === Infinity ? null : n;
});
const safeThresh = computed<number | null>(() => {
  const t = threshold.value;
  return typeof t === 'number' && t > 0 ? t : null;
});
const thresholdMismatch = computed<boolean>(() => {
  const t = safeThresh.value;
  return t != null && Math.abs((t as number) - driftThreshold.value) > 1e-9;
});

async function fetchHistory() {
  try {
    historyLoading.value = true;
    const r = await http.get('/api/features/drift/history', { params: { limit: 50 }});
    if (r.data?.status === 'ok' && Array.isArray(r.data.items)) {
      historyItems.value = r.data.items;
    }
  } catch { /* silent */ }
  finally { historyLoading.value = false; }
}

function formatTime(ts: number) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

// Sparkline computed
const sparkViewBox = '0 0 200 60';
const sparkPath = computed(()=>{
  if (!historyItems.value.length) return '';
  const pts = historyItems.value;
  const max = Math.max(...pts.map(p=>p.drift_count), 1);
  const w = 200; const h = 60;
  return pts.map((p,i)=>{
    const x = (i/(pts.length-1))*w;
    const y = h - (p.drift_count/max)*h;
    return (i===0?`M${x},${y}`:`L${x},${y}`);
  }).join(' ');
});

function fmt(v: any) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  if (typeof v === 'number') return v.toFixed(3);
  return String(v);
}

async function load() {
  try {
    loading.value = true;
    rows.value = [];
    const resp = await http.get('/api/features/drift/scan', {
      params: {
        window: window.value,
        features: features.value.join(','),
        threshold: threshold.value || undefined,
      }
    });
    const data = resp.data;
    if (data.status === 'ok') {
      // applied threshold는 백엔드가 사용한 값(쿼리 파라미터 또는 서버 기본값)으로부터 유도
      const firstKey = features.value[0] ?? Object.keys(data.results || {})[0];
      const applied = firstKey ? data.results?.[firstKey]?.threshold : undefined;
      driftThreshold.value = typeof applied === 'number' && applied > 0 ? applied : DEFAULT_THRESHOLD;
      summary.value = data.summary || null;
      const out: DriftRow[] = [];
      for (const f of Object.keys(data.results || {})) {
        const r = data.results[f];
        if (r.status !== 'ok') continue;
        out.push({
          feature: f,
          z_score: r.z_score,
          baseline_mean: r.baseline_mean,
          recent_mean: r.recent_mean,
          n_baseline: r.n_baseline,
            n_recent: r.n_recent,
          drift: !!r.drift,
          status: r.status,
        });
        const wasDrift = lastDriftSet.has(f);
        const nowDrift = !!r.drift;
        if (!wasDrift && nowDrift) {
          enterDriftMap.set(f, Date.now());
        }
        if (nowDrift) {
          lastDriftSet.add(f);
        } else {
          lastDriftSet.delete(f);
          enterDriftMap.delete(f);
        }
      }
      // sort by absolute z desc
      out.sort((a,b)=>Math.abs(b.z_score)-Math.abs(a.z_score));
      rows.value = out;
      timestamp.value = Date.now();
      // refresh history after successful scan
      fetchHistory();
    }
  } catch (e: any) {
    const toast = useToastStore();
    const friendly = e?.__friendlyMessage || '드리프트 스캔 실패';
    toast.error(friendly, '/api/features/drift/scan');
  } finally {
    loading.value = false;
  }
}

load();

function highlightClass(feature: string, drift: boolean) {
  if (!drift) return '';
  const enteredAt = enterDriftMap.get(feature);
  if (!enteredAt) return '';
  const age = Date.now() - enteredAt;
  if (age < 5000) {
    return age % 1000 < 500 ? 'bg-brand-danger/10' : 'bg-brand-danger/20';
  }
  return '';
}

function startAuto() {
  stopAuto();
  if (auto.value) {
    timer = setInterval(() => {
      load();
    }, Math.max(10, intervalSec.value) * 1000);
  }
}
function stopAuto() { if (timer) clearInterval(timer); timer = null; }

watch([auto, intervalSec], () => {
  startAuto();
});

watch([window, threshold, auto, intervalSec], () => {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify({
      window: window.value,
      threshold: threshold.value,
      auto: auto.value,
      intervalSec: intervalSec.value,
    }));
  } catch { /* ignore quota */ }
});

onMounted(() => {
  // hydrate preferences
  try {
    const raw = localStorage.getItem(PREF_KEY);
    if (raw) {
      const p = JSON.parse(raw);
      if (typeof p.window === 'number' && p.window >= 50) window.value = p.window;
      if (typeof p.threshold === 'number' && p.threshold > 0) threshold.value = p.threshold;
      if (typeof p.auto === 'boolean') auto.value = p.auto;
      if (typeof p.intervalSec === 'number' && p.intervalSec >= 10) intervalSec.value = p.intervalSec;
    }
  } catch { /* ignore */ }
  startAuto();
  fetchHistory();
});

onUnmounted(() => {
  stopAuto();
});

function resetDefaults() {
  try { localStorage.removeItem(PREF_KEY); } catch { /* ignore */ }
  window.value = DEFAULT_WINDOW;
  threshold.value = DEFAULT_THRESHOLD;
  // reset auto scan settings to env-based defaults as well
  auto.value = DEFAULT_AUTO_BOOL;
  intervalSec.value = DEFAULT_INTERVAL;
  // restart auto loop with new interval if needed
  startAuto();
  load();
}
</script>