<template>
  <div class="space-y-4">
    <!-- Shared confirm dialog for model actions -->
    <ConfirmDialog
      :open="confirm.open"
      :title="confirm.title"
      :message="confirm.message"
      :requireText="confirm.requireText"
      :delayMs="confirm.delayMs"
      @confirm="confirm.onConfirm && confirm.onConfirm()"
      @cancel="confirm.open=false"
    />
    <div class="flex items-center gap-3">
      <h2 class="text-sm font-semibold tracking-wide">Models</h2>
      <span v-if="loading" class="text-[10px] text-neutral-500 animate-pulse">Loading...</span>
      <span v-if="!loading" :class="hasModel ? 'bg-emerald-600/20 text-emerald-300' : 'bg-rose-600/20 text-rose-300'" class="px-2 py-0.5 rounded text-[10px] font-mono">
        {{ hasModel ? 'HAS_MODEL' : 'NO_MODEL' }}
      </span>
      <!-- bottom-only mode: no target selector -->
      <button @click="refresh" class="text-[10px] px-2 py-0.5 rounded bg-neutral-800 hover:bg-neutral-700 transition">↻</button>
    </div>

    <div v-if="error" class="text-xs text-rose-400 font-mono break-all">{{ error }}</div>

    <div v-if="!error" class="space-y-3">
      <div v-if="production" class="p-3 border border-neutral-800 rounded bg-neutral-900/50">
        <div class="flex items-center justify-between mb-1">
          <div class="flex items-center gap-2">
            <div class="text-xs font-semibold text-neutral-200">Production Model</div>
            <span class="text-[10px] text-neutral-500">version: {{ production.version }}</span>
          </div>
          <div class="flex items-center gap-2">
            <button @click="toggleHistory" class="text-[10px] px-2 py-0.5 rounded bg-neutral-800 hover:bg-neutral-700 transition">History</button>
          </div>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-3 gap-2 text-[11px]">
          <div v-for="(v,k) in prodMetrics" :key="k" class="flex justify-between gap-2 bg-neutral-800/40 px-2 py-1 rounded">
            <span class="text-neutral-400">{{ k }}</span>
            <span class="text-neutral-200 font-mono">{{ formatMetric(v) }}</span>
          </div>
        </div>
        <div v-if="showHistory" class="mt-3 border-t border-neutral-800 pt-2">
          <div class="flex items-center justify-between mb-1">
            <div class="text-[11px] text-neutral-300">Production History</div>
            <button @click="loadHistory" class="text-[10px] px-2 py-0.5 rounded bg-neutral-800 hover:bg-neutral-700">↻</button>
          </div>
          <div v-if="histError" class="text-[10px] text-rose-400 font-mono">{{ histError }}</div>
          <div v-else>
            <table class="w-full text-[11px]">
              <thead>
                <tr class="text-neutral-400 text-left">
                  <th class="py-1 pr-2 font-medium">Version</th>
                  <th class="py-1 pr-2 font-medium">Promoted</th>
                  <th class="py-1 pr-2 font-medium">AUC</th>
                  <th class="py-1 pr-2 font-medium">ECE</th>
                  <th class="py-1 pr-2 font-medium">Action</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="h in history" :key="h.id" class="border-t border-neutral-800/60">
                  <td class="py-1 pr-2 font-mono">{{ h.version }}</td>
                  <td class="py-1 pr-2 font-mono" :title="h.promoted_at || h.created_at">{{ relativeTime(h.promoted_at || h.created_at) }}</td>
                  <td class="py-1 pr-2 font-mono">{{ fmt(h.metrics?.auc) }}</td>
                  <td class="py-1 pr-2 font-mono">{{ fmt(h.metrics?.ece) }}</td>
                  <td class="py-1 pr-2">
                    <button @click="rollbackTo(h)" class="text-[10px] px-2 py-0.5 rounded bg-amber-700 hover:bg-amber-600 text-white">Rollback</button>
                  </td>
                </tr>
                <tr v-if="history.length===0">
                  <td colspan="5" class="py-2 text-center text-neutral-500">No production history.</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="p-3 border border-neutral-800 rounded bg-neutral-900/40">
        <div class="flex items-center justify-between mb-2">
          <div class="text-xs font-semibold text-neutral-200">Recent Models ({{ recent.length }})</div>
          <div v-if="actionInfo" class="text-[10px] px-2 py-0.5 rounded bg-neutral-800 text-neutral-200 border border-neutral-700">{{ actionInfo }}</div>
        </div>
        <table class="w-full text-[11px] border-collapse">
          <thead>
            <tr class="text-neutral-400 text-left">
              <th class="py-1 pr-2 font-medium">Version</th>
              <th class="py-1 pr-2 font-medium">Status</th>
              <th class="py-1 pr-2 font-medium">Created</th>
              <th class="py-1 pr-2 font-medium">AUC</th>
              <th class="py-1 pr-2 font-medium">ECE</th>
              <th class="py-1 pr-2 font-medium">Acc</th>
              <th class="py-1 pr-2 font-medium">ΔAUC</th>
              <th class="py-1 pr-2 font-medium">ΔECE</th>
              <th class="py-1 pr-2 font-medium">Promote?</th>
              <th class="py-1 pr-2 font-medium">Action</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in recent" :key="m.id" class="border-t border-neutral-800/60 hover:bg-neutral-800/40">
              <td class="py-1 pr-2 font-mono">{{ m.version }}</td>
              <td class="py-1 pr-2">
                <span :class="m.status==='production' ? 'text-emerald-400':'text-neutral-300'">{{ m.status }}</span>
              </td>
              <td class="py-1 pr-2 font-mono" :title="m.created_at">{{ relativeTime(m.created_at) }}</td>
              <td class="py-1 pr-2 font-mono">{{ fmt(m.metrics?.auc) }}</td>
              <td class="py-1 pr-2 font-mono">{{ fmt(m.metrics?.ece) }}</td>
              <td class="py-1 pr-2 font-mono">{{ fmt(m.metrics?.accuracy) }}</td>
              <td class="py-1 pr-2 font-mono" :class="m.status==='production' ? 'text-neutral-500' : deltaClass(m.auc_delta, true)">
                <span v-if="m.status==='production'" title="Baseline production model – delta N/A (현재 프로덕션 기준 모델이라 Δ 값 없음)">-</span>
                <span v-else>{{ formatDelta(m.auc_delta) }}</span>
              </td>
              <td class="py-1 pr-2 font-mono" :class="m.status==='production' ? 'text-neutral-500' : deltaClass(m.ece_delta, false)">
                <span v-if="m.status==='production'" title="Baseline production model – delta N/A (현재 프로덕션 기준 모델이라 Δ 값 없음)">-</span>
                <span v-else>{{ formatDelta(m.ece_delta) }}</span>
              </td>
              <td class="py-1 pr-2">
                <template v-if="m.status==='production'">
                  <span class="text-[10px] px-1 py-0.5 rounded bg-neutral-700/40 text-neutral-400" title="현재 기준(baseline) 모델">BASE</span>
                </template>
                <template v-else>
                  <span v-if="m.promotion_recommend || m.promote_recommend" class="text-[10px] px-1 py-0.5 rounded bg-emerald-600/30 text-emerald-300 font-mono" title="승격 추천: 기준 임계치 충족 (AUC↑ & ECE 허용 범위 내)">RECOMMEND</span>
                  <span v-else class="text-[10px] px-1 py-0.5 rounded bg-neutral-700/40 text-neutral-400 font-mono" :title="formatPromoteReason(m.promotion_recommend_reason)">-</span>
                </template>
              </td>
              <td class="py-1 pr-2">
                <button
                  v-if="m.status!=='production'"
                  @click="promoteRow(m)"
                  :disabled="loading || promotingId===m.id"
                  :title="(m.promotion_recommend || m.promote_recommend) ? '추천됨: 기준 충족' : '수동 승격'"
                  class="text-[10px] px-2 py-0.5 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 disabled:cursor-not-allowed text-white transition"
                >
                  <span v-if="promotingId===m.id">Promoting…</span>
                  <span v-else>Promote</span>
                </button>
                <button
                  v-if="m.status!=='production'"
                  @click="deleteRow(m)"
                  :disabled="loading"
                  class="ml-2 text-[10px] px-2 py-0.5 rounded bg-rose-700 hover:bg-rose-600 text-white transition"
                  title="이 모델 레지스트리 레코드를 삭제(soft-delete)합니다"
                >Delete</button>
              </td>
            </tr>
            <tr v-if="recent.length===0">
              <td colspan="10" class="py-4 text-center text-neutral-500">No models registered.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue';
