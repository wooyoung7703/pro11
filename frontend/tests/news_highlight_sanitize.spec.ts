import { describe, it, expect } from 'vitest'
import sanitize from '@/lib/sanitize'

describe('sanitize + highlight interop', () => {
  it('preserves highlight <span> with class attribute', () => {
    const input = 'Hello <span class="highlight">BTC</span>!'
    const out = sanitize(input)
    expect(out).toContain('<span class="highlight">BTC</span>')
  })

  it('strips script tags and their contents', () => {
    const input = 'x<script>alert(1)</script>y'
    const out = sanitize(input)
    expect(out.toLowerCase()).not.toContain('<script')
    expect(out).not.toContain('alert(1)')
    expect(out.replace(/\s+/g, '')).toBe('xy')
  })

  it('removes event handler attributes, keeps allowed ones', () => {
    const input = '<span onclick="alert(1)" class="highlight">X</span>'
    const out = sanitize(input)
    expect(out.toLowerCase()).not.toContain('onclick')
    expect(out).toContain('<span class="highlight">X</span>')
  })
})
