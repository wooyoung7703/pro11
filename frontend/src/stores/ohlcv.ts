import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import http from '@/lib/http';

export interface Candle {
  open_time: number; // ms epoch
  close_time: number; // ms epoch
  open: number; high: number; low: number; close: number;
  volume?: number; trade_count?: number;
  taker_buy_volume?: number; taker_buy_quote_volume?: number;
  is_closed?: boolean;
}

interface OhlcvMetrics {
  pct_change: number|null;
  avg_volume: number|null;
  volatility: number|null;
}

export const useOhlcvStore = defineStore('ohlcv', () => {
  // 심볼 고정: XRPUSDT
  const symbol = ref<string>('XRPUSDT');
  const interval = ref<string>('');
  const limit = ref<number>(120);
  const candles = ref<Candle[]>([]);
  const metrics = ref<OhlcvMetrics>({ pct_change: null, avg_volume: null, volatility: null });
  const completeness365d = ref<number|null>(null);
  const loading = ref(false);
  const error = ref<string|null>(null);
  const auto = ref(true);
  const pollSec = ref(15);
  // gaps status (segments)
  interface GapSegment { from_ts: number; to_ts: number; missing: number; state: 'open'|'closed'; }
  const gapSegments = ref<GapSegment[]>([]);
  const openGapCount = ref(0);
  // Year backfill state
  interface YearBackfillState { status: string; percent?: number; loaded_bars?: number; expected_bars?: number; eta_seconds?: number; elapsed_seconds?: number; }
  const yearBackfill = ref<YearBackfillState|undefined>();
  const yearBackfillPolling = ref(false);
  let yearPollTimer: any = null;
  let timer: any = null;

  function initDefaults(_defSymbol: string, defInterval: string){
    // symbol은 고정되므로 interval만 초기화
    if(!interval.value) interval.value = defInterval;
  }

  async function fetchRecent(opts?: { includeOpen?: boolean; limit?: number }){
    if(!symbol.value || !interval.value) return;
    loading.value = true; error.value = null;
    try {
      const r = await http.get('/api/ohlcv/recent', { params: { symbol: symbol.value, interval: interval.value, limit: (opts?.limit ?? limit.value), include_open: opts?.includeOpen === true ? '1' : undefined }});
      const d = r.data || {};
      candles.value = Array.isArray(d.candles) ? d.candles : [];
      metrics.value = d.metrics || { pct_change: null, avg_volume: null, volatility: null };
    } catch(e:any){
      error.value = e.__friendlyMessage || e.message || 'fetch failed';
    } finally {
      loading.value = false;
    }
  }

  // Lazy history pagination: fetch older candles before current earliest.
  // Returns number of candles prepended.
  let historyLoading = false;
  async function fetchOlder(beforeOpenTime?: number, chunk: number = 600){
    if(historyLoading) return 0;
    if(!symbol.value || !interval.value) return 0;
    const earliest = candles.value.length? candles.value[0].open_time : undefined;
    const cursor = beforeOpenTime || earliest;
    if(!cursor) return 0;
    historyLoading = true;
    try {
      const r = await http.get('/api/ohlcv/history', { params: { symbol: symbol.value, interval: interval.value, limit: chunk, before_open_time: cursor }});
      const d = r.data || {};
      const arr = Array.isArray(d.candles)? d.candles: [];
      // API returns ASC; we requested before_open_time so arr should end at cursor-interval.
      // Filter out any overlap duplicates
      const existingEarliest = earliest;
      const newOnes = arr.filter((c:any)=> !existingEarliest || c.open_time < existingEarliest);
      if(newOnes.length){
        candles.value = [...newOnes, ...candles.value];
      }
      return newOnes.length;
    } catch(e:any){ /* silent for smooth UX */ return 0; }
    finally { historyLoading = false; }
  }

  async function fetchGaps(){
    if(!symbol.value || !interval.value) return;
    try {
      const r = await http.get('/api/ohlcv/gaps/status', { params: { symbol: symbol.value, interval: interval.value }});
      const d = r.data || {};
      gapSegments.value = Array.isArray(d.segments)? d.segments: [];
      openGapCount.value = d.open_segments || gapSegments.value.filter(s=> s.state==='open').length;
    } catch(e:any){ /* 조용히 무시 (UI 선택적) */ }
  }

  async function fetchMeta(){
    if(!symbol.value || !interval.value) return;
    try {
      const r = await http.get('/api/ohlcv/meta', { params: { symbol: symbol.value, interval: interval.value, sample_for_gap: 1500 }});
      const d = r.data || {};
      completeness365d.value = d.completeness_365d_percent ?? null;
    } catch(e:any){ /* silent */ }
  }

  function startPolling(){
    stopPolling();
    if(auto.value){
      timer = setInterval(fetchRecent, Math.max(5, pollSec.value)*1000);
    }
  }
  function stopPolling(){ if(timer) clearInterval(timer); timer=null; }
  function stopYearPolling(){ if(yearPollTimer) clearInterval(yearPollTimer); yearPollTimer=null; yearBackfillPolling.value=false; }
  // force=true: 항상 새 run 시작 (서버에서 완료 상태라도 재시작)
  async function startYearBackfill(){
    try {
      const r = await http.post('/api/ohlcv/backfill/year', null, { params: { symbol: symbol.value, interval: interval.value }});
      yearBackfill.value = r.data.state || r.data;
      yearBackfillPolling.value = true;
      if(!yearPollTimer){
        yearPollTimer = setInterval(pollYearStatus, 3000);
      }
    }catch(e:any){ /* toast elsewhere */ }
  }
  async function pollYearStatus(){
    try {
      const r = await http.get('/api/ohlcv/backfill/year/status', { params: { symbol: symbol.value, interval: interval.value }});
      yearBackfill.value = r.data;
      if(r.data && ['success','error','partial','idle'].includes(r.data.status)){
        stopYearPolling();
        // On success, refresh candles/meta/gaps
        if(r.data.status === 'success'){
          fetchRecent();
          fetchGaps();
          fetchMeta();
        }
      }
    }catch(e:any){ /* silent */ }
  }
  function toggleAuto(){ auto.value = !auto.value; startPolling(); }
  function setPollSec(v:number){ pollSec.value = v; startPolling(); }

  const lastCandle = computed(()=> candles.value.length? candles.value[candles.value.length-1]: null);
  const range = computed(()=>{
    if(candles.value.length<1) return null;
    let lo = candles.value[0].low, hi = candles.value[0].high;
    for(const c of candles.value){ if(c.low<lo) lo=c.low; if(c.high>hi) hi=c.high; }
    return { low: lo, high: hi };
  });

  return { symbol, interval, limit, candles, metrics, loading, error, auto, pollSec,
    gapSegments, openGapCount,
    yearBackfill, yearBackfillPolling,
    completeness365d,
    initDefaults, fetchRecent, fetchGaps, startPolling, stopPolling, toggleAuto, setPollSec, lastCandle, range,
    startYearBackfill, pollYearStatus, stopYearPolling, fetchOlder, fetchMeta };
});
