import { mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { describe, it, expect } from 'vitest';
import Trading from '../src/views/Trading.vue';

// Minimal smoke: mounts and shows key controls; data comes from MSW handlers

describe('Trading smoke (mocked)', () => {
  it('renders and shows Save button', async () => {
    const wrapper = mount(Trading as any, {
      global: {
        plugins: [createPinia()],
      }
    });
    // Wait a tick for initial async calls
    await new Promise(r => setTimeout(r, 200));
    expect(wrapper.text()).toContain('Live Trading');
    const save = wrapper.findAll('button').find(b => /save/i.test(b.text()));
    expect(!!save).toBe(true);
  });
});
