import axios from 'axios';
import type { AxiosError } from 'axios';
import { useToastStore } from '../stores/toast';
import { getApiKey } from './apiKey';

// Central axios instance that injects API key header (if present) and basic error normalization.
const http = axios.create({
  // baseURL can remain relative; Vite proxy will handle /api -> backend.
});

http.interceptors.request.use((config) => {
  try {
    const key = getApiKey();
    if (key) {
      config.headers = config.headers || {};
      (config.headers as any)['X-API-Key'] = key;
    }
  } catch (e) {
    // ignore
  }
  return config;
});

http.interceptors.response.use(
  (resp) => resp,
  (error: AxiosError) => {
    try {
      const toast = useToastStore();
      const status = error?.response?.status;
      const detail: any = (error?.response?.data as any)?.detail;
      const serverMsg = typeof detail === 'string' ? detail : undefined;
      const friendly = serverMsg || error.message || '요청 실패';
      // Attach friendly message for any component optionally inspecting
      (error as any).__friendlyMessage = friendly;
      // Extract request id from header (preferred) or response body for correlation
      let requestId: string | undefined;
      try {
        const headers: any = error?.response?.headers || {};
        requestId = headers['x-request-id'] || headers['X-Request-ID'];
        if (!requestId) {
          const bodyReqId = (error?.response?.data as any)?.request_id;
            if (typeof bodyReqId === 'string') requestId = bodyReqId;
        }
      } catch (_) {
        // ignore header parsing failures
      }
      (error as any).requestId = requestId;
      // Avoid spamming: skip 401 invalid api key on repeated polling? (basic heuristic)
  const url = (error.config?.url || '').toString();
  const isAuth = status === 401;
  const skipSpam = isAuth && (url.includes('/health') || url.startsWith('/admin'));
      if (!skipSpam) {
        const parts: string[] = [];
        if (status) parts.push(`HTTP ${status}`);
        if (url) parts.push(url);
        if (requestId) parts.push(`req:${requestId}`);
        toast.error(friendly, parts.join(' '));
      }
    } catch (_) {
      // ignore toast failures
    }
    return Promise.reject(error);
  }
);

export default http;