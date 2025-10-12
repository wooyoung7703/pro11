import { mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { describe, it, expect } from 'vitest';
import Trading from '../src/views/Trading.vue';

// Minimal smoke: mounts and shows key controls; data comes from MSW handlers

describe('Trading smoke (mocked)', () => {
  it('renders and shows Live Trading header and Refresh button', async () => {
    const wrapper = mount(Trading as any, {
      global: {
        plugins: [createPinia()],
      }
    });
    // Wait a tick for initial async calls
    await new Promise(r => setTimeout(r, 200));
    expect(wrapper.text()).toContain('Live Trading');
    // UI에서 Save 버튼이 제거되었으므로 Refresh 버튼 존재로 검증
    const btn = wrapper.findAll('button').find(b => /refresh/i.test(b.text()));
    expect(!!btn).toBe(true);
  });
});
