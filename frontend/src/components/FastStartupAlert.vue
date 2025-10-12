<template>
  <AlertBanner v-if="visible" :type="bannerType" :title="title" :description="description">
    <div class="flex flex-wrap gap-2 items-center mt-1" v-if="fast?.fast_startup">
      <span class="px-2 py-0.5 rounded bg-neutral-600/50 text-[10px] tracking-wide uppercase">FAST_STARTUP</span>
      <span class="text-[10px] text-neutral-300">
        생략 컴포넌트: <template v-if="fast?.skipped_components?.length">
          <span v-for="(c,i) in fast.skipped_components" :key="c">{{ humanComponent(c) }}<span v-if="i < fast.skipped_components.length-1">, </span></span>
        </template><template v-else>-</template>
      </span>
      <button class="btn btn-xs" :disabled="upgradeLoading || !fast?.upgrade_possible" @click="emitUpgrade">
        <span v-if="upgradeLoading" class="animate-pulse">업그레이드...</span>
        <span v-else>업그레이드 실행</span>
      </button>
    </div>
    <div v-if="fast?.degraded_components?.length" class="mt-2 text-[10px] text-amber-300">
      느려진(Degraded): {{ fast.degraded_components.join(', ') }}
    </div>
  </AlertBanner>
</template>
<script setup lang="ts">
import { computed } from 'vue';
import AlertBanner from './AlertBanner.vue';

interface FastInfo {
  fast_startup: boolean;
  skipped_components?: string[];
  degraded_components?: string[];
  upgrade_possible?: boolean;
}
interface Props {
  fast: FastInfo | null | undefined;
  ingestionEnabled: boolean | null;
  ingestionRunning: boolean | null;
  upgradeLoading?: boolean;
}
const props = defineProps<Props>();
const emit = defineEmits<{ (e:'upgrade'):void }>();

function emitUpgrade(){ emit('upgrade'); }

const visible = computed(() => {
  if (!props.fast) return false;
  // Ingestion enabled && not running && fast_startup active → 안내 필요
  if (props.ingestionEnabled && props.ingestionRunning === false && props.fast.fast_startup) return true;
  return false;
});

const bannerType = computed(() => props.fast?.fast_startup ? 'warning' : 'info');
const title = computed(() => props.fast?.fast_startup ? 'Fast Startup 모드 활성' : '시스템 알림');
const description = computed(() => {
  if (props.fast?.fast_startup) {
    return '일부 백그라운드 태스크가 아직 기동되지 않았습니다. 업그레이드 실행으로 즉시 시작할 수 있습니다.';
  }
  return '';
});

function humanComponent(key: string) {
  const map: Record<string,string> = {
    'ingestion_ws': 'OHLCV Ingestion',
    'feature_scheduler': 'Feature Scheduler',
    'model_training': 'Model Training',
    'calibration_monitor': 'Calibration Monitor',
    'risk_loop': 'Risk Loop',
    'news_ingestion': 'News Ingestion',
  };
  return map[key] || key;
}
</script>
<script lang="ts">export default {};</script>
