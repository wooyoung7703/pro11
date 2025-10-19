<template>
  <div class="min-h-screen flex bg-neutral-950 text-neutral-200">
    <ToastContainer />
    <!-- jj -->
    <!-- Sidebar -->
    <aside class="hidden md:flex w-60 shrink-0 border-r border-neutral-800 bg-neutral-900/80 backdrop-blur-sm flex-col">
  <div class="h-14 flex items-center px-4 font-semibold tracking-wide text-brand-primary border-b border-neutral-800">XRP TRADER</div>
      <nav class="flex-1 overflow-y-auto px-2 py-3 space-y-6 text-sm">
        <div>
          <div class="nav-section-label">Main</div>
          <ul class="space-y-1">
            <li v-for="item in mainNav" :key="item.label">
              <RouterLink :to="item.to" class="side-link" :class="{ 'side-link-active': isLinkActive(item) }">{{ item.label }}</RouterLink>
            </li>
          </ul>
        </div>
        <div>
          <div class="nav-section-label">Admin</div>
          <ul class="space-y-1">
            <li v-for="a in adminNav" :key="a.label">
              <RouterLink :to="a.to" class="side-link" :class="{ 'side-link-active': isAdminLinkActive(a) }">{{ a.label }}</RouterLink>
            </li>
          </ul>
        </div>
      </nav>
      <div class="p-3 border-t border-neutral-800 space-y-3">
        <div class="flex flex-col gap-1">
          <label class="text-[10px] uppercase tracking-wider text-neutral-500">API Key</label>
          <input v-model="apiKey" placeholder="API Key" class="bg-neutral-800 border border-neutral-700 text-[11px] rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-brand-primary" @input="persistKey" />
        </div>
        <div class="flex items-center justify-between text-[11px]">
          <span class="text-neutral-500">Theme</span>
          <button class="btn !py-1 !px-2" @click="toggleDark">{{ dark ? '라이트' : '다크' }}</button>
        </div>
        <div class="text-[10px] leading-tight text-neutral-500">
          <div class="uppercase tracking-wider mb-1">Backend</div>
          <div class="font-mono break-all text-neutral-400" :title="backendBase">{{ shortBackend }}</div>
        </div>
        <div class="text-[10px] text-neutral-600 text-center pt-2">© 2025 XRP System</div>
        <div class="text-[10px] text-neutral-600 text-center pt-1" v-if="buildSha">
          build: {{ buildSha.slice(0,7) }}
        </div>
      </div>
    </aside>
    <!-- Main Content -->
    <div class="flex-1 flex flex-col min-w-0">
      <!-- Mobile Top Navbar -->
      <header class="md:hidden fixed top-0 left-0 right-0 z-20 h-14 border-b border-neutral-800 bg-neutral-900/80 backdrop-blur-sm flex items-center justify-between px-4 safe-area-top">
        <div class="font-semibold tracking-wide text-brand-primary">XRP TRADER</div>
        <RouterLink :to="{ path: '/admin', query: { tab: 'actions' } }" class="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded border border-neutral-700 bg-neutral-800 hover:bg-neutral-700 transition">
          <span>관리자</span>
        </RouterLink>
      </header>
  <main class="flex-1 px-4 md:px-6 w-full pt-16 md:pt-6 pb-20 md:pb-6 safe-main-bottom">
        <template v-if="!modelGate.ready">
          <FirstModelLoading :hint="modelGate.hint" />
        </template>
        <template v-else>
          <RouterView />
        </template>
      </main>
      <!-- Mobile Bottom Tab Bar -->
      <nav class="md:hidden mobile-tabbar fixed bottom-0 left-0 right-0 z-50 h-14 w-full border-t border-neutral-800 bg-neutral-900/80 backdrop-blur-sm safe-area-bottom" role="tablist" aria-label="하단 탭바">
        <ul class="h-full grid grid-cols-6 text-[11px]">
          <li v-for="t in mobileTabs" :key="t.label" class="flex items-center justify-center">
            <RouterLink :to="t.to" role="tab" :aria-current="isLinkActive(t) ? 'page' : undefined" class="flex flex-col items-center justify-center px-2 py-1 rounded-md"
              :class="{ 'text-brand-accent font-semibold': isLinkActive(t), 'text-neutral-300': !isLinkActive(t) }">
              <span class="w-5 h-5 mb-0.5" aria-hidden="true" v-html="t.icon"></span>
              <span>{{ t.label }}</span>
            </RouterLink>
          </li>
        </ul>
      </nav>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed, reactive } from 'vue';
