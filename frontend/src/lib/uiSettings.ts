import http from './http';

// UI prefs stored in DB under key prefix 'ui.'
// Fallback to localStorage when unauthenticated or backend unavailable.

function lsGet(key: string): any {
  try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) : null; } catch { return null; }
}
function lsSet(key: string, value: any): void {
  try {
    if (value === undefined || value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {}
}

export async function getUiPref<T = any>(key: string, defaultValue?: T): Promise<T | undefined> {
  const dbKey = `ui.${key}`;
  try {
    const { data } = await http.get(`/admin/settings/${encodeURIComponent(dbKey)}`);
    if (data?.item && 'value' in data.item) return data.item.value as T;
  } catch {
    // fall through to localStorage
  }
  const ls = lsGet(dbKey);
  if (ls !== null && ls !== undefined) return ls as T;
  return defaultValue;
}

export async function setUiPref<T = any>(key: string, value: T): Promise<boolean> {
  const dbKey = `ui.${key}`;
  try {
    const { data } = await http.put(`/admin/settings/${encodeURIComponent(dbKey)}`, { value, apply: false });
    if (data?.status === 'ok') {
      // mirror to localStorage for quick subsequent loads (and offline)
      lsSet(dbKey, value);
      return true;
    }
  } catch {
    // ignore
  }
  // fallback persist locally
  lsSet(dbKey, value);
  return false;
}