import ConfirmDialog from '../components/ConfirmDialog.vue';
import { confirmPresets } from '../lib/confirmPresets';
import { buildApiKeyHeaders } from '../lib/apiKey';

interface ModelRow { id:number; version:string; status:string; created_at?:string; metrics?:Record<string,any>; auc_delta?: number|null; ece_delta?: number|null; promotion_recommend?: boolean; promote_recommend?: boolean; promotion_recommend_reason?: string; [key:string]: any }

const loading = ref(true);
const error = ref<string|null>(null);
const hasModel = ref(false);
const production = ref<ModelRow|null>(null);
const recent = ref<ModelRow[]>([]);
const promotingId = ref<number|null>(null);
const actionInfo = ref<string|null>(null);
// bottom-only mode

// Confirm dialog state & helper
type ConfirmFn = () => void | Promise<void>;
const confirm = ref<{ open: boolean; title: string; message: string; requireText?: string; delayMs?: number; onConfirm?: ConfirmFn }>({ open: false, title: '', message: '' });
function openConfirm(opts: { title: string; message: string; requireText?: string; delayMs?: number; onConfirm: ConfirmFn }) {
  confirm.value.title = opts.title;
  confirm.value.message = opts.message;
  confirm.value.requireText = opts.requireText;
  confirm.value.delayMs = opts.delayMs;
  confirm.value.onConfirm = async () => { try { await opts.onConfirm(); } finally { confirm.value.open = false; } };
  confirm.value.open = true;
}

