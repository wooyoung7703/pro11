import { describe, it, expect } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useNewsStore } from '@/stores/news';

// Mock http module
vi.mock('@/lib/http', () => ({
  default: {
    get: vi.fn(async (url: string) => {
      if (url.startsWith('/api/news/recent')) {
        // parse params
        const q = new URL('http://x' + url).searchParams;
        const since = q.get('since_ts');
        if (since) {
          // delta 응답 (신규 2개 기사)
          return { data: {
            status: 'ok', delta: true, limit: 100, summary_only: true,
            latest_ts: Number(since) + 3,
            items: [
              { id: 'n_new_2', title: 'New Title 2', published_ts: Number(since)+3 },
              { id: 'n_new_1', title: 'New Title 1', published_ts: Number(since)+2 }
            ]
          }};
        }
        // 초기 전체 응답
        return { data: {
          status: 'ok', delta: false, limit: 100, summary_only: true,
          latest_ts: 1000,
          items: [
            { id: 'n3', title: 'Old 3', published_ts: 990 },
            { id: 'n2', title: 'Old 2', published_ts: 995 },
            { id: 'n1', title: 'Old 1', published_ts: 1000 }
          ]
        }};
      }
      if (url.startsWith('/api/news/status')) {
        return { data: { running: true }};
      }
      if (url.startsWith('/api/news/article/')) {
        return { data: { article: { id: url.split('/').pop(), body: 'FULL BODY' } } };
      }
      return { data: {} };
    }),
    post: vi.fn(async () => ({ data: { status: 'ok' }}))
  }
}));

describe('NewsStore delta merge', () => {
  setActivePinia(createPinia());
  const store = useNewsStore();

  it('initial load sets articles and latest ts', async () => {
    await store.fetchRecent();
    expect(store.articles.length).toBe(3);
    expect(store.lastLatestTs).toBe(1000);
    expect(store.articles[0].id).toBe('n1'); // 최신이 맨 앞 (정렬 desc)
  });

  it('delta fetch prepends new unique articles', async () => {
    await store.fetchRecent(); // delta 모드 (since_ts=1000)
    expect(store.articles.length).toBe(5);
    // 새 기사 2개가 앞에 위치
    expect(store.articles[0].id).toBe('n_new_2');
    expect(store.articles[1].id).toBe('n_new_1');
    expect(store.lastLatestTs).toBe(1003);
  });
});
