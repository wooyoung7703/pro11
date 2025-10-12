// systemComponents.ts
// System Status components 해석 로직을 Dashboard 에서 분리

export interface ComponentLine { k: string; v: string }
export interface DashboardComponentItem {
  key: string;
  label: string;
  indicatorClass: string;
  lines: ComponentLine[];
}

const LABELS: Record<string,string> = {
  ingestion: 'Ingestion',
  features: 'Features',
  news: 'News',
  drift: 'Drift',
  calibration_monitor: 'Calibration',
  risk: 'Risk',
};

// 특정 컴포넌트별 가공 로직
export function buildSystemComponentItems(raw: any): DashboardComponentItem[] {
  const list: DashboardComponentItem[] = [];
  if (!raw || typeof raw !== 'object') return list;
  // ingestion
  if (raw.ingestion) {
    const st = raw.ingestion;
    const indicator = st.stale ? 'bg-amber-400' : (st.running ? 'bg-green-400':'bg-neutral-500');
    const lines: ComponentLine[] = [];
    if (st.running) lines.push({ k: 'lag', v: `lag ${st.lag_seconds ?? '-' }s` });
    lines.push({ k: 'buf', v: `msgs ${st.total_messages ?? '-'} / rec ${st.reconnect_attempts ?? '-'}` });
    list.push({ key: 'ingestion', label: LABELS.ingestion, indicatorClass: indicator, lines });
  }
  // features
  if (raw.features) {
    const st = raw.features;
    const health = st.health;
    const indicator = health === 'ok' ? 'bg-green-400' : (health === 'degraded' ? 'bg-amber-400' : health === 'stopped' ? 'bg-red-500':'bg-neutral-500');
    const lines: ComponentLine[] = [];
    if (st.last_success_ts) lines.push({ k: 'lag', v: `lag ${st.lag_sec ?? '-' }s` });
    lines.push({ k: 'run', v: st.running ? 'running':'idle' });
    list.push({ key: 'features', label: LABELS.features, indicatorClass: indicator, lines });
  }
  // news
  if (raw.news) {
    const st = raw.news;
    const indicator = st.fast_startup_skipped ? 'bg-neutral-500' : 'bg-green-400';
    const lines: ComponentLine[] = [];
    if (st.ingestion_lag_seconds != null) lines.push({ k: 'lag', v: `lag ${Number(st.ingestion_lag_seconds).toFixed(0)}s` });
    lines.push({ k: 'count', v: `total ${st.total_articles ?? '-'}` });
    list.push({ key: 'news', label: LABELS.news, indicatorClass: indicator, lines });
  }
  // drift
  if (raw.drift) {
    const st = raw.drift;
    const indicator = (st.drift_count ?? 0) > 0 ? 'bg-amber-400':'bg-green-400';
    const lines: ComponentLine[] = [];
    lines.push({ k: 'cnt', v: `${st.drift_count ?? 0}/${st.total ?? '-'}` });
    if (st.top_feature) lines.push({ k: 'top', v: st.top_feature });
    list.push({ key: 'drift', label: LABELS.drift, indicatorClass: indicator, lines });
  }
  // calibration
  if (raw.calibration_monitor) {
    const st = raw.calibration_monitor;
    const indicator = st.recommend_retrain ? 'bg-amber-400':'bg-green-400';
    const lines: ComponentLine[] = [];
    lines.push({ k: 'abs', v: `abs ${st.abs_streak}` });
    lines.push({ k: 'rel', v: `rel ${st.rel_streak}` });
    if (st.recommend_retrain) lines.push({ k: 'rec', v: 'retrain!' });
    list.push({ key: 'calib', label: LABELS.calibration_monitor, indicatorClass: indicator, lines });
  }
  // risk
  if (raw.risk) {
    const st = raw.risk;
    const indicator = 'bg-green-400';
    const lines: ComponentLine[] = [];
    const equity = st.current_equity?.toFixed ? st.current_equity.toFixed(0) : st.current_equity;
    lines.push({ k: 'eq', v: `eq ${equity}` });
    if (st.drawdown_ratio != null) lines.push({ k: 'dd', v: `dd ${(st.drawdown_ratio * 100).toFixed(1)}%` });
    list.push({ key: 'risk', label: LABELS.risk, indicatorClass: indicator, lines });
  }
  return list;
}
