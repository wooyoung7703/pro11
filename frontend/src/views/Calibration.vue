<!-- eslint-disable vue/multi-word-component-names -->
<template>
  <div class="space-y-6">
  <section class="card space-y-4 leading-tight">
      <div class="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div class="flex items-center gap-2 md:gap-3 flex-wrap">
          <h1 class="text-xl font-semibold">
            캘리브레이션 & 드리프트
          </h1>
          <span class="text-[11px] text-neutral-400">컨텍스트: <span class="font-mono">{{ ohlcvSymbol }}</span> · <span class="font-mono">{{ ohlcvInterval || '—' }}</span></span>
        </div>
        <div class="grid grid-cols-2 gap-2 md:flex md:items-center md:gap-3 text-xs">
          <!-- bottom-only; target selector removed -->
          <div class="flex items-center gap-1">
            인터벌
            <select class="input py-0.5" v-model="ohlcv.interval" @change="onIntervalChange">
              <option v-for="iv in intervals" :key="iv" :value="iv">{{ iv }}</option>
            </select>
          </div>
          <button class="btn w-full md:w-auto" :disabled="loading" @click="fetchAll">새로고침</button>
          <label class="flex items-center gap-1 cursor-pointer select-none">
            <input type="checkbox" v-model="auto" @change="toggleAuto()" /> 자동
          </label>
          <div class="flex items-center gap-1 col-span-2 md:col-span-1">
            주기
            <input class="w-24 md:w-40" type="range" min="5" max="120" v-model.number="intervalSec" @change="setIntervalSec(intervalSec)" />
            <span>{{ intervalSec }}s</span>
          </div>
          <div class="flex items-center gap-1">
            live_window
            <input class="input w-24 py-0.5 shrink-0" type="number" min="60" step="60" v-model.number="liveWindowInput" @change="onLiveWindowChange" />
            <span class="text-neutral-400 text-[10px]">sec</span>
          </div>
          <div class="flex items-center gap-1">
            bins
            <input class="input w-16 py-0.5 shrink-0" type="number" min="5" max="50" step="1" v-model.number="liveBinsInput" @change="onLiveBinsChange" />
          </div>
          <!-- Label count quick badge -->
          <div class="flex items-center gap-1">
            <span class="text-neutral-400">라벨 수</span>
            <span class="px-2 py-0.5 rounded bg-neutral-700/60 font-mono">{{ sampleCountDisplay }}</span>
          </div>
          <!-- Manual labeler run removed (use Admin > Actions or Job Center) -->
          <span v-if="error" class="px-2 py-0.5 rounded bg-brand-danger/20 text-brand-danger">{{ error }}</span>
        </div>
      </div>
  <p class="text-xs text-neutral-400 leading-snug">실시간 추론의 캘리브레이션 상태를 프로덕션 모델과 비교하고, ECE 드리프트 감지 및 재학습 추천 상태를 표시합니다.</p>
      <div v-if="live?.ece == null" class="text-[11px] text-amber-300 bg-amber-500/10 border border-amber-500/30 px-2 py-1 rounded">
        라이브 ECE는 최근 창(window) 내에 <b>실현 라벨(realized)</b>이 있어야 계산됩니다. 자동 라벨러를 활성화하거나(Admin → run labeler), 시간이 지난 뒤 다시 확인하세요.
      </div>

  <div class="grid grid-cols-1 md:grid-cols-3 gap-4 md:gap-6">
        <!-- Live vs Production -->
  <div class="p-3 md:p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3 min-w-0">
          <h2 class="text-sm font-semibold">라이브 vs 프로덕션</h2>
          <div class="space-y-2 text-xs">
            <div class="flex justify-between"><span class="text-neutral-400">프로덕션 ECE</span><span class="font-mono">{{ prod?.ece?.toFixed(4) ?? '—' }}</span></div>
            <div class="flex justify-between"><span class="text-neutral-400">라이브 ECE</span><span class="font-mono" :class="eceClass(live?.ece)">{{ live?.ece?.toFixed(4) ?? '—' }}</span></div>
            <div class="flex justify-between"><span class="text-neutral-400">Δ ECE</span><span class="font-mono" :class="deltaClass(deltaECE)">{{ deltaECEDisplay }}</span></div>
            <div class="flex justify-between"><span class="text-neutral-400">Live Brier</span><span class="font-mono">{{ live?.brier?.toFixed(4) ?? '—' }}</span></div>
            <div class="flex justify-between"><span class="text-neutral-400">Prod Brier</span><span class="font-mono">{{ prod?.brier?.toFixed(4) ?? '—' }}</span></div>
            <div class="pt-2">
              <div class="h-2 w-full rounded bg-neutral-700 overflow-hidden" v-if="live?.ece != null && prod?.ece != null">
                <div class="h-full bg-brand-accent transition-all" :style="{ width: liveECEBar }"></div>
              </div>
              <div class="text-[10px] text-neutral-500 mt-1">막대 = 라이브 ECE (0~0.2 스케일 제한)</div>
            </div>
          </div>
          <div class="mt-3 md:mt-4">
            <ReliabilityChart :bins-live="live?.reliability_bins" :bins-prod="prod?.reliability_bins" :height="180" class="md:!h-[220px]" />
          </div>
        </div>

        <!-- Monitor / Recommendation -->
  <div class="p-3 md:p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3 min-w-0">
          <h2 class="text-sm font-semibold flex items-center gap-2">모니터
            <span v-if="recommendation" class="text-[10px] px-2 py-0.5 rounded bg-amber-500/20 text-amber-300">재학습 추천</span>
          </h2>
          <div class="grid grid-cols-2 gap-2 text-xs">
            <div class="p-2 rounded bg-neutral-700/40">
              <div class="text-neutral-400">절대 편차 연속</div>
              <div class="font-mono">{{ monitor?.abs_streak ?? 0 }}</div>
            </div>
            <div class="p-2 rounded bg-neutral-700/40">
              <div class="text-neutral-400">상대 편차 연속</div>
              <div class="font-mono">{{ monitor?.rel_streak ?? 0 }}</div>
            </div>
            <div class="p-2 rounded bg-neutral-700/40 col-span-2">
              <div class="text-neutral-400 flex justify-between items-center">
                <span>델타 / 절대임계</span>
                <span class="font-mono text-[10px]">{{ monitor?.last_snapshot?.abs_threshold?.toFixed?.(3) ?? '-' }}</span>
              </div>
              <div class="h-2 w-full rounded bg-neutral-700 overflow-hidden mt-1">
                <div class="h-full bg-brand-danger" :style="{ width: deltaAbsRatio }"></div>
              </div>
            </div>
          </div>
          <div class="text-xs space-y-1" v-if="reasons.length">
            <div class="text-neutral-400">사유</div>
            <ul class="list-disc list-inside">
              <li v-for="r in reasons" :key="r" class="font-mono break-words">{{ r }}</li>
            </ul>
          </div>
          <div class="text-[10px] text-neutral-500 col-span-2 md:col-span-1" v-if="monitor?.last_snapshot?.sample_count != null">샘플수: {{ monitor?.last_snapshot?.sample_count }}</div>
        </div>

        <!-- Top Drift Features -->
  <div class="p-3 md:p-4 rounded bg-neutral-800/50 border border-neutral-700 space-y-3 min-w-0">
          <h2 class="text-sm font-semibold">상위 드리프트 Z</h2>
          <div class="overflow-x-auto -mx-3 md:mx-0 px-3">
          <table class="w-full text-[10px] md:text-[11px] min-w-[360px] leading-tight">
            <thead class="text-neutral-400">
              <tr><th class="text-left pb-0.5 md:pb-1">특징</th><th class="text-right pb-0.5 md:pb-1">Z</th><th class="text-right pb-0.5 md:pb-1">드리프트</th></tr>
            </thead>
            <tbody>
              <tr v-for="f in topDrift" :key="f.feature" class="border-t border-neutral-800/60">
                <td class="py-0.5 md:py-1 pr-2 font-mono">{{ f.feature }}</td>
                <td class="py-0.5 md:py-1 pr-2 font-mono" :class="zColor(f.z_score)">{{ f.z_score.toFixed(3) }}</td>
                <td class="py-0.5 md:py-1 text-right"><span :class="f.drift ? 'text-brand-danger':'text-neutral-500'">{{ f.drift ? 'Y':'N' }}</span></td>
              </tr>
              <tr v-if="topDrift.length === 0"><td colspan="3" class="text-center text-neutral-500 py-2">데이터 없음</td></tr>
            </tbody>
          </table>
          </div>
        </div>
      </div>
    </section>

    <!-- Reliability bins comparison -->
  <section class="card space-y-4 leading-tight">
      <h2 class="text-sm font-semibold">신뢰 구간</h2>
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
        <div class="min-w-0">
          <h3 class="text-xs text-neutral-400 mb-1">라이브</h3>
          <div class="overflow-x-auto -mx-3 md:mx-0 px-3">
          <table class="w-full text-[10px] md:text-[11px] min-w-[420px] leading-tight">
            <thead class="text-neutral-400">
              <tr><th class="text-left pb-0.5 md:pb-1">구간</th><th class="text-right pb-0.5 md:pb-1">평균</th><th class="text-right pb-0.5 md:pb-1">경험</th><th class="text-right pb-0.5 md:pb-1">격차</th></tr>
            </thead>
            <tbody>
              <tr v-for="b in live?.reliability_bins || []" :key="'l'+b.bin_lower" class="border-t border-neutral-800/50">
                <td class="py-0.5 md:py-1 font-mono">{{ binLabel(b) }}</td>
                <td class="py-0.5 md:py-1 font-mono text-right">{{ num(b.mean_pred_prob) }}</td>
                <td class="py-0.5 md:py-1 font-mono text-right">{{ num(b.empirical_prob) }}</td>
                <td class="py-0.5 md:py-1 font-mono text-right" :class="gapClass(b.gap)">{{ num(b.gap) }}</td>
              </tr>
              <tr v-if="!live?.reliability_bins?.length"><td colspan="4" class="text-center text-neutral-500 py-2">데이터 없음</td></tr>
            </tbody>
          </table>
          </div>
        </div>
        <div class="min-w-0">
          <h3 class="text-xs text-neutral-400 mb-1">프로덕션</h3>
          <div class="overflow-x-auto -mx-3 md:mx-0 px-3">
          <table class="w-full text-[10px] md:text-[11px] min-w-[420px] leading-tight">
            <thead class="text-neutral-400">
              <tr><th class="text-left pb-0.5 md:pb-1">구간</th><th class="text-right pb-0.5 md:pb-1">평균</th><th class="text-right pb-0.5 md:pb-1">경험</th><th class="text-right pb-0.5 md:pb-1">격차</th></tr>
            </thead>
            <tbody>
              <tr v-for="b in prod?.reliability_bins || []" :key="'p'+b.bin_lower" class="border-t border-neutral-800/50">
                <td class="py-0.5 md:py-1 font-mono">{{ binLabel(b) }}</td>
                <td class="py-0.5 md:py-1 font-mono text-right">{{ num(b.mean_pred_prob) }}</td>
                <td class="py-0.5 md:py-1 font-mono text-right">{{ num(b.empirical_prob) }}</td>
                <td class="py-0.5 md:py-1 font-mono text-right" :class="gapClass(b.gap)">{{ num(b.gap) }}</td>
              </tr>
              <tr v-if="!prod?.reliability_bins?.length"><td colspan="4" class="text-center text-neutral-500 py-2">데이터 없음</td></tr>
            </tbody>
          </table>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, computed, ref } from 'vue';
