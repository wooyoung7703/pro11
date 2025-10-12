<template>
  <div class="p-2 rounded bg-neutral-700/40" :class="{ 'py-1 px-2': dense }">
    <div class="flex items-center justify-between mb-0.5" v-if="label || $slots.header">
      <div class="text-[11px] text-neutral-400 font-medium">
        <slot name="label">{{ label }}</slot>
      </div>
      <slot name="badge"></slot>
    </div>
    <div class="font-semibold text-sm transition-colors duration-500" :class="[valueClass, flashClass]">
      <slot name="value">{{ valueDisplay }}</slot>
    </div>
    <div v-if="$slots.footer" class="mt-1 text-[10px] text-neutral-400">
      <slot name="footer" />
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { CoreStatus, statusStyle } from '../utils/status';

interface Props {
  label?: string;
  value?: any;
  status?: CoreStatus | string | null;
  format?: (v:any)=>string;
  dense?: boolean;
  flash?: boolean; // enable change highlight
}
const props = defineProps<Props>();

const style = computed(() => statusStyle(props.status || 'unknown'));
const valueDisplay = computed(() => props.format ? props.format(props.value) : (props.value ?? '-'));
const valueClass = computed(() => style.value.text);

const lastValue = ref<any>(undefined);
const flashState = ref(false);
const direction = ref<'up'|'down'|null>(null);
let flashTimer: ReturnType<typeof setTimeout> | null = null;
const flashClass = computed(() => {
  if (!props.flash || !flashState.value) return '';
  if (direction.value === 'up') return 'metric-flash-up';
  if (direction.value === 'down') return 'metric-flash-down';
  return 'metric-flash';
});

watch(() => props.value, (nv, ov) => {
  if (!props.flash) return;
  if (ov === undefined) { lastValue.value = nv; return; }
  if (nv !== ov) {
    direction.value = (typeof nv === 'number' && typeof ov === 'number') ? (nv > ov ? 'up' : 'down') : null;
    flashState.value = false; // force reflow if same class repeats
    // next microtask
    requestAnimationFrame(() => {
      flashState.value = true;
    });
    if (flashTimer) clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { flashState.value = false; }, 600);
    lastValue.value = nv;
  }
});
</script>
<script lang="ts">export default {};</script>
<style scoped>
/* base (neutral) fallback */
.metric-flash { background-image: linear-gradient(90deg, rgba(56,189,248,0.25), rgba(56,189,248,0)); -webkit-background-clip: text; background-clip: text; color: #38bdf8 !important; }
.metric-flash-up { background-image: linear-gradient(90deg, rgba(34,197,94,0.35), rgba(34,197,94,0)); -webkit-background-clip: text; background-clip: text; color: #4ade80 !important; }
.metric-flash-down { background-image: linear-gradient(90deg, rgba(239,68,68,0.4), rgba(239,68,68,0)); -webkit-background-clip: text; background-clip: text; color: #f87171 !important; }
</style>