import { RouterLink, RouterView, useRoute } from 'vue-router';
import ToastContainer from './components/ToastContainer.vue';
import { getApiKey, setApiKey } from './lib/apiKey';
import http from './lib/http';
import FirstModelLoading from './components/FirstModelLoading.vue';

// Initialize API key from helper (localStorage/env/runtime) without insecure defaults
const apiKey = ref(getApiKey() ?? '');
function persistKey() { setApiKey(apiKey.value); }
persistKey();

const dark = ref(true);
function applyTheme() {
  const root = document.documentElement.classList;
  if (dark.value) root.add('dark'); else root.remove('dark');
}
function toggleDark() { dark.value = !dark.value; applyTheme(); }
onMounted(() => {
  applyTheme();
  startModelGate();
});

const route = useRoute();

// Left nav definitions
interface SimpleNav { label: string; to: any; match?: (r: typeof route) => boolean; icon?: string }
const mainNav: SimpleNav[] = [
  { label: '대시보드', to: '/' },
  { label: '드리프트', to: '/drift' },
  { label: '메트릭', to: '/metrics' },
  { label: '학습', to: '/training' },
  { label: '트레이딩', to: '/trading' },
  { label: '추론', to: '/inference' },
  { label: '보정', to: '/calibration' },
  { label: '리스크', to: '/risk' },
  { label: '시세(OHLCV)', to: '/ohlcv' },
];

// Expose each admin tab individually
function adminTabLink(tab: string, label: string): SimpleNav {
  return { label, to: { path: '/admin', query: { tab } }, match: (r) => r.path.startsWith('/admin') && r.query.tab === tab };
}
const adminNav: SimpleNav[] = [
  adminTabLink('actions', '관리자 작업'),
];

function isLinkActive(item: SimpleNav) {
  // 활성 링크 판정: match 함수가 있으면 사용, 아니면 경로 비교/접두사 비교
  if (item.match) return item.match(route);
  if (typeof item.to === 'string') return route.path === item.to;
  if (item.to && typeof item.to === 'object' && 'path' in item.to) {
    try { return route.path.startsWith((item.to as any).path); } catch { return false; }
  }
  return false;
}
function isAdminLinkActive(item: SimpleNav) { return isLinkActive(item); }

