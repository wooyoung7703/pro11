<template>
  <div class="space-y-4">
    <section class="card p-0 overflow-hidden">
      <div class="border-b border-neutral-800 bg-neutral-900/60 flex flex-wrap">
        <button
          v-for="t in tabs"
          :key="t.key"
          class="px-4 py-2 text-xs font-medium tracking-wide border-r border-neutral-800 hover:bg-neutral-800/60 transition"
          :class="activeTab === t.key ? 'bg-neutral-800 text-brand-accent' : 'text-neutral-400'"
          @click="setTab(t.key)"
        >
          <span>{{ t.label }}</span>
          <span v-if="t.key==='promotion_alerts' && promotionAlertState?.in_cooldown" class="ml-1 inline-block w-2 h-2 rounded-full bg-amber-400 animate-pulse" title="Promotion alerts cooldown active"></span>
          <span v-if="t.key==='inference' && seedFallbackState.active" class="ml-1 inline-block w-2 h-2 rounded-full bg-rose-500 animate-pulse" :title="seedFallbackTooltip" />
        </button>
        <div class="ml-auto px-3 py-2 text-[10px] text-neutral-500 flex items-center gap-2">
          <button v-if="ingestionStale" class="px-2 py-0.5 rounded bg-amber-600/20 text-amber-300 hover:bg-amber-600/30 transition" title="Ingestion stale (click to open)" @click="setTab('ingestion')">INGESTION STALE</button>
          <span class="hidden md:inline">ADMIN CONSOLE</span>
          <span class="font-mono text-neutral-400" title="Last change">{{ buildTs }}</span>
        </div>
      </div>
      <div class="p-4">
        <keep-alive v-if="currentComponent">
          <component :is="currentComponent" :key="activeTab" />
        </keep-alive>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, defineAsyncComponent } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import http from '../lib/http';
import { buildApiKeyHeaders } from '../lib/apiKey';

// Import views as async components to avoid default export typing issues (disable Suspense to avoid hydration issues)
const makeAsync = (loader: () => Promise<any>) => defineAsyncComponent({ loader, suspensible: false });
const AdminPanel = makeAsync(() => import('./AdminPanel.vue'));
const RiskMetrics = makeAsync(() => import('./RiskMetrics.vue'));
const InferencePlayground = makeAsync(() => import('./InferencePlayground.vue'));
const Calibration = makeAsync(() => import('./Calibration.vue'));
const TrainingJobs = makeAsync(() => import('./TrainingJobs.vue'));
const ModelMetrics = makeAsync(() => import('./ModelMetrics.vue'));
const FeatureDrift = makeAsync(() => import('./FeatureDrift.vue'));
const NewsView = makeAsync(() => import('./NewsView.vue'));
const JobCenter = makeAsync(() => import('./JobCenter.vue'));
const IngestionStatus = makeAsync(() => import('./IngestionStatus.vue'));
const PromotionAudit = makeAsync(() => import('./PromotionAudit.vue'));
const ModelsSummary = makeAsync(() => import('./ModelsSummary.vue'));
const PromotionAlertSettings = makeAsync(() => import('./PromotionAlertSettings.vue'));

interface TabDef { key: string; label: string; comp: any; }
const tabs: TabDef[] = [
  { key: 'actions', label: 'Actions', comp: AdminPanel },
  { key: 'jobs', label: 'Job Center', comp: JobCenter },
  { key: 'risk', label: 'Risk', comp: RiskMetrics },
  { key: 'inference', label: 'Inference', comp: InferencePlayground },
  { key: 'calibration', label: 'Calibration', comp: Calibration },
  { key: 'training', label: 'Training', comp: TrainingJobs },
  { key: 'metrics', label: 'Metrics', comp: ModelMetrics },
  { key: 'models', label: 'Models', comp: ModelsSummary },
  { key: 'drift', label: 'Drift', comp: FeatureDrift },
  { key: 'news', label: 'News', comp: NewsView },
  { key: 'ingestion', label: 'Ingestion', comp: IngestionStatus },
  { key: 'promotion_audit', label: 'Promotion', comp: PromotionAudit },
  { key: 'promotion_alerts', label: 'Promotion Alerts', comp: PromotionAlertSettings },
];

