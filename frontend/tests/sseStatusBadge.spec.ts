import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SseStatusBadge from '@/components/SseStatusBadge.vue'

function text(wrapper:any){ return wrapper.text().replace(/\s+/g,' ').trim() }

describe('SseStatusBadge', () => {
  it('shows warning icon and amber ring on warning', () => {
    const w = mount(SseStatusBadge, { props: { status: 'warning', label: 'sse:signals online' } })
    expect(text(w)).toContain('⚠')
    const anySpanHasAmberRing = w.findAll('span').some(s => /ring-amber-400\/30|ring-amber-400-?\/30/.test(s.classes().join(' ')))
    expect(anySpanHasAmberRing).toBe(true)
  })

  it('shows danger icon and rose ring on danger', () => {
    const w = mount(SseStatusBadge, { props: { status: 'danger', label: 'sse:risk lagging' } })
    expect(text(w)).toContain('⚠')
    const anySpanHasRoseRing = w.findAll('span').some(s => /ring-rose-400\/30|ring-rose-400-?\/30/.test(s.classes().join(' ')))
    expect(anySpanHasRoseRing).toBe(true)
  })

  it('compact reduces padding/text-size', () => {
    const w = mount(SseStatusBadge, { props: { status: 'ok', label: 'ok', compact: true } })
    const root = w.find('[role="status"]')
    expect(root.exists()).toBe(true)
    expect(root.classes().join(' ')).toMatch(/!px-1\.5/)
  })

  it('aria-label falls back to title then status/label', () => {
    const w1 = mount(SseStatusBadge, { props: { status: 'idle', label: 'db', title: 'source db' } })
    expect(w1.attributes('aria-label')).toBe('source db')
    const w2 = mount(SseStatusBadge, { props: { status: 'neutral', label: 'none' } })
    expect(w2.attributes('aria-label')).toContain('status neutral none')
  })
})
