import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import AdminPanel from '@/views/AdminPanel.vue'
import { createPinia, setActivePinia } from 'pinia'

// Dynamic mock http with stateful model existence
vi.mock('@/lib/http', () => {
  let modelExists = false
  const get = vi.fn(async (url: string) => {
    if (url === '/admin/inference/thresholds') return { data: { effective_threshold: 0.9 } }
    if (url === '/admin/inference/auto/status') return { data: { enabled: false, interval_sec: 10 } }
    if (url === '/api/features/backfill/runs') return { data: { items: [], total: 0, page: 1, page_size: 50 } }
    if (url === '/api/risk/state') return { data: { session: { starting_equity: 0, peak_equity: 0, current_equity: 0, cumulative_pnl: 0 }, positions: [] } }
    if (url === '/admin/models/artifacts/verify') return { data: { summary: { ok: 0, missing: 0, file_not_found: 0, file_check_error: 0 }, rows: [] } }
    if (url === '/api/models/summary') {
      return modelExists
        ? { data: { status: 'ok', has_model: true, production: { id: 1, version: 1 } } }
        : { data: { status: 'no_models', has_model: false } }
    }
    if (url === '/api/ohlcv/backfill/year/status') return { data: { status: 'ok' } }
    return { data: {} }
  })
  const post = vi.fn(async (url: string, _payload?: any) => {
    if (url === '/admin/models/reset') return { data: { status: 'ok' } }
    if (url === '/api/ohlcv/backfill/year/start') return { data: { status: 'ok' } }
    if (url === '/admin/features/backfill') return { data: { status: 'ok' } }
    if (url === '/api/training/run') {
      // When training runs, flip the model existence to true
      modelExists = true
      return { data: { status: 'ok', job: { id: 'job1', state: 'completed' } } }
    }
    if (url === '/admin/bootstrap') return { data: { status: 'ok', training: { status: 'ok' } } }
    return { data: { status: 'ok' } }
  })
  return { default: { get, post } }
})

// Mock ConfirmDialog to auto-confirm actions (partial mock shape to avoid VTU transform issues)
vi.mock('@/components/ConfirmDialog.vue', async (importOriginal) => {
  const actual: any = await importOriginal();
  return {
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

const sleep = (ms = 0) => new Promise((r) => setTimeout(r, ms))

describe('AdminPanel bootstrap gate on first load (no model)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('keeps loading bar visible until a model exists, then hides', async () => {
    const wrapper = mount(AdminPanel, { global: { plugins: [createPinia()] } })

    // Allow onMounted to run and gating to start
    await sleep(0)

    // Should show loading UI while orchestrating bootstrap
    expect(wrapper.html()).toContain('초기 로딩 중')

    // Wait a short time for mocked bootstrap steps to complete and modelExists to flip
    await sleep(60)

    // After bootstrap completes and summary reports a model, the loading bar should hide
    expect(wrapper.html()).not.toContain('초기 로딩 중')
  })
})