const route = useRoute();
const router = useRouter();
const activeTab = ref<string>('actions');
const ingestionStale = ref(false);
let ingestTimer: any = null;

function setTab(k: string) {
  if (!tabs.find(t => t.key === k)) return;
  activeTab.value = k;
}

const currentComponent = computed(() => tabs.find(t => t.key === activeTab.value)?.comp || AdminPanel);

// Promotion alert status (for badge)
const promotionAlertState = ref<any|null>(null);
const seedFallbackState = ref<{active:boolean; duration_seconds:number; last_exit_ts?:number; started_at?:number}>({active:false, duration_seconds:0});
const seedFallbackTooltip = computed(() => {
  const s = seedFallbackState.value;
  if (s.active) {
    const secs = Math.floor(s.duration_seconds || 0);
    const lastExit = s.last_exit_ts ? new Date(s.last_exit_ts*1000).toLocaleString() : 'N/A';
    const started = s.started_at ? new Date(s.started_at*1000).toLocaleString() : 'N/A';
    return `Seed fallback active ${secs}s\nStarted: ${started}\nLast exit: ${lastExit}`;
  }
  if (!s.active && s.last_exit_ts) {
    const healthyUptime = (s as any).healthy_uptime_seconds ? Math.floor((s as any).healthy_uptime_seconds) : null;
    const lastExit = new Date(s.last_exit_ts*1000).toLocaleString();
    return healthyUptime !== null
      ? `Healthy since last fallback: ${healthyUptime}s\nLast exit: ${lastExit}`
      : `Last fallback exit: ${lastExit}`;
  }
  return '';
});
async function refreshPromotionAlertState() {
  try {
    const response = await fetch('/api/models/promotion/alert/status', { headers: buildApiKeyHeaders() });
    if (response.ok) promotionAlertState.value = await response.json();
  } catch { /* ignore */ }
}
async function refreshSeedFallbackState() {
  try {
    const response = await fetch('/api/inference/seed/status', { headers: buildApiKeyHeaders() });
    if (response.ok) seedFallbackState.value = await response.json();
  } catch { /* ignore */ }
}

// build timestamp (best-effort runtime) for display
const buildTs = new Date().toLocaleTimeString();

// Sync with query param ?tab=
onMounted(async () => {
  const q = route.query.tab;
  if (typeof q === 'string' && tabs.some(t => t.key === q)) {
    activeTab.value = q;
  } else {
    // restore from DB-backed UI pref (fallback local)
    try {
      const { getUiPref } = await import('../lib/uiSettings');
      const saved = await getUiPref<string>('admin_active_tab');
      if (saved && tabs.some(t => t.key === saved)) activeTab.value = saved;
    } catch { /* ignore */ }
  }
  refreshPromotionAlertState();
  refreshSeedFallbackState();
  setInterval(refreshPromotionAlertState, 30000);
  setInterval(refreshSeedFallbackState, 15000);
  // poll ingestion status for global stale badge
  const pollIngestion = async () => {
    try {
      const r = await http.get('/api/ingestion/status');
      const d: any = r.data || {};
      // Robust: stale=true or lag_sec/lag_seconds over threshold, or last_message_ts too old (> threshold)
      const threshold = (d?.thresholds?.ingestion_lag_sec || 60) as number;
      const lagSec = typeof d?.lag_sec === 'number' ? d.lag_sec
        : typeof d?.lag_seconds === 'number' ? d.lag_seconds
        : (typeof d?.last_message_ts === 'number' ? (Date.now()/1000 - d.last_message_ts) : null);
      ingestionStale.value = d?.stale === true || (typeof lagSec === 'number' && lagSec > threshold);
    } catch { /* ignore */ }
  };
  pollIngestion();
  ingestTimer = setInterval(pollIngestion, 10000);
});

onBeforeUnmount(() => { if (ingestTimer) clearInterval(ingestTimer); });

watch(activeTab, async (v: string) => {
  try {
    const { setUiPref } = await import('../lib/uiSettings');
    await setUiPref('admin_active_tab', v);
  } catch { /* ignore */ }
  router.replace({ query: { ...route.query, tab: v } });
});
</script>

<style scoped>
button { cursor: pointer; }
</style>