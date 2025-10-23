import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import AdminPanel from '@/views/AdminPanel.vue'
import { createPinia, setActivePinia } from 'pinia'

// Mock http module to control responses
vi.mock('@/lib/http', () => {
  const get = vi.fn(async (url: string) => {
    if (url === '/admin/inference/thresholds') return { data: { effective_threshold: 0.9 } }
    if (url === '/admin/inference/auto/status') return { data: { enabled: false, interval_sec: 10 } }
    if (url === '/api/features/backfill/runs') return { data: { items: [], total: 0, page: 1, page_size: 50 } }
    if (url === '/api/risk/state') return { data: { session: { starting_equity: 0, peak_equity: 0, current_equity: 0, cumulative_pnl: 0 }, positions: [] } }
    if (url === '/admin/models/artifacts/verify') return { data: { summary: { ok: 0, missing: 0, file_not_found: 0, file_check_error: 0 }, rows: [] } }
    if (url === '/api/models/summary') return { data: { status: 'ok', has_model: true, production: { id: 1, version: 1 } } }
    return { data: {} }
  })
  const post = vi.fn(async (url: string, _payload?: any) => {
    if (url === '/admin/models/reset') return { data: { status: 'ok' } }
    if (url === '/admin/bootstrap') return { data: { status: 'ok', training: { status: 'ok' } } }
    return { data: { status: 'ok' } }
  })
  return { default: { get, post } }
})

// Mock ConfirmDialog to auto-confirm actions
// Use partial mock form to keep module shape compatible with Vue Test Utils component transformer
vi.mock('@/components/ConfirmDialog.vue', async (importOriginal) => {
  const actual: any = await importOriginal();
  return {
    // Provide a dummy named export to satisfy VTU's isTeleport check on mocked SFC modules
  __isTeleport: undefined as any,
  __isKeepAlive: undefined as any,
    ...(actual as any),
    default: {
      name: 'ConfirmDialog',
      props: ['open','title','message','requireText','delayMs'],
      emits: ['confirm','cancel'],
      template: '<div v-if="open"><button id="confirm" @click="$emit(\'confirm\')">OK</button></div>'
    }
  };
});

// Helpers
const tick = () => new Promise((r) => setTimeout(r))

describe('AdminPanel loading bar', () => {
  it('hides after initial data load and after reset+bootstrap', async () => {
    setActivePinia(createPinia())
    const wrapper = mount(AdminPanel, { global: { plugins: [createPinia()] } })
    // Initially, loading bar should become active
    await tick()
    // Let initial load sequence complete
    await new Promise((r) => setTimeout(r, 20))
    // Loading bar should be inactive
    expect(wrapper.html()).not.toContain('초기 로딩 중')

    // Trigger reset; ConfirmDialog mock will auto-confirm
    // Find the reset button and click
    const resetBtn = wrapper.findAll('button').find(b => b.text().includes('모델 초기화'))
    if (resetBtn) await resetBtn.trigger('click')
    // Simulate confirm click
    await tick()
    const confirmBtn = wrapper.find('#confirm')
    if (confirmBtn.exists()) await confirmBtn.trigger('click')

    // Wait a tick for post-reset flow
    await new Promise((r) => setTimeout(r, 30))

    // Ensure no loading bar remains
    expect(wrapper.html()).not.toContain('초기 로딩 중')
  })
})
