import type { Directive } from 'vue'
import sanitize from '@/lib/sanitize'

// v-safe-html: Sanitizes bound HTML string before injecting into the element.
// Usage in <script setup>:
//   import vSafeHtml from '@/directives/safeHtml'
//   <div v-safe-html="someHtml" />
const vSafeHtml: Directive<HTMLElement, unknown> = {
  created(el, binding) {
    el.innerHTML = sanitize(binding.value)
  },
  beforeUpdate(el, binding) {
    // update before children update to minimize flicker
    el.innerHTML = sanitize(binding.value)
  },
  updated(el, binding) {
    // ensure post-update consistency
    el.innerHTML = sanitize(binding.value)
  }
}

export default vSafeHtml