// Mobile bottom tabs (Dashboard, Trading, Training, Inference, Calibration, OHLCV)
const mobileTabs: SimpleNav[] = [
  { label: '대시보드', to: '/', match: (r) => r.path === '/', icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 12l9-9 9 9"/><path d="M4 10v10a1 1 0 001 1h4v-6h6v6h4a1 1 0 001-1V10"/></svg>' },
  { label: '트레이딩', to: '/trading', match: (r) => r.path.startsWith('/trading'), icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 17l6-6 4 4 7-7"/><path d="M14 7h7v7"/></svg>' },
  { label: '학습', to: '/training', match: (r) => r.path.startsWith('/training'), icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M7 20h10"/></svg>' },
  { label: '추론', to: '/inference', match: (r) => r.path.startsWith('/inference'), icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="4"/><path d="M2 12h4M18 12h4M12 2v4M12 18v4"/></svg>' },
  { label: '보정', to: '/calibration', match: (r) => r.path.startsWith('/calibration'), icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 12h16"/><path d="M12 4v16"/></svg>' },
  { label: '시세', to: '/ohlcv', match: (r) => r.path.startsWith('/ohlcv'), icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 19V5"/><path d="M8 19V9"/><path d="M12 19V13"/><path d="M16 19V11"/><path d="M20 19V7"/></svg>' },
];

// Backend base URL 표시 (프록시/환경)
const backendBase = ((): string => {
  // Vite dev proxy target 또는 환경변수 저장을 위해 시도
  // window.__BACKEND_BASE 가 있다면 우선
  const injected = (typeof window !== 'undefined' && (window as any).__BACKEND_BASE)
    ? (window as any).__BACKEND_BASE
    : '';
  const envGuess = (typeof location !== 'undefined') ? `${location.protocol}//${location.host}` : '';
  return injected || envGuess || 'unknown';
})();
const shortBackend = computed(() => {
  if (!backendBase) return '-';
  try {
    const u = new URL(backendBase);
    return u.host + (u.pathname !== '/' ? u.pathname : '');
  } catch { return backendBase; }
});

// Build meta exposed by build-meta.js (injected in Dockerfile)
const buildSha = (typeof window !== 'undefined' && (window as any).__BUILD_SHA)
  ? String((window as any).__BUILD_SHA)
  : '';

// --- Model-ready gate: show loading until first model exists ---
const modelGate = reactive<{ ready: boolean; hint: string | null; tries: number; timer: any | null }>({ ready: false, hint: null, tries: 0, timer: null });
async function checkModelReady(): Promise<boolean> {
  try {
    const { data } = await http.get('/api/models/summary', { params: { name: 'bottom_predictor', limit: 1, model_type: 'supervised' } });
    const has = !!(data && data.has_model);
    if (has) return true;
    // Extra hint: display backend feature status briefly
    try {
      const { data: st } = await http.get('/admin/features/status');
      const lag = st?.lag_seconds;
      modelGate.hint = typeof lag === 'number' ? `피처 준비 대기… (lag ${lag.toFixed(0)}s)` : '피처 준비 대기…';
    } catch { /* ignore */ }
    return false;
  } catch (e: any) {
    const msg = (e && (e.__friendlyMessage || e.message)) || '네트워크 오류';
    modelGate.hint = `백엔드 확인 중: ${msg}`;
    return false;
  }
}
function startModelGate() {
  // If key missing, still allow polling; backend may respond 401, we surface a hint but keep polling.
  if (modelGate.timer) return;
  const tick = async () => {
    modelGate.tries += 1;
    const ok = await checkModelReady();
    if (ok) {
      modelGate.ready = true;
      if (modelGate.timer) { clearInterval(modelGate.timer); modelGate.timer = null; }
      return;
    }
  };
  // immediate try, then poll every 2s up to a soft cap; keep polling until ready
  tick();
  modelGate.timer = setInterval(tick, 2000);
}
</script>

<style>
/* Sidebar specific styles (avoid @apply for TS checker compatibility) */
.nav-section-label { font-size:10px; text-transform:uppercase; letter-spacing:.08em; color:#6b7280; padding-left:0.5rem; margin-bottom:0.25rem; }
.side-link { display:block; padding:0.5rem 0.75rem; border-radius:0.375rem; color:#d1d5db; font-size:0.875rem; transition:background-color .15s ease,color .15s ease; }
.side-link:hover { background-color:rgba(55,65,81,0.4); }
.side-link-active { background-color:rgba(55,65,81,0.7); color: var(--color-brand-accent,#38bdf8); font-weight:600; }
/* Safe-area helpers for iOS devices */
.safe-area-top { padding-top: env(safe-area-inset-top); }
.safe-area-bottom { padding-bottom: env(safe-area-inset-bottom); }
/* main content bottom padding to avoid being covered by fixed tab bar (h-14 = 3.5rem) */
.safe-main-bottom { padding-bottom: calc(3.5rem + env(safe-area-inset-bottom) + 0.75rem); }
.mobile-tabbar { height: calc(3.5rem + env(safe-area-inset-bottom)); }
</style>
