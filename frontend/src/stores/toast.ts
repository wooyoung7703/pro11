import { defineStore } from 'pinia';

export interface ToastItem {
  id: string;
  type: 'info' | 'success' | 'warning' | 'error';
  message: string;
  detail?: string;
  ts: number;
  ttl: number; // ms
}

export const useToastStore = defineStore('toast', {
  state: () => ({
    items: [] as ToastItem[],
    max: 5,
    defaultTtl: 6000,
  }),
  actions: {
    push(partial: Omit<ToastItem, 'id' | 'ts' | 'ttl'> & { id?: string; ttl?: number }) {
      const id = partial.id || crypto.randomUUID();
      const now = Date.now();
      const ttl = partial.ttl ?? this.defaultTtl;
      const item: ToastItem = { id, ts: now, ttl, ...partial } as ToastItem;
      this.items.unshift(item);
      // Trim
      if (this.items.length > this.max) {
        this.items = this.items.slice(0, this.max);
      }
      // Schedule auto remove
      setTimeout(() => this.remove(id), item.ttl);
      return id;
    },
    remove(id: string) {
      this.items = this.items.filter(t => t.id !== id);
    },
  info(message: string, detail?: string, ttl?: number) { this.push({ type: 'info', message, detail, ttl }); },
  success(message: string, detail?: string, ttl?: number) { this.push({ type: 'success', message, detail, ttl }); },
  warn(message: string, detail?: string, ttl?: number) { this.push({ type: 'warning', message, detail, ttl }); },
  error(message: string, detail?: string, ttl?: number) { this.push({ type: 'error', message, detail, ttl }); },
    clear() { this.items = []; },
  }
});
