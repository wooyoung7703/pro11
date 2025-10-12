<template>
  <div class="w-full">
    <div class="flex items-center justify-between mb-1">
      <h3 class="text-xs text-neutral-400">신뢰도 곡선</h3>
      <div class="flex items-center gap-3 text-[10px]">
        <span class="flex items-center gap-1"><span class="inline-block w-3 h-0.5" :style="{ background: liveColor }"></span> Live</span>
        <span class="flex items-center gap-1"><span class="inline-block w-3 h-0.5" :style="{ background: prodColor }"></span> Prod</span>
      </div>
    </div>
  <svg :viewBox="`0 0 ${width} ${height}`" :width="'100%'" :height="height" role="img" aria-label="reliability diagram">
      <!-- background -->
      <rect :x="0" :y="0" :width="width" :height="height" fill="transparent" />
      <!-- axes box -->
      <rect :x="pad" :y="pad" :width="plotW" :height="plotH" fill="none" stroke="#404040" stroke-width="1" />
      <!-- diagonal y=x -->
      <line :x1="pad" :y1="pad + plotH" :x2="pad + plotW" :y2="pad" stroke="#6b7280" stroke-dasharray="4 3" stroke-width="1" />
      <!-- ticks (0, 0.5, 1) -->
      <g v-for="t in ticks" :key="'tx'+t">
        <line :x1="sx(t)" :x2="sx(t)" :y1="pad+plotH" :y2="pad+plotH+4" stroke="#6b7280" stroke-width="1" />
        <text :x="sx(t)" :y="pad+plotH+12" text-anchor="middle" fill="#9ca3af" font-size="9">{{ t }}</text>
      </g>
      <g v-for="t in ticks" :key="'ty'+t">
        <line :x1="pad-4" :x2="pad" :y1="sy(t)" :y2="sy(t)" stroke="#6b7280" stroke-width="1" />
        <text :x="pad-6" :y="sy(t)+3" text-anchor="end" fill="#9ca3af" font-size="9">{{ t }}</text>
      </g>

      <!-- prod curve -->
      <polyline v-if="showProdLine" :points="poly(prodPts)" :stroke="prodColor" fill="none" stroke-width="1.5" />
      <g v-for="(p,i) in prodPts" :key="'pp'+i">
        <circle :cx="p[0]" :cy="p[1]" :r="prodR(i)" :fill="prodColor">
          <title>{{ prodTitle(i) }}</title>
        </circle>
      </g>

      <!-- live curve -->
      <polyline v-if="showLiveLine" :points="poly(livePts)" :stroke="liveColor" fill="none" stroke-width="1.5" />
      <g v-for="(p,i) in livePts" :key="'lp'+i">
        <circle :cx="p[0]" :cy="p[1]" :r="liveR(i)" :fill="liveColor">
          <title>{{ liveTitle(i) }}</title>
        </circle>
      </g>

      <!-- empty state -->
      <text v-if="!livePts.length && !prodPts.length" :x="pad + plotW/2" :y="pad + plotH/2" text-anchor="middle" fill="#9ca3af" font-size="11">데이터 없음</text>
    </svg>
  </div>
  
</template>

<script setup lang="ts">
import { computed } from 'vue';

interface Bin { mean_pred_prob?: number | null; empirical_prob?: number | null; count?: number }

const props = defineProps<{
  binsLive?: Bin[];
  binsProd?: Bin[];
  width?: number;
  height?: number;
}>();

const width = computed(() => props.width ?? 320);
const height = computed(() => props.height ?? 220);
const pad = 28; // padding for axes labels
const liveColor = '#34d399'; // emerald-400
const prodColor = '#f59e0b'; // amber-500
const ticks = [0, 0.5, 1];

const plotW = computed(() => width.value - pad * 2);
const plotH = computed(() => height.value - pad * 2);

function clamp01(v: number) { return Math.max(0, Math.min(1, v)); }
function sx(x: number) { return pad + clamp01(x) * plotW.value; }
function sy(y: number) { return pad + (1 - clamp01(y)) * plotH.value; }
function poly(pts: Array<[number, number]>) { return pts.map(p => p.join(',')).join(' '); }

function mkPts(bins?: Bin[]) {
  if (!Array.isArray(bins)) return { pts: [] as Array<[number, number]>, xs: [] as number[], ys: [] as number[], counts: [] as number[] };
  const arr = bins
    .map((b, idx) => ({ x: Number(b.mean_pred_prob), y: Number(b.empirical_prob), c: Number(b.count) || 0, idx }))
    .filter(v => isFinite(v.x) && isFinite(v.y))
    .sort((a,b) => a.x - b.x);
  const xs = arr.map(p => p.x);
  const ys = arr.map(p => p.y);
  const counts = arr.map(p => p.c);
  // detect collapsed x-range (all points clustered near 0/1)
  const xmin = xs.length ? Math.min(...xs) : 0;
  const xmax = xs.length ? Math.max(...xs) : 0;
  const collapsed = (xmax - xmin) < 0.02; // <2% of range
  const jitterPx = collapsed ? Math.min(6, plotW.value * 0.02) : 0;
  const n = arr.length;
  const pts = arr.map((p, i) => {
    const jx = jitterPx ? ((i - (n - 1) / 2) / Math.max(1, (n - 1))) * jitterPx : 0;
    return [sx(p.x) + jx, sy(p.y)] as [number, number];
  });
  return { pts, xs, ys, counts };
}

const liveData = computed(() => mkPts(props.binsLive));
const prodData = computed(() => mkPts(props.binsProd));
const livePts = computed(() => liveData.value.pts);
const prodPts = computed(() => prodData.value.pts);
const showLiveLine = computed(() => livePts.value.length >= 2 && (Math.max(...(liveData.value.xs||[0])) - Math.min(...(liveData.value.xs||[0])) >= 0.02));
const showProdLine = computed(() => prodPts.value.length >= 2 && (Math.max(...(prodData.value.xs||[0])) - Math.min(...(prodData.value.xs||[0])) >= 0.02));

function scaleR(counts: number[]) {
  const maxC = counts.length ? Math.max(...counts) : 0;
  return (i: number) => {
    const c = counts[i] || 0;
    if (maxC <= 0) return 2;
    const r = 2 + 3 * Math.sqrt(c / maxC);
    return Math.max(2, Math.min(5, r));
  };
}
const liveR = computed(() => scaleR(liveData.value.counts));
const prodR = computed(() => scaleR(prodData.value.counts));

function titleFor(bins: Bin[] | undefined, i: number) {
  const b = Array.isArray(bins) ? bins.filter(b => b && b.mean_pred_prob != null && b.empirical_prob != null)[i] : undefined;
  if (!b) return '';
  const mp = typeof b.mean_pred_prob === 'number' ? b.mean_pred_prob.toFixed(3) : '-';
  const ep = typeof b.empirical_prob === 'number' ? b.empirical_prob.toFixed(3) : '-';
  const c = b.count ?? 0;
  return `mean=${mp}, empirical=${ep}, n=${c}`;
}
const liveTitle = (i: number) => titleFor(props.binsLive, i);
const prodTitle = (i: number) => titleFor(props.binsProd, i);
</script>

<style scoped>
svg { display: block; }
</style>
