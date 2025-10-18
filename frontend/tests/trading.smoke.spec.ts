import { mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { describe, it, expect } from 'vitest';
import Trading from '../src/views/Trading.vue';

// Minimal smoke: mounts and shows key controls; data comes from MSW handlers

describe('Trading smoke (mocked)', () => {
  it('renders deprecation notice and basic content', async () => {
    const wrapper = mount(Trading as any, {
      global: {
        plugins: [createPinia()],
      }
    });
    // Wait a tick for initial async calls
    await new Promise(r => setTimeout(r, 200));
    expect(wrapper.text()).toContain('이 화면은 더 이상 사용되지 않습니다.');
    expect(wrapper.text()).toContain('Autopilot 대시보드');
  });
});