import { storeToRefs } from 'pinia';
import { useCalibrationStore } from '../stores/calibration';
import { useOhlcvStore } from '../stores/ohlcv';
import ReliabilityChart from '@/components/ReliabilityChart.vue';

const store = useCalibrationStore();
const { loading, error, prod, live, monitor, auto, intervalSec, deltaECE, recommendation, reasons, topDrift, liveWindowSeconds, liveBins } = storeToRefs(store);
const { fetchAll, toggleAuto, setIntervalSec } = store;

// OHLCV context for symbol/interval scoping
const ohlcv = useOhlcvStore();
const ohlcvSymbol = computed(() => ohlcv.symbol);
const ohlcvInterval = computed(() => ohlcv.interval);
// Common kline intervals (can be adjusted to your supported set)
const intervals = ['1m','3m','5m','15m','30m','1h','2h','4h','6h','12h','1d'];


onMounted(() => {
  // Initialize interval if empty, prefer a mid-range default
  if (!ohlcv.interval) ohlcv.initDefaults(ohlcv.symbol, '15m');
  fetchAll();
  store.startAuto();
});

function onIntervalChange(){
  // When interval changes, refresh calibration (live fetch will use current ohlcv interval)
  fetchAll();
}

const deltaECEDisplay = computed(() => deltaECE.value == null ? '—' : deltaECE.value.toFixed(4));
// auto labeler status helpers removed
// live window/bins UI state
const liveWindowInput = ref<number>(3600);
const liveBinsInput = ref<number>(10);
onMounted(() => {
  liveWindowInput.value = liveWindowSeconds.value || 3600;
  liveBinsInput.value = liveBins.value || 10;
});
function onLiveWindowChange() {
  const v = liveWindowInput.value;
  if (typeof (store as any).setLiveWindowSeconds === 'function') {
    (store as any).setLiveWindowSeconds(v);
  } else {
    // Fallback if action not hot-reloaded yet
    liveWindowSeconds.value = Math.max(60, Math.floor(v));
    fetchAll();
  }
}
function onLiveBinsChange() {
  const v = liveBinsInput.value;
  if (typeof (store as any).setLiveBins === 'function') {
    (store as any).setLiveBins(v);
  } else {
    // Fallback
    liveBins.value = Math.min(50, Math.max(5, Math.floor(v)));
    fetchAll();
  }
}
// Display label sample count from calibration monitor snapshot (backend keeps it updated)
const sampleCountDisplay = computed(() => {
  const sc = monitor.value?.last_snapshot?.sample_count;
  return typeof sc === 'number' ? sc : '—';
});
const liveECEBar = computed(() => {
  const e = live.value?.ece;
  if (typeof e !== 'number') return '0%';
  const capped = Math.min(0.2, Math.max(0, e));
  return (capped / 0.2) * 100 + '%';
});
const deltaAbsRatio = computed(() => {
  const d = monitor.value?.last_snapshot?.delta;
  const th = monitor.value?.last_snapshot?.abs_threshold;
  if (typeof d === 'number' && typeof th === 'number' && th > 0) {
    return Math.min(1, d / th) * 100 + '%';
  }
  return '0%';
});

function binLabel(b: any) { return b.bin_lower != null && b.bin_upper != null ? `${b.bin_lower.toFixed(2)}-${b.bin_upper.toFixed(2)}` : '-'; }
function num(v: any) { return typeof v === 'number' ? v.toFixed(3) : '—'; }
function gapClass(g: any) { if (typeof g !== 'number') return 'text-neutral-500'; if (g > 0.08) return 'text-brand-danger'; if (g > 0.04) return 'text-amber-400'; return 'text-brand-accent'; }
function eceClass(e: any) { if (typeof e !== 'number') return 'text-neutral-500'; if (e > 0.08) return 'text-brand-danger'; if (e > 0.04) return 'text-amber-400'; return 'text-brand-accent'; }
function deltaClass(d: any) { if (typeof d !== 'number') return 'text-neutral-500'; if (d > 0.08) return 'text-brand-danger'; if (d > 0.04) return 'text-amber-400'; return 'text-brand-accent'; }
function zColor(z: number) { const az = Math.abs(z); if (az > 3) return 'text-brand-danger'; if (az > 2) return 'text-amber-400'; return 'text-brand-accent'; }
</script>

<style scoped>
table { border-collapse: collapse; }
</style>