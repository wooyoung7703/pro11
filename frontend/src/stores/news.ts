import { defineStore } from 'pinia';
import { ref } from 'vue';
import http from '@/lib/http';

export interface NewsArticle {
  id: string;
  title: string;
  summary?: string;
  source?: string;
  published_ts?: number; // epoch seconds
  body?: string;
}

export interface NewsStatus {
  running?: boolean;
  fetch_runs?: number;
  fetch_errors?: number;
  articles_ingested?: number;
  lag_seconds?: number | null;
  last_run_ts?: number | null;
  last_ingested_ts?: number | null;
  fast_startup_skipped?: boolean;
  poll_interval?: number | null;
  active_sources?: string[];
}

export const useNewsStore = defineStore('news', () => {
  const articles = ref<NewsArticle[]>([]);
  const status = ref<NewsStatus>({});
  // delta fetch 관련 상태
  const lastLatestTs = ref<number | null>(null); // 가장 최근 published_ts
  const maxArticles = 500; // 보유 상한
  const loading = ref(false);
  const error = ref<string | null>(null);
  const auto = ref(true);
  const intervalSec = ref(10);
  let timer: any = null;
  // health snapshot
  const health = ref<any[]>([]);
  const dedupHistory = ref<Record<string, {ts:number, raw:number, kept:number, ratio:number}[]>>({});
  // --- New summary & sources & search state ---
  const summary = ref<any|null>(null);
  const summaryLoading = ref(false);
  const sources = ref<any[]>([]); // /api/news/sources
  const sourcesLoading = ref(false);
  const sourcesLoadedAt = ref<number|null>(null);
  const searchQuery = ref('');
  const searchResults = ref<NewsArticle[]>([]);
  const searchLoading = ref(false);
  const searchError = ref<string|null>(null);
  const searchLastExecuted = ref<number|null>(null);
  // ---- Body batch fetch queue ----
  const pendingBodyIds = new Set<string>();
  let bodyDebounceTimer: any = null;
  const bodyDebounceMs = 150;

  async function flushBodyQueue() {
    if (!pendingBodyIds.size) return;
    const ids = Array.from(pendingBodyIds);
    pendingBodyIds.clear();
    try {
      // Bulk endpoint (to be implemented server-side if not existing). Fallback: individual fetch.
      // Attempt bulk; if 404 or error -> fallback sequential.
      try {
        const r = await http.get(`/api/news/body?ids=${encodeURIComponent(ids.join(','))}`);
        if (r.data && Array.isArray(r.data.items)) {
          const map: Record<string,string|undefined> = {};
          for (const it of r.data.items) {
            if (it && it.id) map[String(it.id)] = it.body;
          }
            for (let i=0;i<articles.value.length;i++) {
              const a = articles.value[i];
              const bid = String(a.id);
              if (map[bid] && !a.body) {
                articles.value[i] = { ...a, body: map[bid] };
              }
            }
          return;
        }
      } catch (_) {
        // fallback to sequential fetchArticle
      }
      for (const id of ids) {
        await fetchArticle(id);
      }
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'bulk body fetch failed';
    }
  }

  function queueBodyFetch(id: string|number) {
    const sid = String(id);
    // 이미 본문 있으면 skip
    const existing = articles.value.find(a=>String(a.id)===sid);
    if (existing && existing.body) return;
    pendingBodyIds.add(sid);
    if (bodyDebounceTimer) clearTimeout(bodyDebounceTimer);
    bodyDebounceTimer = setTimeout(flushBodyQueue, bodyDebounceMs);
  }

  function mergeDelta(newItems: NewsArticle[]) {
    if (!newItems.length) return;
    // 현재 기사 id set
    const existingIds = new Set(articles.value.map(a => String(a.id)));
    const fresh: NewsArticle[] = [];
    for (const it of newItems) {
      if (!it || it.id == null) continue;
      const sid = String(it.id);
      if (existingIds.has(sid)) continue; // 중복 skip
      fresh.push(it);
    }
    if (!fresh.length) return;
    // 최신순 정렬 (가정: 서버는 desc 이지만 안전하게 재정렬)
    fresh.sort((a,b)=> (b.published_ts||0)-(a.published_ts||0));
    // prepend 후 길이 제한
    articles.value = [...fresh, ...articles.value];
    if (articles.value.length > maxArticles) {
      articles.value = articles.value.slice(0, maxArticles);
    }
  }

  async function fetchRecent() {
    try {
      // since_ts 기반 delta 시도
      const params: string[] = ['limit=100','summary_only=1'];
      if (lastLatestTs.value) {
        params.push(`since_ts=${lastLatestTs.value}`);
      }
      const r = await http.get(`/api/news/recent?${params.join('&')}`);
      const data = r.data || {};
      const items: NewsArticle[] = Array.isArray(data.items) ? data.items : [];
      const delta: boolean = !!data.delta;
      if (!lastLatestTs.value || !delta) {
        // 초기 로드 또는 delta 아님: 전체 재정렬 교체
        articles.value = items.sort((a:NewsArticle,b:NewsArticle)=> (b.published_ts||0)-(a.published_ts||0));
      } else {
        mergeDelta(items);
      }
      // latest_ts 갱신
      if (typeof data.latest_ts === 'number') {
        if (!lastLatestTs.value || data.latest_ts > lastLatestTs.value) {
          lastLatestTs.value = data.latest_ts;
        }
      } else if (articles.value.length) {
        // fallback 계산
        lastLatestTs.value = Math.max(...articles.value.map(a=>a.published_ts||0));
      }
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'fetch recent failed';
    }
  }

  async function fetchArticle(id: number|string) {
    try {
      const r = await http.get(`/api/news/article/${id}`);
      const art = r.data?.article;
      if (art && art.id) {
        const idx = articles.value.findIndex(a => String(a.id) === String(id));
        if (idx >= 0) {
          articles.value[idx] = { ...articles.value[idx], body: art.body };
        } else {
          articles.value.unshift(art);
        }
      }
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'fetch article failed';
    }
  }

  async function fetchStatus() {
    try {
      const r = await http.get('/api/news/status');
      // Backend now flattens status fields at top-level
      const d = r.data || {};
      const normalized: NewsStatus = {
        running: d.running,
        fetch_runs: d.fetch_runs,
        fetch_errors: d.fetch_errors,
        articles_ingested: d.articles_ingested,
        lag_seconds: d.lag_seconds,
        last_run_ts: d.last_run_ts,
        last_ingested_ts: d.last_ingested_ts,
        fast_startup_skipped: d.fast_startup_skipped,
        poll_interval: d.poll_interval,
        active_sources: Array.isArray(d.active_sources)? d.active_sources: undefined,
      };
      status.value = normalized;
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'fetch status failed';
    }
  }

  async function fetchHealth() {
    try {
      const r = await http.get('/api/news/feeds/health');
      const arr = r.data?.feeds;
      if (Array.isArray(arr)) health.value = arr;
    } catch (_) { /* silent */ }
  }

  async function fetchDedupHistory() {
    try {
      const r = await http.get('/api/news/feeds/dedup_history');
      const hist = r.data?.history;
      if (hist && typeof hist === 'object') {
        dedupHistory.value = hist;
      }
    } catch (_) { /* silent */ }
  }

  async function manualRefresh() {
    loading.value = true; error.value = null;
    try {
      await http.post('/api/news/refresh');
      await Promise.all([fetchStatus(), fetchRecent()]);
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'refresh failed';
    } finally {
      loading.value = false;
    }
  }

  async function manualFetchImmediate() {
    // new manual ingestion trigger (fetch/manual)
    try {
      await http.post('/api/news/fetch/manual');
      await fetchRecent();
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'manual fetch failed';
    }
  }

  async function backfill(days: number) {
    if (days <= 0) return;
    try {
      await http.post(`/api/news/backfill?days=${days}`);
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'backfill failed';
    }
  }

  async function fetchSummary(windowMinutes = 1440) {
    summaryLoading.value = true; error.value = null;
    try {
      const r = await http.get(`/api/news/summary?window_minutes=${windowMinutes}`);
      summary.value = r.data || null;
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'fetch summary failed';
    } finally {
      summaryLoading.value = false;
    }
  }

  async function fetchSources(includeHistory: boolean = true, force: boolean = false) {
    if (sourcesLoading.value) return;
    const now = Date.now();
    if (!force && sourcesLoadedAt.value && (now - sourcesLoadedAt.value) < 60000) {
      return; // cache 60s
    }
    sourcesLoading.value = true;
    try {
      const r = await http.get(`/api/news/sources?include_history=${includeHistory? '1':'0'}`);
      if (r.data) {
        sources.value = r.data.sources || [];
        dedupHistory.value = r.data.dedup_history || dedupHistory.value;
        sourcesLoadedAt.value = now;
      }
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'fetch sources failed';
    } finally {
      sourcesLoading.value = false;
    }
  }

  async function searchArticles(q: string, days = 7) {
    searchQuery.value = q;
    if (!q || q.trim().length < 2) {
      searchResults.value = [];
      return;
    }
    searchLoading.value = true; searchError.value = null;
    try {
      const r = await http.get(`/api/news/search?q=${encodeURIComponent(q)}&days=${days}&limit=200`);
      const arts = r.data?.articles;
      if (Array.isArray(arts)) {
        searchResults.value = arts;
        searchLastExecuted.value = Date.now();
      } else {
        searchResults.value = [];
      }
    } catch (e:any) {
      searchError.value = e.__friendlyMessage || e.message || 'search failed';
    } finally {
      searchLoading.value = false;
    }
  }

  function startPolling() {
    stopPolling();
    if (auto.value) {
      timer = setInterval(async () => {
        await fetchStatus();
        // 검색 활성 시 최근 기사 자동 갱신을 임시로 skip (검색 결과 덮어쓰기 방지)
        const searchActive = searchQuery.value.trim().length >= 2 && searchResults.value.length > 0;
        if (!searchActive) {
          await fetchRecent();
        }
        fetchHealth();
        fetchDedupHistory();
      }, Math.max(5, intervalSec.value)*1000);
    }
  }
  function stopPolling() { if (timer) clearInterval(timer); timer = null; }
  function toggleAuto(v?: boolean) { auto.value = typeof v==='boolean'?v:!auto.value; startPolling(); }
  function setIntervalSec(v: number) { intervalSec.value = v; startPolling(); }
  function clearArticles() { articles.value = []; lastLatestTs.value = null; }

  // expose queueBodyFetch for view usage (expand 시 사용)

  return { articles, status, loading, error, auto, intervalSec, lastLatestTs, health, dedupHistory,
    summary, summaryLoading, sources, sourcesLoading, sourcesLoadedAt,
    searchQuery, searchResults, searchLoading, searchError, searchLastExecuted,
    fetchRecent, fetchStatus, fetchHealth, fetchDedupHistory, manualRefresh, manualFetchImmediate, backfill, fetchSummary, fetchSources, searchArticles,
    startPolling, stopPolling, toggleAuto, setIntervalSec, clearArticles, fetchArticle, queueBodyFetch };
});
