<template>
  <StatusBadge :status="status" :title="title" :icon="icon" :extraClass="badgeClass" :aria-label="ariaLabel" role="status">
    <span :class="ringClass">{{ label }}</span>
  </StatusBadge>
  
</template>

<script lang="ts">
import { computed, defineComponent, PropType } from 'vue'
import StatusBadge from './StatusBadge.vue'

export default defineComponent({
  name: 'SseStatusBadge',
  components: { StatusBadge },
  props: {
    status: {
      type: String as PropType<'ok'|'warning'|'danger'|'idle'|'neutral'|'unknown'>,
      required: true,
    },
    title: {
      type: String,
      default: undefined,
    },
    label: {
      type: String,
      required: true,
    },
    compact: {
      type: Boolean,
      default: false,
    }
  },
  setup(props) {
    const isWarning = computed(() => props.status === 'warning')
    const isDanger = computed(() => props.status === 'danger')
    const isAlert = computed(() => isWarning.value || isDanger.value)
    const icon = computed(() => isAlert.value ? '⚠' : null)
    const ringClass = computed(() => isAlert.value ? `ring-1 ${isDanger.value ? 'ring-rose-400/30' : 'ring-amber-400/30'} rounded-[3px]` : '')
    const badgeClass = computed(() => props.compact ? '!px-1.5 !py-[1px] !text-[10px]' : '')
    const ariaLabel = computed(() => props.title || `status ${props.status ?? ''} ${props.label}`.trim())
    return { icon, ringClass, badgeClass, ariaLabel }
  }
})
</script>
