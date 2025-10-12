<template>
  <div class="grid grid-cols-2 md:grid-cols-6 gap-3 text-[11px]">
    <div v-for="c in items" :key="c.key" class="p-2 rounded bg-neutral-700/40 space-y-1">
      <div class="flex items-center justify-between">
        <span class="font-medium">{{ c.label }}</span>
        <span class="w-2 h-2 rounded-full" :class="c.indicatorClass"></span>
      </div>
      <div class="text-[10px] leading-tight text-neutral-400 min-h-[28px]">
        <div v-for="l in c.lines" :key="l.k">{{ l.v }}</div>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed } from 'vue';
import { buildSystemComponentItems, DashboardComponentItem } from '../utils/systemComponents';

interface Props { componentsRaw: any }
const props = defineProps<Props>();

const items = computed<DashboardComponentItem[]>(() => buildSystemComponentItems(props.componentsRaw));
</script>
<script lang="ts">export default {};</script>
