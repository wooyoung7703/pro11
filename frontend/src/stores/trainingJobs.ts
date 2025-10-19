import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import http from '@/lib/http';

export interface TrainingJob {
  id: number; // for in-memory synthetic rows we may use negative ids
  status: string;
  trigger: string;
  created_at: string; // ISO or timestamp string from backend (FastAPI returning datetime -> iso)
  finished_at?: string | null;
  duration_seconds?: number | null;
  artifact_path?: string | null;
  model_id?: number | null;
  version?: string | null;
  metrics?: Record<string, any> | null;
  drift_feature?: string | null;
  drift_z?: number | null;
  error?: string | null;
  target?: string | null;
}

export const useTrainingJobsStore = defineStore('trainingJobs', () => {
  const jobs = ref<TrainingJob[]>([]);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const auto = ref(true);
  const intervalSec = ref(10);
  const limit = ref(100);
  let timer: any = null;

  const sortKey = ref<string>('created_at');
  const sortDir = ref<'desc' | 'asc'>('desc');
  const nowTs = ref(Date.now());
  let liveTimer: any = null;
  // Filters
  const statusFilter = ref<'all' | 'running' | 'success' | 'error'>('all');
  const triggerFilter = ref<'all' | 'manual' | 'auto' | 'other'>('all');
  const targetFilter = ref<'all' | 'bottom' | 'other'>('all');

  function parseTs(ts: string) {
    if (!ts) return 0;
    if (/^\d+(?:\.\d+)?$/.test(ts)) return parseFloat(ts) * 1000;
    const d = new Date(ts).getTime();
    return isNaN(d) ? 0 : d;
  }

  function jobDurationMs(j: TrainingJob): number {
    const c = parseTs(j.created_at);
    // Prefer explicit duration from backend
    if (typeof j.duration_seconds === 'number' && !isNaN(j.duration_seconds)) {
      return Math.max(0, j.duration_seconds * 1000);
    }
    // Fallback: compute from finished_at
    const f = j.finished_at ? parseTs(j.finished_at) : null;
    if (f && f > 0 && f >= c) return Math.max(0, f - c);
    // Running: elapsed since created
    if (j.status === 'running') return Math.max(0, nowTs.value - c);
    // Unknown: return 0
    return 0;
  }

  function normalizeTriggerCategory(t: string | undefined | null): 'manual' | 'auto' | 'other' {
    if (!t) return 'other';
    const s = String(t).toLowerCase();
    if (s.includes('drift') || s === 'drift_auto') return 'auto';
    if (s.includes('manual')) return 'manual';
    return 'other';
  }

  function normalizeTargetCategory(t: string | undefined | null): 'bottom' | 'other' {
    if (!t) return 'other';
    const s = String(t).toLowerCase();
    if (s.includes('bottom')) return 'bottom';
    return 'other';
  }

  const filteredSorted = computed(() => {
    const arr = jobs.value.filter((j) => {
      // Status filter
      if (statusFilter.value !== 'all' && j.status !== statusFilter.value) return false;
      // Trigger filter: map to manual/auto/other buckets
      if (triggerFilter.value !== 'all') {
        const cat = normalizeTriggerCategory(j.trigger);
        if (triggerFilter.value !== cat) return false;
      }
      // Target filter: map to bottom/direction/other buckets
      if (targetFilter.value !== 'all') {
        const cat = normalizeTargetCategory(j.target || (j.metrics && typeof j.metrics === 'object' ? (j.metrics as any).target : undefined));
        if (targetFilter.value !== cat) return false;
      }
      return true;
    });
    arr.sort((a,b) => {
      const k = sortKey.value;
      let av: any; let bv: any;
      if (k === 'auc' || k === 'accuracy' || k === 'brier' || k === 'ece') {
        av = a.metrics?.[k]; bv = b.metrics?.[k];
      } else if (k === 'duration') {
        av = jobDurationMs(a); bv = jobDurationMs(b);
      } else {
        av = (a as any)[k]; bv = (b as any)[k];
      }
      if (av === undefined || av === null) av = -Infinity;
      if (bv === undefined || bv === null) bv = -Infinity;
      if (av < bv) return sortDir.value === 'asc' ? -1 : 1;
      if (av > bv) return sortDir.value === 'asc' ? 1 : -1;
      return 0;
    });
    return arr;
  });

  function statusColor(s: string) {
    switch (s) {
      case 'running': return 'text-amber-400';
      case 'success': return 'text-brand-accent';
      case 'error': return 'text-brand-danger';
      default: return 'text-neutral-400';
    }
  }

  async function fetchJobs() {
    try {
      loading.value = true;
      error.value = null;
      // DB-only fetch with configurable limit
      const dbResp = await http.get(`/api/training/jobs?limit=${encodeURIComponent(limit.value)}`).catch((e) => ({ data: [] }));
      const dbRows: any[] = Array.isArray(dbResp.data) ? dbResp.data : [];
      const cleanVersion = (v: any): string | null => {
        if (v === null || v === undefined) return null;
        const s = String(v).trim();
        const bad = ['n/a','na','none','null','undefined','-','—',''];
        return bad.includes(s.toLowerCase()) ? null : s;
      };
      const deriveVersionFromArtifact = (p: any): string | null => {
        if (!p || typeof p !== 'string') return null;
        try {
          const base = p.split('/').pop() as string;
          if (!base) return null;
          // expect name__VERSION.ext
          if (base.includes('__')) {
            const v = base.split('__', 2)[1];
            return (v && v.includes('.')) ? v.slice(0, v.lastIndexOf('.')) : (v || null);
          }
          return null;
        } catch { return null; }
      };
      jobs.value = dbRows.map((r) => ({
        id: r.id,
        status: r.status,
        trigger: r.trigger,
        created_at: r.created_at,
        finished_at: r.finished_at ?? null,
        duration_seconds: r.duration_seconds ?? null,
        artifact_path: r.artifact_path,
        model_id: r.model_id,
        version: cleanVersion(r.version) ?? deriveVersionFromArtifact(r.artifact_path),
        metrics: r.metrics,
        drift_feature: r.drift_feature,
        drift_z: r.drift_z,
        error: r.error,
        target: r.target ?? (r.metrics && typeof r.metrics === 'object' ? r.metrics.target : null),
      }));
    } catch (e: any) {
      // eslint-disable-next-line no-console
      console.warn('[trainingJobs] fetch failed', e);
      error.value = e.__friendlyMessage || e.message || 'fetch failed';
    } finally {
      loading.value = false;
    }
  }

  function start() {
    stop();
    if (liveTimer) clearInterval(liveTimer);
    liveTimer = setInterval(() => { nowTs.value = Date.now(); }, 1000);
    if (auto.value) {
      // Fast warm: 2 quick retries every 2s then normal interval
      let quick = 2;
      const quickInterval = setInterval(async () => {
        await fetchJobs();
        quick -= 1;
        if (quick <= 0) {
          clearInterval(quickInterval);
          timer = setInterval(fetchJobs, intervalSec.value * 1000);
        }
      }, 2000);
    }
  }

  function stop() {
    if (timer) clearInterval(timer); timer = null;
    if (liveTimer) clearInterval(liveTimer); liveTimer = null;
  }

  function toggleAuto(v?: boolean) {
    if (typeof v === 'boolean') auto.value = v; else auto.value = !auto.value;
    start();
  }

  function setIntervalSec(v: number) { intervalSec.value = v; start(); }
  function setLimit(v: number) { limit.value = v; fetchJobs(); }

  function setSort(k: string) {
    if (sortKey.value === k) {
      sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc';
    } else {
      sortKey.value = k; sortDir.value = k === 'created_at' ? 'desc' : 'asc';
    }
  }

  return { jobs, filteredSorted, loading, error, auto, intervalSec, fetchJobs, start, stop, toggleAuto, setIntervalSec, statusColor, sortKey, sortDir, setSort, jobDurationMs, limit, setLimit, statusFilter, triggerFilter, targetFilter };
});