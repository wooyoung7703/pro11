import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import http from '@/lib/http';
import { useOhlcvStore } from './ohlcv';

interface ReliabilityBin {
  bin_lower?: number;
  bin_upper?: number;
  count?: number;
  mean_pred_prob?: number | null;
  empirical_prob?: number | null;
  gap?: number | null;
  // production variant keys (from registry metrics) may differ
  mean_prob?: number; // fallback naming
  empirical?: number; // fallback naming
  abs_diff?: number;  // fallback naming
}

interface CalibrationSnapshot {
  brier?: number | null;
  ece?: number | null;
  mce?: number | null;
  reliability_bins?: ReliabilityBin[];
}

interface MonitorStatus {
  enabled?: boolean;
  labeler_enabled?: boolean;
  abs_streak?: number;
  rel_streak?: number;
  last_snapshot?: any; // raw structure from backend
  thresholds?: any;
  window_seconds?: number;
  min_samples?: number;
  interval?: number;
  recommend_retrain?: boolean;
  recommend_retrain_reasons?: string[];
  calibration_retrain_delta_before?: number | null;
  calibration_retrain_delta_improvement?: number | null;
}

interface FeatureDriftRow { feature: string; z_score: number; drift: boolean; }

export const useCalibrationStore = defineStore('calibration', () => {
  const loading = ref(false);
  const error = ref<string | null>(null);
  const prod = ref<CalibrationSnapshot | null>(null);
  const prodVersion = ref<string | number | null>(null);
  const live = ref<CalibrationSnapshot | null>(null);
  const liveWindowSeconds = ref(3600);
  const liveBins = ref(10);
  const monitor = ref<MonitorStatus | null>(null);
  const driftFeatures = ref<FeatureDriftRow[]>([]);
  const lastUpdated = ref<number | null>(null);
  const auto = ref(true);
  const intervalSec = ref(15);
  const autoLabelerTriggeredAt = ref<number | null>(null);
  let timer: any = null;
  let autoLabelerRefetchTimer: any = null;

  const deltaECE = computed(() => {
    const l = live.value?.ece; const p = monitor.value?.last_snapshot?.prod_ece ?? prod.value?.ece;
    if (typeof l === 'number' && typeof p === 'number') return Math.abs(l - p);
    return null;
  });
  const recommendation = computed(() => monitor.value?.recommend_retrain === true);
  const reasons = computed(() => monitor.value?.recommend_retrain_reasons || []);

  const topDrift = computed(() => {
    return driftFeatures.value
      .slice()
      .sort((a,b) => Math.abs(b.z_score) - Math.abs(a.z_score))
      .slice(0,5);
  });

  function normalizeProdCalibration(raw: any): CalibrationSnapshot | null {
    if (!raw) return null;
    // production registry may store reliability_bins with different key names
    const bins = (raw.reliability_bins || []).map((b: any) => ({
      bin_lower: b.bin_lower ?? b.bin ?? b.lower,
      bin_upper: b.bin_upper ?? b.upper,
      count: b.count,
      mean_pred_prob: b.mean_pred_prob ?? b.mean_prob,
      empirical_prob: b.empirical_prob ?? b.empirical,
      gap: b.gap ?? b.abs_diff,
    }));
    return {
      brier: raw.brier,
      ece: raw.ece,
      mce: raw.mce,
      reliability_bins: bins,
    };
  }

  function normalizeLiveCalibration(raw: any): CalibrationSnapshot | null {
    if (!raw) return null;
    return {
      brier: raw.brier,
      ece: raw.ece,
      mce: raw.mce,
      reliability_bins: raw.reliability_bins,
    };
  }

  async function fetchProductionCalibration() {
    const r = await http.get('/api/models/production/calibration');
    if (r.data.status !== 'ok') return;
    prod.value = normalizeProdCalibration(r.data.calibration);
    prodVersion.value = r.data.model_version;
  }

  async function fetchLiveCalibration() {
    const ohlcv = useOhlcvStore();
    const params: any = { window_seconds: liveWindowSeconds.value, bins: liveBins.value };
    // Pinia store unwraps refs on the store instance; access without .value
    if (ohlcv.symbol) params.symbol = ohlcv.symbol;
    if (ohlcv.interval) params.interval = ohlcv.interval;
  // Bottom-only target
  params.target = 'bottom';
    const r = await http.get('/api/inference/calibration/live', { params });
    if (r.data.status !== 'ok') {
      if (r.data.status === 'no_data') {
        live.value = null;
        if (r.data.auto_labeler_triggered) {
          autoLabelerTriggeredAt.value = Date.now();
          if (autoLabelerRefetchTimer) clearTimeout(autoLabelerRefetchTimer);
          autoLabelerRefetchTimer = setTimeout(() => {
            autoLabelerRefetchTimer = null;
            fetchLiveCalibration().catch(() => undefined);
          }, 1500);
        }
      } else {
        error.value = r.data.status;
        live.value = null;
      }
      return;
    }
    live.value = normalizeLiveCalibration(r.data);
  }

  async function fetchMonitor() {
    const r = await http.get('/api/monitor/calibration/status');
    monitor.value = r.data;
  }

  async function fetchDrift() {
    try {
      const r = await http.get('/api/features/drift/scan');
      const rows: FeatureDriftRow[] = [];
      const data = r.data?.features || r.data?.results || r.data;
      if (Array.isArray(data)) {
        for (const f of data) {
          const z = f.z_score ?? f.z ?? 0;
            rows.push({ feature: f.feature || f.name || 'unknown', z_score: z, drift: !!f.drift });
        }
      }
      driftFeatures.value = rows;
    } catch {
      // non-fatal
    }
  }

  async function fetchAll() {
    loading.value = true; error.value = null;
    try {
      await Promise.all([
        fetchProductionCalibration(),
        fetchLiveCalibration(),
        fetchMonitor(),
        fetchDrift(),
      ]);
      lastUpdated.value = Date.now();
    } catch (e:any) {
      error.value = e.__friendlyMessage || e.message || 'fetch failed';
    } finally {
      loading.value = false;
    }
  }

  function startAuto() {
    stopAuto();
    if (auto.value) {
      timer = setInterval(fetchAll, Math.max(5, intervalSec.value) * 1000);
    }
  }
  function stopAuto() {
    if (timer) clearInterval(timer);
    timer = null;
    if (autoLabelerRefetchTimer) {
      clearTimeout(autoLabelerRefetchTimer);
      autoLabelerRefetchTimer = null;
    }
  }
  function toggleAuto(v?: boolean) { auto.value = typeof v === 'boolean' ? v : !auto.value; if (auto.value) fetchAll(); startAuto(); }
  function setIntervalSec(v: number) { intervalSec.value = Math.max(5, v); startAuto(); }
  function setLiveWindowSeconds(v: number) { liveWindowSeconds.value = Math.max(60, Math.floor(v)); fetchLiveCalibration(); }
  function setLiveBins(v: number) { liveBins.value = Math.min(50, Math.max(5, Math.floor(v))); fetchLiveCalibration(); }

  return {
    loading, error, prod, prodVersion, live, monitor, driftFeatures, lastUpdated,
    auto, intervalSec, deltaECE, recommendation, reasons, topDrift,
    liveWindowSeconds, liveBins, autoLabelerTriggeredAt,
    fetchAll, startAuto, stopAuto, toggleAuto, setIntervalSec,
    setLiveWindowSeconds, setLiveBins,
  };
});