// History state
const showHistory = ref(false);
const history = ref<any[]>([]);
const histError = ref<string | null>(null);

function relativeTime(ts?: string) {
  if (!ts) return '-';
  try { const d = new Date(ts); const diff = (Date.now() - d.getTime())/1000; if (diff < 60) return Math.floor(diff)+'s ago'; if (diff < 3600) return Math.floor(diff/60)+'m ago'; if (diff < 86400) return Math.floor(diff/3600)+'h ago'; return d.toLocaleString(); } catch { return ts; }
}
function fmt(v: any) { if (v===null || v===undefined) return '-'; if (typeof v === 'number') return v.toFixed(3); return v; }
function formatMetric(v:any){ if (v===null||v===undefined) return '-'; if (typeof v==='number'){ if(Math.abs(v) < 0.0001) return v.toExponential(2); return v.toFixed(4);} return v; }
const prodMetrics = computed(() => {
  if (!production.value) return {};
  const m = production.value.metrics || {}; const keys = ['auc','ece','brier','accuracy'];
  const out: Record<string, any> = {};
  keys.forEach(k => { if (m[k] !== undefined) out[k] = m[k]; });
  return out;
});

const MODEL_NAME = 'bottom_predictor';

async function refresh(){
  loading.value = true; error.value = null;
  try {
    const r = await fetch(`/api/models/summary?limit=6&name=${encodeURIComponent(MODEL_NAME)}&model_type=supervised`, { headers: buildApiKeyHeaders() });
    if(!r.ok){ throw new Error(`HTTP ${r.status}`); }
    const data = await r.json();
    hasModel.value = !!data.has_model;
    production.value = data.production || null;
    // Normalize backend field names for recommendation flag
    recent.value = (data.recent || []).map((row: any) => ({
      ...row,
      promotion_recommend: (row.promotion_recommend ?? row.promote_recommend ?? false)
    }));
  } catch(e: any){ error.value = e.message || String(e); }
  finally { loading.value = false; }
}

function formatDelta(v:any){
  if (v===null || v===undefined) return '-';
  if (typeof v !== 'number') return v;
  const sign = v>0?'+':'';
  return sign + v.toFixed(4);
}
function deltaClass(v:any, auc:boolean){
  if (v===null || v===undefined || typeof v!=='number') return 'text-neutral-400';
  if (auc){
    if (v > 0.01) return 'text-emerald-400';
    if (v < -0.005) return 'text-rose-400';
    return 'text-neutral-300';
  } else { // ECE (lower is better) -> negative improvement good
    if (v < -0.002) return 'text-emerald-400';
    if (v > 0.004) return 'text-rose-400';
    return 'text-neutral-300';
  }
}

