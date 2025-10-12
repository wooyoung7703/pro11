<template>
  <div class="space-y-6">
    <section class="card p-4" v-if="summary">
      <h2 class="text-sm font-semibold mb-3 tracking-wide text-neutral-300">Summary (window={{ summary.window }})</h2>
      <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4 text-xs">
        <div class="flex flex-col">
          <span class="text-neutral-500">Promotion Rate</span>
          <span class="text-lg font-mono">{{ pct(summary.promotion_rate) }}</span>
        </div>
        <div class="flex flex-col">
          <span class="text-neutral-500">Promoted / Total</span>
          <span class="text-lg font-mono">{{ summary.promoted }} / {{ summary.total }}</span>
        </div>
        <div class="flex flex-col">
          <span class="text-neutral-500">ΔAUC Avg / Med</span>
          <span class="text-lg font-mono">{{ fmt4(summary.auc_improve_avg) }} / {{ fmt4(summary.auc_improve_median) }}</span>
        </div>
        <div class="flex flex-col">
          <span class="text-neutral-500">ΔECE Avg / Med</span>
          <span class="text-lg font-mono">{{ fmt4(summary.ece_delta_avg) }} / {{ fmt4(summary.ece_delta_median) }}</span>
        </div>
        <div class="flex flex-col">
          <span class="text-neutral-500">ValSamples Avg / Med</span>
          <span class="text-lg font-mono">{{ fmtInt(summary.val_samples_avg) }} / {{ fmtInt(summary.val_samples_median) }}</span>
        </div>
        <div class="flex flex-col">
          <span class="text-neutral-500">Errors</span>
          <span class="text-lg font-mono" :class="summary.errors ? 'text-amber-400':'text-neutral-400'">{{ summary.errors }}</span>
        </div>
      </div>
      <div class="mt-4">
        <h3 class="text-[11px] uppercase tracking-wide text-neutral-500 mb-1">ΔAUC Sparkline (Promoted)</h3>
        <svg v-if="aucSpark.length > 1" :width="sparkW" :height="sparkH" class="overflow-visible">
          <polyline :points="sparkPoints" fill="none" class="stroke-emerald-400" stroke-width="1.5" />
          <line v-if="zeroY !== null" :x1="0" :x2="sparkW" :y1="zeroY" :y2="zeroY" class="stroke-neutral-600" stroke-dasharray="2,3" stroke-width="1" />
        </svg>
        <div v-else class="text-[11px] text-neutral-500">insufficient data</div>
      </div>
      <div class="mt-5 grid md:grid-cols-2 gap-6">
        <div>
          <h3 class="text-[11px] uppercase tracking-wide text-neutral-500 mb-2">ΔAUC Histogram</h3>
          <div v-if="summary.auc_improve_hist" class="flex items-end gap-1 h-28">
            <div v-for="b in summary.auc_improve_hist" :key="b.bucket" class="flex flex-col items-center justify-end flex-1">
              <div class="w-full bg-emerald-500/60 rounded-t" :style="{height: barHeight(b.count, summary.auc_improve_hist)+'%'}"></div>
              <div class="text-[9px] mt-1 text-center truncate max-w-full" :title="b.bucket">{{ shortBucket(b.bucket) }}</div>
            </div>
          </div>
          <div v-else class="text-[11px] text-neutral-600">no data</div>
        </div>
        <div>
          <h3 class="text-[11px] uppercase tracking-wide text-neutral-500 mb-2">ECE Δ Histogram</h3>
            <div v-if="summary.ece_delta_hist" class="flex items-end gap-1 h-28">
              <div v-for="b in summary.ece_delta_hist" :key="b.bucket" class="flex flex-col items-center justify-end flex-1">
                <div class="w-full bg-sky-500/60 rounded-t" :style="{height: barHeight(b.count, summary.ece_delta_hist)+'%'}"></div>
                <div class="text-[9px] mt-1 text-center truncate max-w-full" :title="b.bucket">{{ shortBucket(b.bucket) }}</div>
              </div>
            </div>
            <div v-else class="text-[11px] text-neutral-600">no data</div>
        </div>
      </div>
    </section>
    <section class="card">
      <div class="flex items-center justify-between mb-4 flex-wrap gap-3">
        <h1 class="text-xl font-semibold">Promotion Audit</h1>
        <div class="flex items-center gap-3 text-xs flex-wrap">
          <button class="btn" :disabled="loading" @click="manualRefresh">새로고침</button>
          <label class="flex items-center gap-1 cursor-pointer select-none">
            <input type="checkbox" v-model="auto" @change="restart" /> 자동
          </label>
          <div class="flex items-center gap-1">
            간격 <input type="range" min="5" max="60" v-model.number="intervalSec" @change="restart" /> <span>{{ intervalSec }}s</span>
          </div>
          <div class="flex items-center gap-1">
            <select v-model="filterDecision" class="bg-neutral-800 px-2 py-1 rounded">
              <option :value="null">decision: ALL</option>
              <option value="promoted">promoted</option>
              <option value="skipped">skipped</option>
              <option value="error">error</option>
            </select>
            <select v-model="filterReasonCat" class="bg-neutral-800 px-2 py-1 rounded">
              <option :value="null">reason: ALL</option>
              <option value="criteria_met">criteria_met</option>
              <option value="no_production">no_production</option>
              <option value="criteria_not_met">criteria_not_met</option>
              <option value="insufficient_val_samples">insufficient_val_samples</option>
              <option value="promotion_failed">promotion_failed</option>
              <option value="policy_error">policy_error</option>
              <option value="other">other</option>
              <option value="unknown">unknown</option>
            </select>
            <input v-model="search" placeholder="search reason/model" class="bg-neutral-800 px-2 py-1 rounded w-40" />
          </div>
          <span v-if="error" class="px-2 py-0.5 rounded bg-brand-danger/20 text-brand-danger">{{ error }}</span>
        </div>
      </div>
      <div class="overflow-x-auto">
        <table class="min-w-full text-sm select-none">
          <thead class="text-left text-neutral-400 border-b border-neutral-700/60">
            <tr>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('id')">ID <SortIcon k="id" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('created_at')">Created <SortIcon k="created_at" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('decision')">Decision <SortIcon k="decision" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4">Reason</th>
              <th class="py-1 pr-4">Reason Cat</th>
              <th class="py-1 pr-4">Model</th>
              <th class="py-1 pr-4">Prev Prod</th>
              <th class="py-1 pr-4">Samples Old</th>
              <th class="py-1 pr-4">Samples New</th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('auc_improve')">ΔAUC <SortIcon k="auc_improve" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('ece_delta')">ΔECE <SortIcon k="ece_delta" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('val_samples')">ValSamples <SortIcon k="val_samples" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('auc_ratio')">ΔAUC Ratio <SortIcon k="auc_ratio" :sortKey="sortKey" :sortDir="sortDir"/></th>
              <th class="py-1 pr-4 cursor-pointer" @click="setSort('ece_margin_ratio')">ECE Margin <SortIcon k="ece_margin_ratio" :sortKey="sortKey" :sortDir="sortDir"/></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in sorted" :key="e.id" class="border-b border-neutral-800/40 hover:bg-neutral-800/30">
              <td class="py-1 pr-4 font-mono">{{ e.id || '—' }}</td>
              <td class="py-1 pr-4" :title="e.created_at">{{ shortTime(e.created_at) }}</td>
              <td class="py-1 pr-4">
                <span :class="decisionClass(e.decision)">{{ e.decision }}</span>
              </td>
              <td class="py-1 pr-4 max-w-[240px] truncate" :title="e.reason || undefined">{{ e.reason ? String(e.reason) : '—' }}</td>
              <td class="py-1 pr-4">
                <span class="px-1.5 py-0.5 rounded text-[10px] font-medium" :class="badgeColor(e.reason_category)">{{ e.reason_category }}</span>
              </td>
              <td class="py-1 pr-4 font-mono">{{ e.model_id || '—' }}</td>
              <td class="py-1 pr-4 font-mono">{{ e.previous_production_model_id || '—' }}</td>
              <td class="py-1 pr-4 font-mono">{{ e.samples_old ?? '—' }}</td>
              <td class="py-1 pr-4 font-mono">{{ e.samples_new ?? '—' }}</td>
              <td class="py-1 pr-4 font-mono" :title="e.auc_improve != null ? String(e.auc_improve) : undefined">{{ e.auc_improve != null ? e.auc_improve.toFixed(4) : '—' }}</td>
              <td class="py-1 pr-4 font-mono" :title="e.ece_delta != null ? String(e.ece_delta) : undefined">{{ e.ece_delta != null ? e.ece_delta.toFixed(4) : '—' }}</td>
              <td class="py-1 pr-4 font-mono">{{ e.val_samples != null ? e.val_samples : '—' }}</td>
              <td class="py-1 pr-4 font-mono" :class="ratioClass(e.auc_ratio, true)">{{ fmtRatio(e.auc_ratio) }}</td>
              <td class="py-1 pr-4 font-mono" :class="ratioClass(e.ece_margin_ratio, false)">{{ fmtRatio(e.ece_margin_ratio) }}</td>
            </tr>
            <tr v-if="!loading && sorted.length === 0">
              <td colspan="9" class="py-4 text-center text-neutral-500">No events</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, computed } from 'vue';
