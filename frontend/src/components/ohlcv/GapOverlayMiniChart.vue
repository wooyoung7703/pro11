<template>
  <div class="gap-mini-chart" v-if="candles.length">
    <svg :width="width" :height="height">
      <!-- 배경 영역 -->
      <rect :width="width" :height="height" fill="#0f1115" rx="4" ry="4" />

      <!-- 전체 타임레인지 기준 스케일 -->
      <g v-if="range">
        <!-- Gap Segment 하이라이트 -->
        <rect
          v-for="(seg, idx) in gapSegments"
          :key="idx"
          class="gap-segment"
          :x="timeToX(seg.from_ts)"
          :y="2"
          :width="Math.max(1, timeToX(seg.to_ts) - timeToX(seg.from_ts))"
          :height="height - 4"
          :fill="seg.state==='open' ? 'rgba(255,99,71,0.35)' : 'rgba(255,215,0,0.20)'"
          :stroke="seg.state==='open' ? 'tomato' : 'gold'"
          stroke-width="1"
        />

        <!-- 가격 라인 (간단히 종가 기준 스파크라인) -->
        <polyline
          class="price-line"
          :points="sparklinePoints"
          fill="none"
          stroke="#4ea1ff"
          stroke-width="1.2"
          stroke-linejoin="round"
          stroke-linecap="round"
          opacity="0.9"
        />
      </g>

      <!-- Open gap count 표시 -->
      <text x="6" :y="height - 6" font-size="10" fill="#ccc">Open: {{ openGapCount }}</text>
    </svg>
  </div>
  <div v-else class="empty">No data</div>
</template>

<script setup lang="ts">
import { computed } from 'vue';
import { useOhlcvStore } from '@/stores/ohlcv';

interface Props {
  width?: number;
  height?: number;
}
const props = withDefaults(defineProps<Props>(), { width: 240, height: 60 });

const store = useOhlcvStore();
const candles = computed(()=> store.candles);
const gapSegments = computed(()=> store.gapSegments);
const openGapCount = computed(()=> store.openGapCount);

const range = computed(()=> {
  if(!candles.value.length) return null;
  let lo = candles.value[0].low; let hi = candles.value[0].high;
  for(const c of candles.value){ if(c.low < lo) lo=c.low; if(c.high>hi) hi=c.high; }
  return { lo, hi };
});

function timeToX(ts: number){
  if(!candles.value.length) return 0;
  const first = candles.value[0].open_time;
  const last = candles.value[candles.value.length-1].open_time;
  if(last === first) return 0;
  return ( (ts - first) / (last - first) ) * props.width;
}

const sparklinePoints = computed(()=> {
  if(!candles.value.length || !range.value) return '';
  const { lo, hi } = range.value;
  const span = hi - lo || 1;
  return candles.value.map((c,i)=> {
    const x = (i / Math.max(1, candles.value.length -1)) * props.width;
    const y = props.height - ((c.close - lo)/span)*props.height;
    return `${x},${y}`;
  }).join(' ');
});
</script>

<style scoped>
.gap-mini-chart { font-family: system-ui, sans-serif; }
.empty { font-size: 12px; color: #666; padding: 4px; }
</style>
