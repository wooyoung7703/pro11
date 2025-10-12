import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { connectSSE } from '@/lib/sse';

type Mode = 'buy' | 'price_gate' | 'exit' | 'idle';
interface SignalsPayload {
  mode?: Mode;
  prob?: { up?: number };
  gates?: { entry_price?: number; anchor_price?: number; exit_threshold?: number; exit_remain?: number };
  position?: { in_position?: boolean; avg_entry?: number; scalein_legs?: number };
  // optional server_time for age display
  server_time?: number | string;
}

interface Envelope {
  type?: string; // snapshot|delta|heartbeat|error
  seq?: number;
  epoch?: string;
  server_time?: number | string;
  channel?: string;
  data?: SignalsPayload;
}

export const useSignalsStore = defineStore('signals', () => {
  const connected = ref<'idle'|'online'|'lagging'|'offline'>('idle');
  const lastServerTs = ref<number | null>(null);
  const lastSeq = ref<number>(0);
  const lastEpoch = ref<string | null>(null);
  const state = ref<SignalsPayload>({});
  let sse: { close: () => void } | null = null;

  function parseServerTime(v: any): number | null {
    if (typeof v === 'number') return Math.floor(v * 1000);
    if (typeof v === 'string') {
      const t = Date.parse(v);
      if (!isNaN(t)) return t;
    }
    return null;
  }

  function merge(data?: SignalsPayload) {
    if (!data) return;
    state.value = { ...state.value, ...data,
      prob: { ...(state.value.prob||{}), ...(data.prob||{}) },
      gates: { ...(state.value.gates||{}), ...(data.gates||{}) },
      position: { ...(state.value.position||{}), ...(data.position||{}) },
    };
    if (data.server_time != null) lastServerTs.value = parseServerTime(data.server_time);
  }

  function handleEnvelope(env: Envelope) {
    if (!env) return;
    const epoch = env.epoch || lastEpoch.value || 'default';
    const seq = typeof env.seq === 'number' ? env.seq : undefined;
    const type = env.type || 'message';
    const serverTs = parseServerTime(env.server_time);
    if (serverTs) lastServerTs.value = serverTs;
    // epoch change => accept and reset lastSeq
    if (epoch !== lastEpoch.value) {
      lastEpoch.value = epoch;
      lastSeq.value = 0;
    }
    if (type === 'heartbeat') return;
    if (type === 'error') return; // optionally surface to a toast store
    if (type === 'snapshot') {
      lastSeq.value = seq || lastSeq.value;
      state.value = {};
      merge(env.data);
      return;
    }
    if (type === 'delta') {
      if (seq != null) {
        if (seq <= lastSeq.value) return; // drop stale/dup
        // If we detect a gap, we could trigger a resync fetch here later
        lastSeq.value = seq;
      }
      merge(env.data);
      return;
    }
    // Fallback: treat as delta
    merge(env.data);
  }

  function start() {
    stop();
    const base = (import.meta as any).env?.VITE_BACKEND_URL || '';
    const url = `${base}/stream/signals`.replace(/\/$/, '/stream/signals');
    sse = connectSSE({ url, heartbeatTimeoutMs: 20000 }, {
      onOpen: () => { connected.value = 'online'; },
      onMessage: ({ data }) => {
        handleEnvelope(data as any);
        const age = lastServerTs.value ? (Date.now() - lastServerTs.value) : 0;
        connected.value = age > 20000 ? 'lagging' : 'online';
      },
      onError: () => { connected.value = 'lagging'; },
      onClose: () => { connected.value = 'offline'; },
    });
  }

  function stop() { if (sse) { try { sse.close(); } catch(_){} sse = null; } connected.value = 'offline'; }

  const mode = computed<Mode>(() => (state.value.mode as Mode) || 'idle');
  const upProb = computed<number | null>(() => typeof state.value.prob?.up === 'number' ? state.value.prob.up! : null);
  const nextEntryPrice = computed<number | null>(() => state.value.gates?.entry_price ?? null);
  const exitThreshold = computed<number | null>(() => state.value.gates?.exit_threshold ?? null);
  const inPosition = computed<boolean>(() => !!state.value.position?.in_position);

  return { connected, lastServerTs, mode, upProb, nextEntryPrice, exitThreshold, inPosition, state, start, stop };
});