// Use relative path to avoid alias resolution issues
import { usePromotionAuditStore } from '../stores/promotionAudit';

const store = usePromotionAuditStore();
const { fetchEvents, start, stop, sorted, loading, error, auto, intervalSec, filterDecision, filterReasonCat, search, setSort, sortKey, sortDir, badgeColor, fetchSummary, summary, aucSpark, setAuto, setIntervalSeconds } = store;

const sparkW = 260;
const sparkH = 54;
function fmt4(v: any) { return (typeof v === 'number' && isFinite(v)) ? v.toFixed(4) : '—'; }
function fmtInt(v: any) { return (typeof v === 'number' && isFinite(v)) ? Math.round(v) : '—'; }
function pct(v: any) { return (typeof v === 'number' && isFinite(v)) ? (v*100).toFixed(1)+'%' : '—'; }

const sparkPoints = computed(() => {
  if (!aucSpark.length) return '';
  const vals = [...aucSpark];
  const min = Math.min(...vals, 0);
  const max = Math.max(...vals, 0.000001);
  const span = (max - min) || 1;
  const step = sparkW / Math.max(1, vals.length - 1);
  return vals.map((v,i) => {
    const x = i * step;
    const y = sparkH - ((v - min) / span) * sparkH;
    return `${x},${y}`;
  }).join(' ');
});
const zeroY = computed(() => {
  if (!aucSpark.length) return null;
  const min = Math.min(...aucSpark, 0);
  const max = Math.max(...aucSpark, 0);
  if (min > 0 || max < 0) return null; // zero outside range
  const span = (max - min) || 1;
  return sparkH - ((0 - min)/span)*sparkH;
});

