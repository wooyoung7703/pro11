import { defineStore } from 'pinia';
import api from '@/lib/http';
import { validateResponse } from '@/validation/validate';
import { recentInferenceSpec } from '@/validation/specs';
import { useToastStore } from './toast';

export interface RecentDecisionItem {
  created_at: number | null; // epoch seconds
  probability?: number | null;
  decision: any; // server may send -1/0/1; we normalize later
  threshold?: number | null;
  production?: boolean;
  model_name?: string;
  model_version?: string | number;
  auto?: boolean;
  // client-side stable key to preserve identity across refreshes
  uid?: string;
}

interface FetchFilters { limit: number; auto_only?: boolean; manual_only?: boolean; }
interface State {
  loading: boolean;
  error: string | null;
  items: RecentDecisionItem[];
  lastFetched: number | null;
  filters: FetchFilters;
  // consecutive empty responses counter to avoid flicker when backend briefly returns []
  emptyStreak: number;
}

export const useRecentDecisionsStore = defineStore('recentDecisions', {
  state: (): State => ({
    loading: false,
    error: null,
    items: [],
    lastFetched: null,
    filters: { limit: 50 },
    emptyStreak: 0,
  }),
  getters: {
    normalized(state): RecentDecisionItem[] {
      return state.items.map(it => ({
        ...it,
        decision: typeof it.decision === 'number' ? it.decision : (typeof it.decision === 'string' ? Number(it.decision) : null),
      }));
    },
    decisionLabel(): (d: number|null|undefined) => string {
      return (d) => d === 1 ? '매수' : d === -1 ? '매도' : d === 0 ? '유지' : '-';
    }
  },
  actions: {
    setLimit(n: number) { this.filters.limit = Math.min(500, Math.max(1, Math.floor(n))); },
    setAutoOnly(v: boolean) { this.filters.auto_only = v; if (v) this.filters.manual_only = false; },
    setManualOnly(v: boolean) { this.filters.manual_only = v; if (v) this.filters.auto_only = false; },
    async fetchRecent() {
      this.loading = true; this.error = null;
      const toast = useToastStore();
      try {
        const params: any = { limit: this.filters.limit };
        if (this.filters.auto_only) params.auto_only = true;
        if (this.filters.manual_only) params.manual_only = true;
        const resp = await api.get('/api/inference/activity/recent', { params });
        const data = resp.data;
        const { warnings, errors } = validateResponse(data, recentInferenceSpec);
        if (errors.length) {
          this.error = errors.join(',');
          toast.push({ type: 'error', message: `Recent decisions 오류: ${this.error}` });
          return;
        }
        if (warnings.length) {
          toast.push({ type: 'warning', message: `Recent decisions 경고: ${warnings.join(' | ')}` });
        }
        if (data.status !== 'ok') {
          this.error = data.status;
          toast.push({ type: 'error', message: `Recent decisions status=${data.status}` });
          return;
        }
        const incoming: RecentDecisionItem[] = Array.isArray(data.items) ? data.items : [];
        if (incoming.length === 0) {
          this.emptyStreak += 1;
          // Preserve existing list for a couple of empty ticks to avoid flicker
          if (this.items.length > 0 && this.emptyStreak <= 2) {
            this.lastFetched = Date.now();
            return;
          }
        } else {
          this.emptyStreak = 0;
        }
        // Build stable uid key for each item (no server id provided)
        const keyOf = (it: RecentDecisionItem) => {
          const ts = it.created_at ?? 0;
          const m = it.model_name ?? '';
          const v = it.model_version ?? '';
          // Avoid volatile fields (decision/prod/threshold/probability) in key to keep identity stable
          return `${ts}|${m}|${v}`;
        };
        // Fast-path: if order and keys are unchanged, update in place without replacing the array
        const incomingKeys = incoming.map((inc) => keyOf(inc));
        const currentKeys = this.items.map((it) => it.uid || '');
        const sameOrder = incomingKeys.length === currentKeys.length && incomingKeys.every((k, i) => k === currentKeys[i]);
        if (sameOrder) {
          for (let i = 0; i < incoming.length; i++) {
            const inc = incoming[i];
            const key = incomingKeys[i];
            let target = this.items[i];
            if (!target || target.uid !== key) {
              // fallback safety, though we expect keys to align here
              const alt = this.items.find((x) => x.uid === key);
              if (alt) target = alt;
            }
            if (target) {
              Object.assign(target, inc);
            } else {
              // very unlikely in sameOrder branch; ensure uid and patch
              inc.uid = key;
              if (this.items[i]) this.items[i] = inc; else this.items.push(inc);
            }
          }
        } else {
          // General path: rebuild while preserving object identity when possible
          const existingMap = new Map<string, RecentDecisionItem>();
          for (const old of this.items) {
            if (old && old.uid) existingMap.set(old.uid, old);
          }
          const next: RecentDecisionItem[] = [];
          for (const inc of incoming) {
            const key = keyOf(inc);
            const found = existingMap.get(key);
            if (found) {
              Object.assign(found, inc);
              next.push(found);
            } else {
              inc.uid = key;
              next.push(inc);
            }
          }
          // Mutate in place to preserve array reference and reduce flicker in renderers
          this.items.splice(0, this.items.length, ...next);
        }
        this.lastFetched = Date.now();
      } catch (e: any) {
        this.error = e?.message || 'fetch_failed';
      } finally {
        this.loading = false;
      }
    }
  }
});
