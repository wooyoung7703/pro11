const STORAGE_KEY = 'api_key';

function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof document !== 'undefined';
}

function safeGetLocalStorage(): Storage | null {
  if (!isBrowser()) return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function readFromLocalStorage(): string | null {
  const storage = safeGetLocalStorage();
  if (!storage) return null;
  try {
    const value = storage.getItem(STORAGE_KEY);
    return value ? value.trim() : null;
  } catch {
    return null;
  }
}

function readFromWindow(): string | null {
  if (!isBrowser()) return null;
  const win = window as any;
  const candidates = [win.__API_KEY, win.API_KEY, win.__DEV_API_KEY];
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.trim()) {
      return candidate.trim();
    }
  }
  return null;
}

function readFromEnv(): string | null {
  try {
    const env = (import.meta as any)?.env;
    const raw = env?.VITE_DEV_API_KEY;
    return (typeof raw === 'string' && raw.trim()) ? raw.trim() : null;
  } catch {
    return null;
  }
}

function resolveInitialKey(): string | null {
  return readFromLocalStorage() ?? readFromWindow() ?? readFromEnv();
}

let cachedKey: string | null = resolveInitialKey();

function persistToLocalStorage(value: string | null): void {
  const storage = safeGetLocalStorage();
  if (!storage) return;
  try {
    if (value) storage.setItem(STORAGE_KEY, value);
    else storage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

function persistToWindow(value: string | null): void {
  if (!isBrowser()) return;
  const win = window as any;
  if (value) win.__API_KEY = value;
  else delete win.__API_KEY;
}

if (cachedKey) {
  persistToLocalStorage(cachedKey);
  persistToWindow(cachedKey);
}

export function getApiKey(): string | null {
  if (cachedKey && cachedKey.trim()) return cachedKey.trim();
  cachedKey = resolveInitialKey();
  const normalized = cachedKey && cachedKey.trim() ? cachedKey.trim() : null;
  if (normalized) {
    persistToLocalStorage(normalized);
    persistToWindow(normalized);
  }
  return normalized;
}

export function setApiKey(value: string | null | undefined): void {
  const normalized = value ? value.trim() : '';
  cachedKey = normalized || null;
  persistToLocalStorage(cachedKey);
  persistToWindow(cachedKey);
}

export function buildApiKeyHeaders(extra?: HeadersInit): Headers {
  const headers = extra instanceof Headers
    ? new Headers(extra)
    : Array.isArray(extra)
      ? new Headers(extra)
      : new Headers(extra ?? {});
  const key = getApiKey();
  if (key) headers.set('X-API-Key', key);
  return headers;
}

export function hasApiKey(): boolean {
  return !!getApiKey();
}

export function clearApiKey(): void {
  cachedKey = null;
  persistToLocalStorage(null);
  persistToWindow(null);
}
