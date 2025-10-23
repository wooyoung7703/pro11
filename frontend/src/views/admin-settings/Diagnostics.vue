<template>
  <div class="space-y-3">
    <div class="flex items-center justify-between">
      <div class="text-sm text-neutral-400">서버 진단 및 핵심 설정 스냅샷</div>
      <div class="flex items-center gap-2">
        <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-neutral-800 text-neutral-300 border border-neutral-700 hover:bg-neutral-700/80 transition" @click="refreshHealth" :disabled="loading">Health</button>
        <button class="inline-flex items-center gap-2 px-3 py-1.5 rounded bg-sky-500/20 text-sky-300 border border-sky-500/40 hover:bg-sky-500/30 transition" @click="load" :disabled="loading">전체 새로고침</button>
      </div>
    </div>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <section class="p-3 bg-neutral-900/40 rounded border border-neutral-800">
        <div class="text-xs text-neutral-400 mb-2">Health</div>
        <div class="text-sm">
          <span class="inline-block px-2 py-0.5 rounded" :class="health==='ok' ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40' : 'bg-rose-500/20 text-rose-300 border border-rose-500/40'">{{ health || 'unknown' }}</span>
        </div>
      </section>
      <section class="p-3 bg-neutral-900/40 rounded border border-neutral-800">
        <div class="text-xs text-neutral-400 mb-2">Drift Settings (DB)</div>
        <div class="text-sm space-y-1 font-mono">
          <div>window: <span class="text-neutral-200">{{ drift.window ?? '-' }}</span></div>
          <div>threshold: <span class="text-neutral-200">{{ drift.threshold ?? '-' }}</span></div>
          <div>features: <span class="text-neutral-200 break-all">{{ drift.features || '-' }}</span></div>
          <div>auto.enabled: <span class="text-neutral-200">{{ drift.auto_enabled ?? '-' }}</span></div>
          <div>auto.interval_sec: <span class="text-neutral-200">{{ drift.auto_interval ?? '-' }}</span></div>
        </div>
        <div class="mt-3 flex items-center gap-2 text-[11px]">
          <button class="px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 border border-neutral-700" @click="runDriftScan" :disabled="loading">Drift Quick Scan</button>
          <span class="text-neutral-500" v-if="driftScan && driftScan.summary">drift: {{ driftScan.summary.drift_count }}/{{ driftScan.summary.total }}, max|Z|={{ driftScan.summary.max_abs_z?.toFixed(2) ?? '-' }}</span>
        </div>
      </section>
      <section class="p-3 bg-neutral-900/40 rounded border border-neutral-800 md:col-span-2">
        <div class="text-xs text-neutral-400 mb-2">Calibration Settings (DB)</div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm font-mono">
          <div>
            <div>live.window_seconds: <span class="text-neutral-200">{{ calib.window ?? '-' }}</span></div>
            <div>live.bins: <span class="text-neutral-200">{{ calib.bins ?? '-' }}</span></div>
            <div>eager.enabled: <span class="text-neutral-200">{{ calib.eager_enabled ?? '-' }}</span></div>
          </div>
          <div>
            <div>eager.limit: <span class="text-neutral-200">{{ calib.eager_limit ?? '-' }}</span></div>
            <div>eager.min_age_seconds: <span class="text-neutral-200">{{ calib.eager_min_age ?? '-' }}</span></div>
            <div>monitor.ece_abs / ece_rel: <span class="text-neutral-200">{{ calib.ece_abs ?? '-' }} / {{ calib.ece_rel ?? '-' }}</span></div>
          </div>
        </div>
        <div class="mt-3 flex items-center gap-2 text-[11px]">
          <button class="px-2 py-1 rounded bg-neutral-800 hover:bg-neutral-700 border border-neutral-700" @click="loadCalibLive" :disabled="loading">Calibration Live</button>
          <span class="text-neutral-500" v-if="calibLive">
            ECE={{ fmt(calibLive?.ece) }}, MCE={{ fmt(calibLive?.mce) }}, N={{ calibLive?.n ?? '-' }}
          </span>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import http from '../../lib/http';

const health = ref<string>('');
const drift = ref<{ window?: number; threshold?: number; features?: string; auto_enabled?: boolean; auto_interval?: number; }>({});
const calib = ref<{ window?: number; bins?: number; eager_enabled?: boolean; eager_limit?: number; eager_min_age?: number; ece_abs?: number; ece_rel?: number; }>({});
const driftScan = ref<any | null>(null);
const calibLive = ref<any | null>(null);
const loading = ref(false);

async function safeGet(path: string): Promise<any|undefined> {
  try { const { data } = await http.get(path); return data; } catch { return undefined; }
}
async function getSetting(key: string): Promise<any|undefined> {
  try { const { data } = await http.get(`/admin/settings/${encodeURIComponent(key)}`); return data?.item?.value; } catch { return undefined; }
}

async function load() {
  try {
    loading.value = true;
    await refreshHealth();
    drift.value = {
      window: await getSetting('drift.window'),
      threshold: await getSetting('drift.threshold'),
      features: await getSetting('drift.features'),
      auto_enabled: await getSetting('drift.auto.enabled'),
      auto_interval: await getSetting('drift.auto.interval_sec'),
    };
    calib.value = {
      window: await getSetting('calibration.live.window_seconds'),
      bins: await getSetting('calibration.live.bins'),
      eager_enabled: await getSetting('calibration.eager.enabled'),
      eager_limit: await getSetting('calibration.eager.limit'),
      eager_min_age: await getSetting('calibration.eager.min_age_seconds'),
      ece_abs: await getSetting('calibration.monitor.ece_abs'),
      ece_rel: await getSetting('calibration.monitor.ece_rel'),
    };
  } finally {
    loading.value = false;
  }
}

onMounted(load);

async function refreshHealth() {
  const h = await safeGet('/health');
  health.value = (h && h.status) || '';
}

async function runDriftScan() {
  try {
    loading.value = true;
    const w = typeof drift.value.window === 'number' ? drift.value.window : 200;
    const t = typeof drift.value.threshold === 'number' ? drift.value.threshold : undefined;
    const feats = (drift.value.features || 'ret_1,ret_5,ret_10,rsi_14,rolling_vol_20,ma_20,ma_50');
    const { data } = await http.get('/api/features/drift/scan', { params: { window: w, features: feats, threshold: t } });
    driftScan.value = data;
  } finally {
    loading.value = false;
  }
}

async function loadCalibLive() {
  try {
    loading.value = true;
    const { data } = await http.get('/api/inference/calibration/live');
    calibLive.value = data;
  } finally {
    loading.value = false;
  }
}

function fmt(v: any) {
  if (v === null || v === undefined) return '-';
  if (typeof v === 'number' && Number.isFinite(v)) return v.toFixed(3);
  return String(v);
}
</script>
