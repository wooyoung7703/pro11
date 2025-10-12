<template>
  <div class="min-h-screen flex bg-neutral-950 text-neutral-200">
    <ToastContainer />
    <!-- jj -->
    <!-- Sidebar -->
    <aside class="w-60 shrink-0 border-r border-neutral-800 bg-neutral-900/80 backdrop-blur-sm flex flex-col">
      <div class="h-14 flex items-center px-4 font-semibold tracking-wide text-brand-primary border-b border-neutral-800">XRP Console</div>
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
      </div>
    </aside>
    <!-- Main Content -->
    <div class="flex-1 flex flex-col min-w-0">
      <main class="flex-1 px-6 py-6 w-full">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import { RouterLink, RouterView, useRoute } from 'vue-router';
import ToastContainer from './components/ToastContainer.vue';

// Initialize API key with existing or fallback dev key (no import.meta dependency for TS simplicity)
// If you want to override, set localStorage.setItem('api_key', 'your-key') or edit .env with VITE_DEV_API_KEY and re-run dev server.
// We'll attempt to read from a global injected value if present (window.__DEV_API_KEY)
const injectedKey = (typeof window !== 'undefined' && (window as any).__DEV_API_KEY)
  ? (window as any).__DEV_API_KEY
  : '';
const apiKey = ref(localStorage.getItem('api_key') || injectedKey || 'dev-key');
function persistKey() { localStorage.setItem('api_key', apiKey.value); }
if (!localStorage.getItem('api_key') && apiKey.value) {
  localStorage.setItem('api_key', apiKey.value);
}

const dark = ref(true);
function applyTheme() {
  const root = document.documentElement.classList;
  if (dark.value) root.add('dark'); else root.remove('dark');
}
function toggleDark() { dark.value = !dark.value; applyTheme(); }
onMounted(applyTheme);

const route = useRoute();

// Left nav definitions
interface SimpleNav { label: string; to: any; match?: (r: typeof route) => boolean }
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
  adminTabLink('ingestion', '관리자 수집'),
  adminTabLink('news', '관리자 뉴스'),
  adminTabLink('drift', '관리자 드리프트'),
  adminTabLink('risk', '관리자 리스크'),
  adminTabLink('inference', '관리자 추론'),
  adminTabLink('calibration', '관리자 보정'),
  adminTabLink('training', '관리자 학습'),
  adminTabLink('metrics', '관리자 모델 메트릭'),
];

function isLinkActive(item: SimpleNav) {
  alert('99999');
  if (item.match) return item.match(route);
  return route.path === item.to;
}
function isAdminLinkActive(item: SimpleNav) { return isLinkActive(item); }

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
</script>

<style>
/* Sidebar specific styles (avoid @apply for TS checker compatibility) */
.nav-section-label { font-size:10px; text-transform:uppercase; letter-spacing:.08em; color:#6b7280; padding-left:0.5rem; margin-bottom:0.25rem; }
.side-link { display:block; padding:0.5rem 0.75rem; border-radius:0.375rem; color:#d1d5db; font-size:0.875rem; transition:background-color .15s ease,color .15s ease; }
.side-link:hover { background-color:rgba(55,65,81,0.4); }
.side-link-active { background-color:rgba(55,65,81,0.7); color: var(--color-brand-accent,#38bdf8); font-weight:600; }
</style>
