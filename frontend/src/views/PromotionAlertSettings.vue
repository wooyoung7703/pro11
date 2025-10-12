<template>
  <div class="space-y-6">
    <section class="card">
      <div class="flex items-center justify-between mb-4">
        <h1 class="text-xl font-semibold">Promotion Alert Settings</h1>
        <div class="flex items-center gap-2 text-xs">
          <button class="btn" :disabled="loading" @click="fetchStatus">새로고침</button>
          <button class="btn" :disabled="testLoading || !status?.webhook_configured" @click="sendTest">테스트 전송</button>
          <span v-if="error" class="px-2 py-0.5 rounded bg-brand-danger/20 text-brand-danger">{{ error }}</span>
          <span v-if="testError" class="px-2 py-0.5 rounded bg-brand-danger/20 text-brand-danger">TEST: {{ testError }}</span>
          <span v-if="testResult && testResult.status==='ok'" class="px-2 py-0.5 rounded bg-emerald-600/20 text-emerald-400">TEST OK ({{ testResult.code }})</span>
        </div>
      </div>
      <div v-if="status" class="grid gap-4 md:grid-cols-2 lg:grid-cols-3 text-sm">
        <div class="p-3 bg-neutral-800/40 rounded">
          <div class="text-neutral-400 text-xs">Enabled</div>
          <div class="font-mono" :class="status.enabled ? 'text-emerald-400':'text-neutral-500'">{{ status.enabled }}</div>
        </div>
        <div class="p-3 bg-neutral-800/40 rounded">
          <div class="text-neutral-400 text-xs">Webhook Configured</div>
          <div class="font-mono" :class="status.webhook_configured ? 'text-emerald-400':'text-rose-400'">{{ status.webhook_configured }}</div>
        </div>
        <div class="p-3 bg-neutral-800/40 rounded">
          <div class="text-neutral-400 text-xs">Min Window</div>
          <div class="font-mono">{{ status.min_window }}</div>
        </div>
        <div class="p-3 bg-neutral-800/40 rounded">
          <div class="text-neutral-400 text-xs">Low AUC Ratio</div>
          <div class="font-mono">{{ status.low_auc_ratio }}</div>
        </div>
        <div class="p-3 bg-neutral-800/40 rounded">
          <div class="text-neutral-400 text-xs">Low ECE Margin Ratio</div>
          <div class="font-mono">{{ status.low_ece_margin_ratio }}</div>
        </div>
        <div class="p-3 bg-neutral-800/40 rounded">
          <div class="text-neutral-400 text-xs">Cooldown (s)</div>
          <div class="font-mono">{{ status.cooldown_seconds }}</div>
        </div>
        <div class="p-3 bg-neutral-800/40 rounded col-span-full md:col-span-2">
          <div class="text-neutral-400 text-xs">Last Alert</div>
          <div class="font-mono">
            <span v-if="status.last_alert_ts">{{ relTime(status.last_alert_ts) }} ({{ fmtTime(status.last_alert_ts) }})</span>
            <span v-else class="text-neutral-500">—</span>
            <span v-if="status.in_cooldown" class="ml-2 px-2 py-0.5 rounded bg-amber-600/20 text-amber-400 text-[11px]">COOLDOWN</span>
          </div>
        </div>
      </div>
  <div v-else class="text-neutral-500 text-sm">No status</div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';

const status = ref<any|null>(null);
const loading = ref(false);
const error = ref<string|null>(null);
const testLoading = ref(false);
const testError = ref<string|null>(null);
const testResult = ref<any|null>(null);

async function fetchStatus() {
  loading.value = true; error.value = null;
  try {
    const r = await fetch('/api/models/promotion/alert/status', { headers: { 'X-API-Key': (window as any).API_KEY || 'dev-key' } });
    if (!r.ok) throw new Error('HTTP '+r.status);
    status.value = await r.json();
  } catch (e:any) {
    error.value = e.message || String(e);
  } finally { loading.value = false; }
}
async function sendTest() {
  testLoading.value = true; testError.value = null; testResult.value = null;
  try {
    const r = await fetch('/api/models/promotion/alert/test', { method:'POST', headers: { 'X-API-Key': (window as any).API_KEY || 'dev-key' } });
    const data = await r.json();
    testResult.value = data;
    if (data.status !== 'ok') {
      testError.value = data.error || data.reason || 'failed';
    }
  } catch (e:any) {
    testError.value = e.message || String(e);
  } finally { testLoading.value = false; }
}
function fmtTime(ts:number) { return new Date(ts*1000).toLocaleString(); }
function relTime(ts:number) {
  const diff = Date.now()/1000 - ts;
  if (diff < 60) return diff.toFixed(0)+'s ago';
  if (diff < 3600) return (diff/60).toFixed(1)+'m ago';
  if (diff < 86400) return (diff/3600).toFixed(1)+'h ago';
  return (diff/86400).toFixed(1)+'d ago';
}

onMounted(() => fetchStatus());
</script>

<style scoped>
</style>
