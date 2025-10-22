<template>
  <div class="space-y-4">
    <div class="flex flex-wrap gap-4 items-center">
      <div class="flex items-center gap-2 text-xs text-neutral-400">
        <span class="font-semibold text-neutral-200">Status:</span>
        <span :class="statusColor">{{ statusLabel }}</span>
        <span v-if="store.status.last_run_ts" class="text-[10px] text-neutral-500">(last {{ lastRunAge }}, interval {{ store.status.poll_interval || 'n/a' }}s)</span>
      </div>
      <div v-if="store.status.fast_startup_skipped" class="flex items-center gap-2 text-xs">
        <span class="px-2 py-0.5 rounded bg-neutral-700/60 border border-neutral-600 text-neutral-300">FAST_STARTUP Skipped</span>
        <button class="btn-xs" :disabled="starting" @click="startNews">
          <span v-if="starting" class="animate-pulse">Starting...</span>
          <span v-else>Start News Only</span>
        </button>
      </div>
      <div class="flex items-center gap-2 text-xs">
        <button class="btn-xs" @click="manualRefresh" :disabled="loading">Manual Refresh</button>
        <button class="btn-xs" @click="store.toggleAuto()" :class="store.auto? 'bg-neutral-700':'bg-neutral-800'">
          {{ store.auto ? 'Auto: On' : 'Auto: Off' }}
        </button>
        <select v-model.number="store.intervalSec" @change="store.startPolling()" class="bg-neutral-900 text-xs px-2 py-1 rounded border border-neutral-700">
          <option :value="5">5s</option>
          <option :value="10">10s</option>
          <option :value="30">30s</option>
          <option :value="60">60s</option>
        </select>
      </div>
      <div class="flex items-center gap-3 text-xs">
        <div class="flex items-center gap-1">
          <span class="text-neutral-400">Sentiment</span>
          <label class="flex items-center gap-1 cursor-pointer">
            <input type="checkbox" value="pos" v-model="sentimentSelected" @change="persistSentimentMulti" class="accent-emerald-500 w-3 h-3" />
            <span class="text-[10px] text-emerald-300">Pos</span>
          </label>
          <label class="flex items-center gap-1 cursor-pointer">
            <input type="checkbox" value="neg" v-model="sentimentSelected" @change="persistSentimentMulti" class="accent-rose-500 w-3 h-3" />
            <span class="text-[10px] text-rose-300">Neg</span>
          </label>
          <label class="flex items-center gap-1 cursor-pointer">
            <input type="checkbox" value="neu" v-model="sentimentSelected" @change="persistSentimentMulti" class="accent-amber-500 w-3 h-3" />
            <span class="text-[10px] text-amber-300">Neu</span>
          </label>
          <button class="btn-xs !px-1.5 !text-[10px]" @click="resetSentimentMulti" :disabled="sentimentSelected.length===3" title="Reset to All">All</button>
        </div>
        <span class="text-[10px] text-neutral-500" v-if="sentimentSelected.length!==3">Filtered: <span class="text-neutral-300">{{ filteredArticles.length }}</span>/<span>{{ store.articles.length }}</span></span>
      </div>
      <!-- 검색 입력 (스토어 searchArticles 연동) -->
      <div class="flex items-center gap-1 text-xs">
        <input v-model="searchTerm" @input="onSearchInput" type="text" placeholder="coin, BTC, ETH, XRP, 비트코인 (2+ chars)" class="bg-neutral-900 text-[10px] px-2 py-1 rounded border border-neutral-700 focus:outline-none focus:border-brand-accent w-56" />
        <button v-if="searchTerm" class="btn-xs !px-1.5 !text-[10px]" @click="clearSearch" :title="'검색어 지우기'">✕</button>
        <span v-if="showSearchHits" class="text-[10px] text-neutral-500">{{ searchHitCount }} hits</span>
        <span v-else-if="searchMode && searchQueryMatches && !store.searchLoading" class="text-[10px] text-neutral-500">0 hits</span>
        <span v-if="store.searchLoading" class="text-[10px] text-neutral-400 animate-pulse">검색중...</span>
        <span v-else-if="store.searchError" class="text-[10px] text-brand-danger">{{ store.searchError }}</span>
      </div>
      <!-- 코인 키워드 퀵버튼 -->
      <div class="flex flex-wrap gap-1 text-[10px]" aria-label="coin keywords">
        <button v-for="c in coinKeywords" :key="c" class="px-1.5 py-0.5 rounded border border-neutral-700 bg-neutral-800 hover:border-brand-accent transition" @click="appendKeyword(c)" :title="c + ' 추가'">{{ c }}</button>
      </div>
      <div v-if="activeTokens.length" class="flex flex-wrap gap-1 text-[10px]">
        <button v-for="t in activeTokens" :key="t.key" @click="toggleToken(t.text)" :class="['px-1.5 py-0.5 rounded border transition', t.disabled? 'bg-neutral-800 border-neutral-700 text-neutral-500 line-through':'bg-neutral-700/60 border-neutral-600 text-neutral-200 hover:border-brand-accent']" :title="t.disabled? '클릭하여 다시 활성화':'클릭하여 비활성화'">
          {{ t.text }}
        </button>
      </div>
      <div class="ml-auto flex items-center gap-2 text-[10px] text-neutral-500">
        <span>Articles: <span class="text-neutral-300">{{ displayArticles.length }}</span></span>
        <span v-if="store.status.lag_seconds!=null">Lag: <span :class="lagClass">{{ store.status.lag_seconds.toFixed(1) }}s</span></span>
        <span v-if="sentiment.avg !== null">Sentiment: <span :class="sentimentClass">{{ sentiment.avg.toFixed(2) }}</span></span>
        <div class="flex items-center gap-1">
          <span class="text-neutral-400">Win</span>
          <input type="range" min="5" max="240" step="5" v-model.number="windowMinutes" @input="persistWindow" class="w-24" />
          <span class="text-neutral-300 w-8 text-right">{{ windowMinutes }}m</span>
        </div>
      </div>
    </div>

    <div class="grid md:grid-cols-3 gap-4">
      <div class="md:col-span-2 space-y-3">
        <div v-if="loading" class="text-xs text-neutral-500">Loading...</div>
        <div v-else-if="searchPending" class="text-xs text-neutral-500">Waiting for search results...</div>
        <div v-else-if="searchEmpty" class="text-xs text-neutral-500">No search results for "{{ searchTermTrimmed }}"</div>
        <div v-else-if="displayArticles.length===0" class="text-xs text-neutral-500">No articles yet.</div>
        <div v-if="error" class="text-xs text-brand-danger">Error: {{ error }}</div>
        <ul class="space-y-2">
          <li v-for="a in displayArticles" :key="a.id" class="p-3 rounded border border-neutral-800 bg-neutral-900/60 hover:border-neutral-700 transition cursor-pointer" @click="toggleExpand(a)" :title="isExpanded(a) ? '클릭하여 접기' : '클릭하여 전체 보기'">
            <div class="text-xs text-neutral-500 flex justify-between items-center mb-1">
              <span class="flex items-center gap-2">
                <span>{{ formatTs(a.published_ts) }}</span>
                <span v-if="a.sentiment!=null" :class="['px-1.5 py-0.5 rounded text-[10px] font-mono', sentimentBadgeClass(a.sentiment)]" :title="'sentiment: '+a.sentiment.toFixed(2)">
                  {{ sentimentBadgeLabel(a.sentiment) }}
                </span>
              </span>
              <span class="font-mono text-[10px] text-neutral-600">{{ a.source }}</span>
            </div>
            <!-- highlightText() escapes input and only injects a safe <span> wrapper; sanitized via directive -->
            <div class="text-sm font-medium text-neutral-200 leading-snug" v-safe-html="highlightText(a.title)"></div>
            <div v-if="!isExpanded(a) && a.summary" class="text-xs text-neutral-400 mt-1 line-clamp-2" v-safe-html="highlightText(a.summary)"></div>
            <div v-if="isExpanded(a)" class="mt-2 text-[11px] text-neutral-300 whitespace-pre-wrap">
              <div v-if="a.body" v-safe-html="highlightText(a.body)"></div>
              <div v-else class="italic text-neutral-500 flex flex-col gap-1">
                <span>본문 없음</span>
                <a v-if="a.url" :href="a.url" target="_blank" rel="noopener" class="text-brand-accent underline">원문 열기 ↗</a>
              </div>
            </div>
          </li>
        </ul>
      </div>
      <div class="space-y-3">
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60">
          <div class="text-xs font-semibold text-neutral-300 mb-2">Ingestion Stats</div>
          <dl class="text-[11px] space-y-1">
            <div class="flex justify-between"><dt class="text-neutral-500">Fetch Runs</dt><dd>{{ store.status.fetch_runs ?? '-' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Errors</dt><dd :class="{'text-brand-danger': (store.status.fetch_errors||0)>0}">{{ store.status.fetch_errors ?? '-' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Articles Ingested</dt><dd>{{ store.status.articles_ingested ?? '-' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Lag (s)</dt><dd>{{ store.status.lag_seconds?.toFixed(1) ?? '-' }}</dd></div>
            <div class="flex justify-between"><dt class="text-neutral-500">Running</dt><dd>{{ store.status.running ? 'yes':'no' }}</dd></div>
          </dl>
        </div>
        <!-- 소스 헬스 (신규 /api/news/sources) -->
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60" v-if="store.sources.length">
          <div class="flex justify-between items-center mb-2">
            <div class="text-xs font-semibold text-neutral-300">Source Health</div>
            <button class="btn-xs !text-[10px]" @click="refreshSources" :disabled="store.sourcesLoading">↻</button>
          </div>
          <ul class="space-y-1 max-h-64 overflow-auto pr-1">
            <li v-for="s in store.sources" :key="s.source" class="text-[11px] flex flex-col gap-0.5" :title="sourceTooltip(s)">
              <div class="flex justify-between items-center">
                <span class="font-mono text-neutral-400 flex items-center gap-1">
                  <span>{{ s.source }}</span>
                  <span v-if="s.stalled" class="px-1 py-0.5 rounded bg-rose-600/20 text-rose-300 border border-rose-600/30 text-[9px] tracking-wide">STALL</span>
                  <span v-else-if="s.dedup_anomaly" class="px-1 py-0.5 rounded bg-amber-600/20 text-amber-300 border border-amber-600/30 text-[9px] tracking-wide">ANOM</span>
                </span>
                <div class="flex items-center gap-2">
                  <svg v-if="dedupSpark(s.source).length" :width="60" :height="14" viewBox="0 0 60 14">
                    <polyline :points="dedupSpark(s.source)" fill="none" stroke="#22c55e" stroke-width="1.1" stroke-linejoin="round" stroke-linecap="round" />
                  </svg>
                  <span :class="healthScoreClass(s.score)">{{ s.score!=null? s.score.toFixed(2):'--' }}</span>
                </div>
              </div>
              <div class="w-full h-1.5 bg-neutral-800 rounded overflow-hidden">
                <div class="h-full" :style="healthBarStyle(s.score)" />
              </div>
              <div class="flex justify-between text-[10px] text-neutral-500">
                <span v-if="s.backoff_remaining>0" class="text-amber-400">backoff {{ s.backoff_remaining.toFixed(0) }}s</span>
                <span v-else class="text-neutral-600">ok</span>
                <span class="text-neutral-600" v-if="s.dedup_last_ratio!=null">ratio {{ (s.dedup_last_ratio*100).toFixed(0) }}%</span>
              </div>
            </li>
          </ul>
        </div>
        <!-- Summary 패널 -->
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60" v-if="store.summary">
          <div class="flex justify-between items-center mb-2">
            <div class="text-xs font-semibold text-neutral-300">Summary ({{ store.summary.total }} / {{ store.summary.window_minutes }}m)</div>
            <button class="btn-xs !text-[10px]" @click="refreshSummary" :disabled="store.summaryLoading">↻</button>
          </div>
          <div class="text-[11px] space-y-2">
            <div v-if="store.summary.sentiment" class="flex flex-wrap gap-2">
              <span class="text-neutral-500">avg: <span class="text-neutral-300" v-if="store.summary.sentiment.avg!=null">{{ store.summary.sentiment.avg.toFixed(2) }}</span><span v-else>--</span></span>
              <span class="text-neutral-500">std: <span class="text-neutral-300" v-if="store.summary.sentiment.std!=null">{{ store.summary.sentiment.std.toFixed(2) }}</span><span v-else>--</span></span>
              <span class="text-emerald-400">pos {{ store.summary.sentiment.pos }}</span>
              <span class="text-rose-400">neg {{ store.summary.sentiment.neg }}</span>
              <span class="text-amber-400">neu {{ store.summary.sentiment.neutral }}</span>
            </div>
            <div v-if="store.summary.sources && store.summary.sources.length" class="space-y-1">
              <div v-for="s in store.summary.sources.slice(0,6)" :key="s.source" class="flex items-center gap-2">
                <span class="font-mono text-[10px] text-neutral-500 w-20 truncate">{{ s.source }}</span>
                <div class="h-2 bg-neutral-800 rounded w-full relative overflow-hidden">
                  <div class="h-full bg-brand-accent/60" :style="{ width: (s.ratio*100).toFixed(1)+'%' }"></div>
                </div>
                <span class="text-[10px] text-neutral-400 w-8 text-right">{{ s.count }}</span>
              </div>
            </div>
          </div>
        </div>
        <div class="p-3 rounded border border-neutral-800 bg-neutral-900/60">
          <div class="text-xs font-semibold text-neutral-300 mb-2">Manual Controls</div>
          <button class="btn-xs w-full mb-2" @click="manualRefresh" :disabled="loading">Legacy Refresh</button>
          <button class="btn-xs w-full mb-2" @click="manualFetchImmediate" :disabled="loading">Manual Fetch (Immediate)</button>
          <div class="flex items-center gap-2 mb-2 text-[10px]">
            <input v-model.number="backfillDays" type="number" min="1" max="30" class="w-14 px-1 py-0.5 bg-neutral-900 border border-neutral-700 rounded" />
            <button class="btn-xs" @click="runBackfill" :disabled="backfillRunning">Backfill</button>
            <span v-if="backfillRunning" class="text-neutral-500 animate-pulse">...</span>
          </div>
          <div class="flex items-center gap-2 text-[10px]">
            <button class="btn-xs" @click="refreshSources" :disabled="store.sourcesLoading">Sources</button>
            <button class="btn-xs" @click="refreshSummary" :disabled="store.summaryLoading">Summary</button>
            <button class="btn-xs" @click="clearSearch" :disabled="!searchTerm">Clear Search</button>
          </div>
          <button class="btn-xs w-full" @click="store.clearArticles" :disabled="loading">Clear Articles</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useNewsStore } from '../stores/news';
import http from '../lib/http';
import { MemoCache, sigParts } from '../lib/memoCache';
import { getUiPref, setUiPref } from '../lib/uiSettings';

const store = useNewsStore();
const loading = computed(()=>store.loading);
const error = computed(()=>store.error);

function manualRefresh() { store.manualRefresh(); }
function formatTs(ts?: number) {
  if (!ts) return '-';
  const d = new Date(ts*1000);
  return d.toLocaleString();
}

const statusLabel = computed(()=> {
  const r = store.status.running;
  const last = store.status.last_run_ts;
  const interval = store.status.poll_interval || 90;
  const recent = last ? (Date.now()/1000 - last) < (interval * 2.2) : false;
  return (r || recent) ? 'running' : 'stopped';
});
const statusColor = computed(()=> statusLabel.value === 'running' ? 'text-brand-accent' : 'text-neutral-500');
const lastRunAge = computed(()=> {
  const ts = store.status.last_run_ts; if (!ts) return 'n/a';
  const age = Date.now()/1000 - ts; if (age < 60) return age.toFixed(0)+'s ago';
  if (age < 3600) return Math.floor(age/60)+'m ago';
  return Math.floor(age/3600)+'h ago';
});
const lagClass = computed(()=> {
  const v = store.status.lag_seconds; if (v==null) return 'text-neutral-400';
  if (v > 300) return 'text-brand-danger'; if (v>120) return 'text-amber-400'; return 'text-brand-accent';
});

// Partial start for news ingestion only
const starting = ref(false);
async function startNews() {
  if (starting.value) return;
  starting.value = true;
  try {
    await http.post('/admin/fast_startup/start_component?component=news_ingestion');
    await Promise.all([
      store.fetchStatus(),
      store.fetchSources(true, true),
      store.fetchRecent(),
    ]);
  } catch {
    // no-op
  } finally {
    starting.value = false;
  }
}

onMounted(()=>{
  store.fetchStatus();
  store.fetchRecent();
  store.fetchHealth(); // legacy health (optional)
  store.fetchSources();
  store.fetchSummary();
  store.startPolling();
  fetchSentiment();
  sentimentTimer = setInterval(fetchSentiment, 60000);
});

// --- Sentiment aggregation (last 60m) ---
interface SentimentAgg { avg: number|null; count: number; window_minutes: number; pos: number; neg: number; neutral: number; }
const sentiment = computed(()=>sentimentState.value);
const sentimentState = ref<SentimentAgg>({ avg: null, count: 0, window_minutes: 60, pos:0, neg:0, neutral:0 });
let sentimentTimer: any = null;
async function fetchSentiment() {
  try {
    const r = await http.get('/api/news/sentiment?window=60');
    const d = r.data || {};
    sentimentState.value = {
      avg: typeof d.avg === 'number' ? d.avg : null,
      count: d.count || 0,
      window_minutes: d.window_minutes || 60,
      pos: d.pos || 0,
      neg: d.neg || 0,
      neutral: d.neutral || 0,
    };
  } catch {
    // silent
  }
}
const sentimentClass = computed(()=> {
  const v = sentiment.value.avg; if (v===null) return 'text-neutral-400';
  if (v > 0.4) return 'text-brand-accent'; if (v > 0.15) return 'text-amber-400'; if (v < -0.4) return 'text-brand-danger'; if (v < -0.15) return 'text-amber-400'; return 'text-neutral-300';
});

function sentimentBadgeClass(v:number){
  if (v > 0.4) return 'bg-emerald-600/20 text-emerald-300 border border-emerald-600/30';
  if (v > 0.15) return 'bg-amber-500/20 text-amber-300 border border-amber-500/30';
  if (v < -0.4) return 'bg-rose-600/20 text-rose-300 border border-rose-600/30';
  if (v < -0.15) return 'bg-amber-600/20 text-amber-300 border border-amber-600/30';
  return 'bg-neutral-700/40 text-neutral-300 border border-neutral-600/40';
}
function sentimentBadgeLabel(v:number){
  if (v > 0.4) return 'POS';
  if (v > 0.15) return 'pos';
  if (v < -0.4) return 'NEG';
  if (v < -0.15) return 'neg';
  return 'NEU';
}

// --- Sentiment multi-select filter ---
// 선택된 sentiment 타입 목록 (기본: 모두)
const sentimentSelected = ref<string[]>(['pos','neg','neu']);
async function persistSentimentMulti(){
  try { await setUiPref('news_sentiment_multi', sentimentSelected.value); } catch {}
}
function resetSentimentMulti(){ sentimentSelected.value = ['pos','neg','neu']; persistSentimentMulti(); }
// 기존 단일 select 저장값 마이그레이션 후 multi 사용
;(async () => {
  try {
    const arr = await getUiPref<any[]>('news_sentiment_multi');
    if (Array.isArray(arr) && arr.every(x=>['pos','neg','neu'].includes(x))) {
      const uniq = Array.from(new Set(arr.filter(x=>['pos','neg','neu'].includes(x))));
      if (uniq.length) sentimentSelected.value = uniq as any;
    } else {
      // legacy fallback
      try {
        const legacy = await getUiPref<string>('news_sentiment_filter');
        if (legacy === 'pos') sentimentSelected.value = ['pos'];
        else if (legacy === 'neg') sentimentSelected.value = ['neg'];
        else if (legacy === 'neu') sentimentSelected.value = ['neu'];
        else sentimentSelected.value = ['pos','neg','neu'];
      } catch {}
    }
  } catch {}
})();

// 검색어/토큰 상태 (ref 포함) 선행 정의
const searchTerm = ref('');
const disabledTokens = ref<Set<string>>(new Set());

// ---- Memoization for filtering pipeline ----
const filterCache = new MemoCache<any[]>(200);
const filteredArticles = computed(()=> {
  const activeSent = sentimentSelected.value.length ? sentimentSelected.value : ['pos','neg','neu'];
  const qRaw = searchTerm.value.trim();
  const disabled = Array.from(disabledTokens.value).sort();
  const articleSig = store.articles.length ? store.articles[0].id + ':' + store.articles.length + ':' + (store.articles[0].published_ts||'') : 'empty';
  const sig = sigParts([articleSig, activeSent, qRaw, disabled]);
  const cached = filterCache.get('filtered', sig);
  if (cached) return cached;
  // compute
  let base = store.articles.filter((a:any) => {
    if (a.sentiment == null) return activeSent.length === 3; // allow only if all selected
    const v = a.sentiment as number;
    const isPos = v > 0.15; const isNeg = v < -0.15; const isNeu = !isPos && !isNeg;
    return (isPos && activeSent.includes('pos')) || (isNeg && activeSent.includes('neg')) || (isNeu && activeSent.includes('neu'));
  });
  if (qRaw) {
    const tokens = qRaw.split(/\s+/).filter(t => t.length > 1).map(t=>t.toLowerCase());
    if (tokens.length) {
      base = base.filter((a:any) => {
        const hay = ((a.title||'') + ' ' + (a.summary||'') + ' ' + (a.body||'')).toLowerCase();
        return tokens.every(tok => disabledTokens.value.has(tok) || hay.includes(tok));
      });
    }
  }
  filterCache.set('filtered', sig, base);
  return base;
});

// --- 시간 창(window) 필터: 최근 N분만 표시 (UI는 슬라이더) ---
const windowMinutes = ref(60);
async function persistWindow(){
  try { await setUiPref('news_window_minutes', Number(windowMinutes.value)); } catch {}
}
;(async () => {
  try {
    const wm = await getUiPref<number>('news_window_minutes');
    if (typeof wm === 'number') {
      const n = parseInt(String(wm), 10); if (!isNaN(n) && n>=5 && n<=240) windowMinutes.value = n;
    }
  } catch {}
})();

// filteredArticles 재정의: 시간 창 필터를 sentiment/검색 이전 단계로 적용하기 위해 래핑
const filteredArticlesRaw = filteredArticles; // 기존 계산 유지
const filteredArticlesTimeApplied = computed(()=> {
  const nowSec = Date.now()/1000;
  const cutoff = nowSec - windowMinutes.value * 60;
  return filteredArticlesRaw.value.filter((a:any)=>{
    if (!a.published_ts) return false; // timestamp 없으면 제외
    return a.published_ts >= cutoff;
  });
});

// --- 검색 상태 관리 ---
const MIN_SEARCH_LENGTH = 2;
const searchTermTrimmed = computed(()=> searchTerm.value.trim());
const searchMode = computed(()=> searchTermTrimmed.value.length >= MIN_SEARCH_LENGTH);
const storeSearchQueryTrimmed = computed(()=> store.searchQuery.trim());
const searchQueryMatches = computed(()=> searchMode.value && storeSearchQueryTrimmed.value.toLowerCase() === searchTermTrimmed.value.toLowerCase());
const searchPending = computed(()=> searchMode.value && (store.searchLoading || !searchQueryMatches.value));
const showSearchHits = computed(()=> searchQueryMatches.value && store.searchResults.length > 0);
const searchHitCount = computed(()=> showSearchHits.value ? store.searchResults.length : 0);
const searchEmpty = computed(()=> searchQueryMatches.value && !store.searchLoading && store.searchResults.length === 0);
const displayArticles = computed(()=> {
  if (!searchMode.value) return filteredArticlesTimeApplied.value;
  if (!searchQueryMatches.value) return [];
  return store.searchResults;
});

const DISABLED_TOKENS_KEY = 'news_disabled_tokens';
interface TokenChip { text: string; disabled: boolean; key: string; }
const activeTokens = computed<TokenChip[]>(()=>{
  const q = searchTermTrimmed.value;
  if (!q) return [];
  const toks = Array.from(new Set(q.split(/\s+/).filter(t=>t.length>1).map(t=>t.toLowerCase())));
  return toks.map(t=> ({ text: t, disabled: disabledTokens.value.has(t), key: t }));
});
function toggleToken(tok:string){
  const t = tok.toLowerCase();
  if (disabledTokens.value.has(t)) disabledTokens.value.delete(t); else disabledTokens.value.add(t);
  disabledTokens.value = new Set(disabledTokens.value);
  persistDisabledTokens();
}
async function persistDisabledTokens(){
  try { await setUiPref(DISABLED_TOKENS_KEY, Array.from(disabledTokens.value)); } catch {}
}
function pruneDisabledTokens(){
  const current = new Set(activeTokens.value.map(t=>t.text));
  let changed = false;
  for (const tok of Array.from(disabledTokens.value)){
    if (!current.has(tok)) { disabledTokens.value.delete(tok); changed = true; }
  }
  if (changed) {
    disabledTokens.value = new Set(disabledTokens.value);
    persistDisabledTokens();
  }
}

// --- Source / Summary Refresh Helpers ---
const refreshSources = () => {
  // Force cast to any to bypass mismatched d.ts inference in template environment
  (store as any).fetchSources(true, true);
};
const refreshSummary = () => {
  store.fetchSummary(windowMinutes.value);
};
const manualFetchImmediate = () => {
  store.manualFetchImmediate();
};

// --- Backfill Controls ---
const backfillDays = ref(3);
const backfillRunning = ref(false);
async function runBackfill(){
  if (backfillRunning.value) return;
  backfillRunning.value = true;
  try {
    await store.backfill(backfillDays.value);
    await store.fetchRecent();
    await store.fetchSummary(windowMinutes.value);
  } finally {
    backfillRunning.value = false;
  }
}

// --- Source Tooltip & Dedup Sparkline ---
function sourceTooltip(s: any){
  const parts:string[] = [];
  if (s.stalled) parts.push('stalled');
  if (s.dedup_anomaly) parts.push('dedup anomaly');
  if (s.backoff_remaining>0) parts.push(`backoff ${s.backoff_remaining.toFixed(0)}s`);
  if (s.dedup_last_ratio!=null) parts.push(`last ratio ${(s.dedup_last_ratio*100).toFixed(1)}%`);
  if (s.score!=null) parts.push(`score ${s.score.toFixed(2)}`);
  return parts.join(' | ');
}

function dedupSpark(source: string): string {
  const entry:any = store.sources.find((x:any)=>x.source===source);
  const hist:number[] = entry?.dedup_history || entry?.history || [];
  if (!hist.length) return '';
  const values = hist.slice(-20);
  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const h = 14; const w = 60; const n = values.length;
  if (n===1) return `0,${(h/2).toFixed(1)}`;
  const step = w/(n-1);
  const pts = values.map((v,i)=>{
    const norm = (v - min) / (max - min || 1);
    const y = h - norm * h;
    const x = i * step;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return pts;
}

// React to window change -> refresh summary window-based stats
watch(()=>windowMinutes.value, () =>{
  // debounce simple
  setTimeout(()=> refreshSummary(), 50);
});

// --- Expandable article body ---
const expandedIds = ref<Set<string>>(new Set());
function isExpanded(a:any){ return expandedIds.value.has(String(a.id)); }
function toggleExpand(a:any){
  const id = String(a.id);
  if (expandedIds.value.has(id)) {
    expandedIds.value.delete(id);
  } else {
    expandedIds.value.add(id);
    if (!a.body) {
      // 배치 본문 요청 큐 사용
      store.queueBodyFetch(a.id);
    }
  }
}

const coinKeywords = ['BTC','ETH','XRP','SOL','DOGE','BNB','비트코인','이더리움'];
function appendKeyword(k: string){
  if (!searchTerm.value.includes(k)) {
    if (searchTerm.value && !searchTerm.value.endsWith(' ')) searchTerm.value += ' ';
    searchTerm.value += k + ' ';
    onSearchInput();
  }
}
let searchDebounce: any = null;
function onSearchInput(){
  persistSearch();
  if (searchDebounce) clearTimeout(searchDebounce);
  pruneDisabledTokens();
  const q = searchTermTrimmed.value;
  if (q.length < MIN_SEARCH_LENGTH) {
    store.searchResults = [];
    store.searchQuery = '';
    store.searchError = null;
    return;
  }
  searchDebounce = setTimeout(()=>{
    store.searchArticles(q);
  }, 300);
}
async function persistSearch(){
  try { await setUiPref('news_search_term', searchTerm.value); } catch {}
}
function clearSearch(){
  searchTerm.value='';
  persistSearch();
  if (searchDebounce) {
    clearTimeout(searchDebounce);
    searchDebounce = null;
  }
  store.searchResults = [];
  store.searchQuery = '';
  store.searchError = null;
  if (disabledTokens.value.size) {
    disabledTokens.value = new Set();
    persistDisabledTokens();
  }
}
// 복원 (DB-backed with local fallback)
;(async () => {
  try {
    const st = await getUiPref<string>('news_search_term');
    if (st && st.trim()) {
      searchTerm.value = st;
      onSearchInput();
    } else {
      searchTerm.value = '';
      persistSearch();
    }
  } catch {}
  try {
    const arr = await getUiPref<any[]>(DISABLED_TOKENS_KEY);
    if (Array.isArray(arr)) {
      const valid = arr.map((t:any)=> typeof t === 'string' ? t.toLowerCase() : '').filter(t=>t.length>1);
      if (valid.length) disabledTokens.value = new Set(valid);
    }
  } catch {}
})();

// --- 검색어 하이라이트 ---
function escapeHtml(s:string){
  return s.replace(/[&<>"]|'/g, (c) => {
    switch(c){
      case '&': return '&amp;';
      case '<': return '&lt;';
      case '>': return '&gt;';
      case '"': return '&quot;';
      case "'": return '&#39;';
      default: return c;
    }
  });
}
// Memoization for highlight output per (text, searchSig)
const highlightCache = new MemoCache<string>(500);
function highlightText(raw?: string){
  if (!raw) return '';
  const q = searchTermTrimmed.value;
  if (!q) return escapeHtml(raw);
  const tokens = q.split(/\s+/).filter(t=>t.length>1).map(t=>t.toLowerCase()).filter(t=>!disabledTokens.value.has(t));
  if (!tokens.length) return escapeHtml(raw);
  const uniq = Array.from(new Set(tokens)).sort((a,b)=>b.length - a.length);
  const searchSig = uniq.join(',') + '|' + disabledTokens.value.size;
  const key = raw.slice(0,64); // partial key (본문 길어도 앞부분으로 캐시 식별 충분)
  const sig = key + '|' + searchSig;
  const cached = highlightCache.get(sig, sig);
  if (cached) return cached;
  let escaped = escapeHtml(raw);
  try {
    // Combined alternation regex (순서: 긴 토큰 먼저) → ReDoS 방지 위해 개별 토큰 escape 후 단순 alternation만 사용
    const pattern = uniq.map(t=>t.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|');
    const re = new RegExp('('+pattern+')','gi');
    escaped = escaped.replace(re, '<span class="highlight">$1</span>');
  } catch{ /* 실패시 하이라이트 없이 원문 반환 */ }
  highlightCache.set(sig, sig, escaped);
  return escaped;
}

// --- Health score helpers ---
function healthScoreClass(v:number|undefined){
  if (v==null) return 'text-neutral-500';
  if (v >= 0.8) return 'text-emerald-400';
  if (v >= 0.6) return 'text-emerald-300';
  if (v >= 0.4) return 'text-amber-400';
  if (v >= 0.2) return 'text-rose-400';
  return 'text-rose-500';
}
function healthBarStyle(v:number|undefined){
  if (v==null) return { width: '0%', backgroundColor: '#444' };
  const pct = Math.max(0, Math.min(1, v))*100;
  let color = '#22c55e';
  if (v < 0.2) color = '#dc2626';
  else if (v < 0.4) color = '#f87171';
  else if (v < 0.6) color = '#f59e0b';
  else if (v < 0.8) color = '#4ade80';
  return { width: pct+'%', backgroundColor: color, transition: 'width 0.4s ease' };
}

// --- Recent drop / recover transient indicators ---
const lastScores = ref<Record<string, number|null|undefined>>({});
const lastChangeTs = ref<Record<string, number>>({});

function updateHealthChanges(){
  const now = Date.now()/1000;
  for (const f of store.health){
    const prev = lastScores.value[f.source];
    const cur = f.score;
    if (cur==null){
      continue; // 아직 데이터 없음
    }
    // 임계치 (backend 와 동일 0.3) - UI 하드코딩 (추후 store 노출 고려)
    const th = 0.3;
    const wasHealthy = prev!=null ? prev >= th : undefined;
    const isHealthy = cur >= th;
    if (wasHealthy === undefined){
      lastScores.value[f.source] = cur; continue;
    }
    if (wasHealthy !== isHealthy){
      lastChangeTs.value[f.source] = now;
    }
    lastScores.value[f.source] = cur;
  }
}

let healthChangeTimer: any = setInterval(()=>{ updateHealthChanges(); }, 5000);

onUnmounted(()=>{
  try { if (sentimentTimer) clearInterval(sentimentTimer); } catch {}
  try { if (healthChangeTimer) clearInterval(healthChangeTimer); } catch {}
});
</script>

<style scoped>
 /* .btn-xs utility assumed globally available; removed local @apply causing lint error */
.highlight { background: rgba(250,204,21,0.15); color: #facc15; padding:0 2px; border-radius:2px; }
</style>
