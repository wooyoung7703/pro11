import { defineStore } from 'pinia';
import api from '../lib/http';

interface ActivitySummary {
  status: string;
  last_decision_ts: number | null;
  decisions_5m: number;
  decisions_1m: number;
  auto_loop_enabled: boolean;
  interval: number;
}

interface State {
  loading: boolean; // kept for backward compatibility (initial)
  initialLoading: boolean;
  refreshing: boolean;
  error: string | null;
  summary: ActivitySummary | null;
  lastOkAt: number | null;
  idleStreakMinutes: number; // 연속 idle 분
}

export const useTradingActivityStore = defineStore('tradingActivity', {
  state: (): State => ({
    loading: false,
    initialLoading: true,
    refreshing: false,
    error: null,
    summary: null,
    lastOkAt: null,
    idleStreakMinutes: 0,
  }),
  getters: {
    isIdle(state): boolean {
      // 5분 의사결정 0 이면 idle 로 간주
      if (!state.summary) return true;
      return state.summary.decisions_5m === 0;
    },
    lastDecisionDate(state): Date | null {
      if (!state.summary || !state.summary.last_decision_ts) return null;
      return new Date(state.summary.last_decision_ts * 1000);
    },
  },
  actions: {
    async fetchSummary() {
      if (this.initialLoading) {
        this.loading = true;
      } else {
        this.refreshing = true;
      }
      this.error = null;
      try {
        const resp = await api.get<ActivitySummary>('/api/inference/activity/summary');
        if (resp.data.status === 'ok') {
            const prev = this.summary;
            this.summary = resp.data;
            this.lastOkAt = Date.now();
            if (resp.data.decisions_5m === 0) {
              const intervalSec = resp.data.interval || 60;
              this.idleStreakMinutes += intervalSec / 60;
            } else {
              this.idleStreakMinutes = 0;
            }
            // optional: could track field diffs here for highlight
        } else {
          this.error = resp.data.status;
        }
      } catch (e: any) {
        this.error = e?.message || 'fetch_failed';
      } finally {
        this.loading = false;
        if (this.initialLoading) this.initialLoading = false;
        this.refreshing = false;
      }
    },
    compareIntervalWithEnv(): { backend?: number; env?: number; match: boolean | null } {
      const backend = this.summary?.interval;
      let envVal: number | undefined;
      try { const raw = (import.meta as any).env?.VITE_INFERENCE_AUTO_LOOP_INTERVAL; if (raw) envVal = Number(raw); } catch(_){}
      // If either side missing, treat as not comparable (no banner)
      if (backend == null || envVal == null) return { backend, env: envVal, match: null };
      return { backend, env: envVal, match: backend === envVal };
    }
  },
});
