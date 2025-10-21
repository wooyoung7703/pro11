import { ref, onBeforeUnmount } from 'vue';
import http from '@/lib/http';
import { getApiKey } from '@/lib/apiKey';

export function useBackendStatus(pollMs = 15000) {
  const serverOk = ref<boolean | null>(null);
  const readyStatus = ref<'unknown' | 'ready' | 'not_ready'>('unknown');
  const reasons = ref<string[]>([]);
  const authOk = ref<boolean | null>(null);
  const lastChecked = ref<number | null>(null);
  const loading = ref(false);
  let timer: any = null;

  async function pollOnce() {
    loading.value = true;
    lastChecked.value = Date.now();
    try {
      // 1) Unauthenticated readiness
      try {
        const r = await fetch('/health/ready');
        if (!r.ok) throw new Error('health_not_ok');
        const data = await r.json().catch(() => ({}));
        serverOk.value = true;
        const st = (data?.status as string) || 'unknown';
        readyStatus.value = st === 'ready' ? 'ready' : st === 'not_ready' ? 'not_ready' : 'unknown';
        reasons.value = Array.isArray(data?.reasons) ? data.reasons : [];
      } catch {
        serverOk.value = false;
        readyStatus.value = 'unknown';
        reasons.value = [];
      }

      // 2) Auth check via lightweight admin endpoint
      try {
        // Only attempt if we have some API key to try; otherwise it's clearly unauthenticated
        const hasKey = !!getApiKey();
        if (!hasKey) {
          authOk.value = false;
        } else {
          await http.get('/admin/inference/thresholds');
          authOk.value = true;
        }
      } catch (e: any) {
        const status = e?.response?.status || e?.status;
        authOk.value = status === 401 ? false : null;
      }
    } finally {
      loading.value = false;
    }
  }

  function start() {
    if (timer) clearInterval(timer);
    timer = setInterval(pollOnce, Math.max(2000, pollMs));
    // immediate first run
    pollOnce();
  }
  function stop() { if (timer) clearInterval(timer); timer = null; }

  onBeforeUnmount(stop);

  return { serverOk, readyStatus, reasons, authOk, lastChecked, loading, pollOnce, start, stop };
}
