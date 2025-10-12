// Lightweight signature-based memo cache for expensive news pipeline stages.
// Each stage provides (key, compute) and depends on an input signature string.
// Eviction: simple LRU with max entries.

interface Entry<T> { value: T; sig: string; ts: number; }

export class MemoCache<T> {
  private store: Map<string, Entry<T>> = new Map();
  constructor(private max = 100) {}
  get(key: string, sig: string): T | undefined {
    const e = this.store.get(key);
    if (e && e.sig === sig) {
      e.ts = Date.now();
      return e.value;
    }
    return undefined;
  }
  set(key: string, sig: string, value: T) {
    this.store.set(key, { value, sig, ts: Date.now() });
    if (this.store.size > this.max) this.evict();
  }
  private evict() {
    // remove oldest 10% entries
    const arr = Array.from(this.store.entries());
    arr.sort((a,b)=> a[1].ts - b[1].ts);
    const remove = Math.ceil(arr.length * 0.1);
    for (let i=0;i<remove;i++) this.store.delete(arr[i][0]);
  }
  clear() { this.store.clear(); }
}

// Utility helpers to create signatures safely
export function sigParts(parts: any[]): string {
  return parts.map(p => typeof p==='object' ? JSON.stringify(p) : String(p)).join('|');
}
