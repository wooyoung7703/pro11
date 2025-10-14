import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { buildApiKeyHeaders } from '../lib/apiKey';

interface PromotionEvent {
  id?: number;
  created_at?: string;
  model_id?: number | null;
  previous_production_model_id?: number | null;
  decision?: string; // promoted | skipped | error
  reason?: string | null;
  samples_old?: number | null;
  samples_new?: number | null;
  auc_improve?: number | null;
  ece_delta?: number | null;
  val_samples?: number | null;
}

export const usePromotionAuditStore = defineStore('promotionAudit', () => {
  const events = ref<PromotionEvent[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const auto = ref(true);
  const intervalSec = ref(15);
  try {
    const persisted = localStorage.getItem('promotion_audit_auto');
    if (persisted === 'false') auto.value = false;
    const iv = localStorage.getItem('promotion_audit_interval');
    if (iv) {
      const n = parseInt(iv, 10);
      if (isFinite(n) && n >= 5 && n <= 120) intervalSec.value = n;
    }
  } catch (_) { /* ignore */ }
  let timer: any = null;

  const filterDecision = ref<string | null>(null);
  const filterReasonCat = ref<string | null>(null);
  const search = ref('');
  const sortKey = ref<string>('created_at');
  const sortDir = ref<'asc' | 'desc'>('desc');
  const summary = ref<any | null>(null);
  const summaryError = ref<string | null>(null);
  const aucSpark = ref<number[]>([]); // last N auc improvements (promoted only)

  function classifyReason(r: string | null | undefined): string {
    if (!r) return 'unknown';
    if (r.startsWith('auc+')) return 'criteria_met';
    if (r.startsWith('criteria_not_met')) return 'criteria_not_met';
    if (r.startsWith('no_production')) return 'no_production';
    if (r.startsWith('insufficient_val_samples')) return 'insufficient_val_samples';
    if (r.startsWith('promotion_failed')) return 'promotion_failed';
    if (r.startsWith('policy_error')) return 'policy_error';
    return 'other';
  }

  const decorated = computed(() => events.value.map(e => ({
    ...e,
    reason_category: classifyReason(e.reason || null),
    // keep numeric fields as-is; if absent backend older version, attempt light parse fallback
    auc_improve: e.auc_improve ?? parseAuc(e.reason),
    ece_delta: e.ece_delta ?? parseEce(e.reason),
    val_samples: e.val_samples ?? parseValSamples(e.reason),
    auc_ratio: (e as any).auc_ratio ?? (typeof (e as any).auc_improve === 'number' && (window as any).CFG_MIN_AUC_DELTA ? (e as any).auc_improve / (window as any).CFG_MIN_AUC_DELTA : null),
    ece_margin_ratio: (e as any).ece_margin_ratio ?? (typeof (e as any).ece_delta === 'number' && (window as any).CFG_MAX_ECE_DELTA ? ((window as any).CFG_MAX_ECE_DELTA - (e as any).ece_delta) / (window as any).CFG_MAX_ECE_DELTA : null),
  })));

  function parseAuc(r?: string | null): number | null {
    if (!r) return null;
    const m = r.match(/auc\+([0-9.+-]+)/) || r.match(/criteria_not_met_auc([0-9.+-]+)/);
    if (m) { const v = parseFloat(m[1]); return isFinite(v) ? v : null; }
    return null;
  }
  function parseEce(r?: string | null): number | null {
    if (!r) return null;
    const m = r.match(/ece_delta(-?[0-9.]+)/);
    if (m) { const v = parseFloat(m[1]); return isFinite(v) ? v : null; }
    return null;
  }
  function parseValSamples(r?: string | null): number | null {
    if (!r) return null;
    const m = r.match(/val(\d+)/) || r.match(/insufficient_val_samples:(\d+)/);
    if (m) { const v = parseInt(m[1]); return isFinite(v) ? v : null; }
    return null;
  }

  const filtered = computed(() => decorated.value.filter(e => {
    if (filterDecision.value && e.decision !== filterDecision.value) return false;
    if (filterReasonCat.value && e.reason_category !== filterReasonCat.value) return false;
    if (search.value) {
      const s = search.value.toLowerCase();
      if (!(e.reason || '').toLowerCase().includes(s) && !(String(e.model_id||'')).includes(s)) return false;
    }
    return true;
  }));

  const sorted = computed(() => {
    const arr = [...filtered.value];
    arr.sort((a,b) => {
      const k = sortKey.value as keyof typeof a;
      const av: any = a[k];
      const bv: any = b[k];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av === bv) return 0;
      if (sortDir.value === 'asc') return av > bv ? 1 : -1;
      return av < bv ? 1 : -1;
    });
    return arr;
  });

  async function fetchEvents(limit = 200) {
    loading.value = true;
    error.value = null;
    try {
  const r = await fetch(`/api/models/promotion/events?limit=${limit}`, { headers: buildApiKeyHeaders() });
      if (!r.ok) throw new Error('HTTP '+r.status);
      const data = await r.json();
      events.value = data.events || [];
      // update sparkline (only promoted with numeric auc_improve)
      const aucs: number[] = [];
      for (const e of events.value) {
        if (e.decision === 'promoted') {
          const v: any = (e as any).auc_improve;
            if (typeof v === 'number' && isFinite(v)) aucs.push(v);
        }
      }
      aucSpark.value = aucs.slice(-40); // cap length
    } catch (e: any) {
      error.value = e.message || String(e);
    } finally {
      loading.value = false;
    }
  }

  async function fetchSummary(window = 200) {
    summaryError.value = null;
    try {
  const r = await fetch(`/api/models/promotion/summary?window=${window}`, { headers: buildApiKeyHeaders() });
      if (!r.ok) throw new Error('HTTP '+r.status);
      const data = await r.json();
      summary.value = data.summary || null;
    } catch (e: any) {
      summaryError.value = e.message || String(e);
    }
  }

  function setSort(k: string) {
    if (sortKey.value === k) {
      sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc';
    } else {
      sortKey.value = k;
      sortDir.value = k === 'created_at' ? 'desc' : 'asc';
    }
  }

  function start() {
    stop();
    if (auto.value) {
      // immediate tick
      fetchEvents();
      timer = setInterval(() => fetchEvents(), intervalSec.value * 1000);
    }
  }
  function stop() { if (timer) { clearInterval(timer); timer = null; } }

  // persist auto & interval changes
  function setAuto(v: boolean) {
    auto.value = v;
    try { localStorage.setItem('promotion_audit_auto', String(v)); } catch (_) {}
    if (v) start(); else stop();
  }
  function setIntervalSeconds(v: number) {
    intervalSec.value = Math.max(5, Math.min(120, Math.floor(v)));
    try { localStorage.setItem('promotion_audit_interval', String(intervalSec.value)); } catch (_) {}
    if (auto.value) start();
  }

  function badgeColor(cat: string) {
    switch (cat) {
      case 'criteria_met': return 'bg-emerald-600/20 text-emerald-400';
      case 'promoted': return 'bg-emerald-600/20 text-emerald-400';
      case 'no_production': return 'bg-sky-600/20 text-sky-400';
      case 'criteria_not_met': return 'bg-amber-600/20 text-amber-400';
      case 'insufficient_val_samples': return 'bg-rose-600/20 text-rose-400';
      case 'promotion_failed': return 'bg-red-600/20 text-red-400';
      case 'policy_error': return 'bg-fuchsia-600/20 text-fuchsia-400';
      case 'other': return 'bg-neutral-600/20 text-neutral-300';
      default: return 'bg-neutral-700/30 text-neutral-400';
    }
  }

  return {
    events,
    loading,
    error,
    auto,
    intervalSec,
  setAuto,
  setIntervalSeconds,
    fetchEvents,
    start,
    stop,
    filtered,
    sorted,
    search,
    filterDecision,
    filterReasonCat,
    sortKey,
    sortDir,
    setSort,
    badgeColor,
    classifyReason,
    summary,
    summaryError,
    fetchSummary,
    aucSpark,
  };
});
