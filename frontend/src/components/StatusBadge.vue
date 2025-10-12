<template>
  <span :class="classes" class="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium tracking-wide" :data-extra="extraClass">
    <span v-if="icon" aria-hidden="true">{{ icon }}</span>
    <slot>{{ style.label }}</slot>
  </span>
</template>
<script setup lang="ts">
import { computed } from 'vue';
import { statusStyle, CoreStatus } from '../utils/status';

interface Props { status: CoreStatus | string | null | undefined; icon?: string | null; extraClass?: string }
const props = defineProps<Props>();

const style = computed(() => statusStyle(props.status));
const classes = computed(() => [style.value.bg, style.value.text, props.extraClass].filter(Boolean).join(' '));
</script>
<script lang="ts">export default {};</script>