function formatPromoteReason(r?: string){
  if(!r) return '기준 미충족';
  if(r === 'criteria_met') return '기준 충족';
  if(r === 'baseline_production') return '프로덕션 기준 모델';
  if(r === 'no_production_baseline') return '비교 기준 없음';
  // compress internal reason list like "missing_val_samples,val_samples<800,auc_low,ece_ok"
  try {
    const parts = r.split(',').slice(0,5);
    return parts.join(', ');
  } catch { return r; }
}

onMounted(() => { refresh(); setInterval(refresh, 30000); });

// no-op: single model family

async function promoteRow(m: ModelRow){
  if (!m || !m.id) return;
  if (promotingId.value !== null) return;
  openConfirm({
    ...confirmPresets.modelPromote(m.version),
    onConfirm: async () => {
      promotingId.value = m.id;
      actionInfo.value = null;
      try {
        const r = await fetch(`/api/models/${m.id}/promote`, {
          method: 'POST',
          headers: buildApiKeyHeaders()
        });
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        const j = await r.json();
        if (j && j.promoted) {
          actionInfo.value = `버전 ${m.version} 승격 완료`;
        } else {
          actionInfo.value = `승격 실패: ${(j && (j.reason || j.error)) || 'unknown'}`;
        }
        await refresh();
      } catch (e: any) {
        actionInfo.value = `승격 오류: ${e?.message || String(e)}`;
      } finally {
        promotingId.value = null;
        window.setTimeout(() => { actionInfo.value = null; }, 4000);
      }
    }
  });
}

function toggleHistory(){
  showHistory.value = !showHistory.value;
  if (showHistory.value && history.value.length === 0) {
    loadHistory();
  }
}

async function loadHistory(){
  histError.value = null;
  try {
  const res = await fetch(`/api/models/production/history?limit=8&name=${encodeURIComponent(MODEL_NAME)}&model_type=supervised`, { headers: buildApiKeyHeaders() });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const j = await res.json();
    history.value = j?.rows || [];
  } catch(e:any){
    histError.value = e?.message || String(e);
  }
}

async function rollbackTo(row: any){
  if (!row || !row.id) return;
  openConfirm({
    ...confirmPresets.modelRollback(row.version),
    onConfirm: async () => {
      try {
        const r = await fetch(`/api/models/${row.id}/rollback`, {
          method: 'POST',
          headers: buildApiKeyHeaders()
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (j?.status === 'ok') {
          actionInfo.value = `프로덕션을 ${row.version} 으로 롤백했습니다`;
          await refresh();
          await loadHistory();
        } else {
          actionInfo.value = `롤백 실패: ${(j && (j.reason||j.error)) || 'unknown'}`;
        }
      } catch(e:any){
        actionInfo.value = `롤백 오류: ${e?.message || String(e)}`;
      } finally {
        window.setTimeout(() => { actionInfo.value = null; }, 4000);
      }
    }
  });
}

async function deleteRow(m: ModelRow){
  if (!m || !m.id) return;
  openConfirm({
    ...confirmPresets.modelDelete(m.version),
    onConfirm: async () => {
      try {
        const r = await fetch(`/api/models/${m.id}`, {
          method: 'DELETE',
          headers: buildApiKeyHeaders()
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (j?.status === 'deleted') {
          actionInfo.value = `버전 ${m.version} 삭제 완료`;
          await refresh();
          if (showHistory.value) await loadHistory();
        } else {
          actionInfo.value = `삭제 실패: ${(j && (j.reason||j.error)) || 'unknown'}`;
        }
      } catch(e:any){
        actionInfo.value = `삭제 오류: ${e?.message || String(e)}`;
      } finally {
        window.setTimeout(() => { actionInfo.value = null; }, 4000);
      }
    }
  });
}
</script>

<style scoped>
 table { table-layout: fixed; }
 th, td { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