function manualRefresh() { fetchEvents(); }
function restart() { start(); }

function shortTime(ts?: string) {
  if (!ts) return '—';
  try { return new Date(ts).toLocaleTimeString(); } catch { return ts; }
}
function decisionClass(decision?: string) {
  switch(decision) {
    case 'promoted': return 'text-emerald-400';
    case 'skipped': return 'text-neutral-400';
    case 'error': return 'text-red-400';
    default: return 'text-neutral-500';
  }
}

function fmtRatio(v: any) {
  if (typeof v !== 'number' || !isFinite(v)) return '—';
  return v.toFixed(2) + 'x';
}
function ratioClass(v: any, positiveBetter: boolean) {
  if (typeof v !== 'number' || !isFinite(v)) return 'text-neutral-500';
  // Heuristic bands
  if (positiveBetter) { // auc_ratio >=1 good
    if (v >= 1.2) return 'text-emerald-400';
    if (v >= 1.0) return 'text-emerald-300';
    if (v >= 0.8) return 'text-amber-300';
    return 'text-rose-400';
  } else { // ece_margin_ratio >=1 : strong margin, >=0 good, <0 bad
    if (v >= 1.0) return 'text-emerald-400';
    if (v >= 0.5) return 'text-emerald-300';
    if (v >= 0.0) return 'text-amber-300';
    return 'text-rose-400';
  }
}

function barHeight(count: any, buckets: any[]): number {
  if (!buckets || !buckets.length) return 0;
  const max = Math.max(...buckets.map(b => b.count || 0), 1);
  const v = typeof count === 'number' ? count : 0;
  return (v / max) * 100;
}
function shortBucket(label: any): string {
  if (typeof label !== 'string') return '';
  // compress like (-inf,0.0) -> <0 / (0.0,0.005) -> 0-0.005
  if (label.startsWith('(') || label.startsWith('[')) {
  // match ranges like (-inf,0.0) or (0.0,0.005) or (0.1,inf)
  // hyphen does not need escaping when placed at the start or end of a character class
  const m = label.match(/([-0-9.]+),(inf|[-0-9.]+)/);
    if (m) {
      const a = m[1];
      const b = m[2];
      if (b === 'inf') return a + '+';
      if (a === '-inf') return '<' + b;
      return a + '–' + b;
    }
  }
  return label;
}

// Inline sort indicator
const SortIcon = (props: { k: string; sortKey: string; sortDir: string }) => {
  if (props.sortKey !== props.k) return null as any;
  return props.sortDir === 'asc' ? '▲' : '▼';
};

onMounted(() => {
  setAuto(true);
  fetchSummary();
  start();
});
</script>

<style scoped>
table { border-collapse: collapse; }
</style>
