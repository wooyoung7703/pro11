import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ExitSettings from '@/views/admin-settings/ExitSettings.vue'

// Mock http module used by the component. Do not capture outer variables to avoid hoist issues.
vi.mock('@/lib/http', () => ({
  default: {
    get: vi.fn(async (_url: string) => ({ data: {} })),
    put: vi.fn(async (_url: string, _payload?: any) => ({ data: { status: 'ok' } }))
  }
}))

const tick = () => new Promise((r) => setTimeout(r))

describe('ExitSettings validation', () => {
  beforeEach(async () => {
    const http: any = (await import('@/lib/http')).default
    http.get.mockClear()
    http.put.mockClear()
  })

  it('rejects partial levels sum > 1 and does not call save API', async () => {
    const wrapper = mount(ExitSettings)
    await tick()

    // 입력: 합이 1.1인 경우
    const input = wrapper.find('input[type="text"]')
    await input.setValue('0.6,0.5')

    // 저장 클릭
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('저장'))
    expect(saveBtn).toBeTruthy()
    if (saveBtn) await saveBtn.trigger('click')

    await tick()
    // 오류 메시지 노출 및 put 미호출
    expect(wrapper.text()).toMatch(/합은 1을 초과|합은 1을 초과할 수 없습니다|오류/)
  const http: any = (await import('@/lib/http')).default
  expect(http.put).not.toHaveBeenCalled()
  })

  it('rejects trailing percent > 50% and does not call save API', async () => {
    const wrapper = mount(ExitSettings)
    await tick()

    // 트레일 모드 percent 유지, 51% 입력
    const percentInput = wrapper.findAll('input[type="number"]').at(0)
    // 첫 번째 number input은 트레일링 폭(%) (컴포넌트 구성상 순서대로 렌더링됨)
    await percentInput?.setValue('51')

    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('저장'))
    expect(saveBtn).toBeTruthy()
    if (saveBtn) await saveBtn.trigger('click')

    await tick()
    expect(wrapper.text()).toMatch(/트레일링 폭.*0~50|오류/)
  const http2: any = (await import('@/lib/http')).default
  expect(http2.put).not.toHaveBeenCalled()
  })

  it('accepts valid inputs and calls save API', async () => {
    const wrapper = mount(ExitSettings)
    await tick()

    // 유효한 값들 셋업
    // 트레일링 percent: 0.5%
    const numberInputs = wrapper.findAll('input[type="number"]')
    // 순서: trailPercentPct, timeStopBars, cooldownBars, dailyLossCapR
    await numberInputs.at(0)?.setValue('0.5')
    await numberInputs.at(1)?.setValue('10')
    await numberInputs.at(2)?.setValue('5')
    await numberInputs.at(3)?.setValue('2')

    // 부분 청산: 0.25,0.25 (합 0.5)
    const levelsInput = wrapper.find('input[type="text"]')
    await levelsInput.setValue('0.25,0.25')

    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('저장'))
    expect(saveBtn).toBeTruthy()
    if (saveBtn) await saveBtn.trigger('click')

    await tick()
  const http3: any = (await import('@/lib/http')).default
  expect(http3.put).toHaveBeenCalled()
    expect(wrapper.text()).toMatch(/저장 완료|일부 실패/)
  })
})
