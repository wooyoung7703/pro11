export type SSEHandlers = {
  onOpen?: () => void;
  onMessage?: (evt: { type: string; data: any; raw: MessageEvent }) => void;
  onError?: (err: any) => void;
  onClose?: () => void;
};

export type SSEOptions = {
  url: string;
  heartbeatTimeoutMs?: number; // consider dead if no message within this window
  maxBackoffMs?: number;
  initialBackoffMs?: number;
};

export function connectSSE(opts: SSEOptions, handlers: SSEHandlers) {
  // Dev mock: if VITE_USE_MOCKS=1, simulate a live SSE stream so UI can update without backend
  if (import.meta.env.DEV && (import.meta as any).env?.VITE_USE_MOCKS === '1') {
    let closed = false;
    let timer: any = null;
    handlers.onOpen && handlers.onOpen();
    let seq = 0;
    const t0 = Date.now();
    function tick() {
      if (closed) return;
      const now = Date.now();
      const up = 0.45 + Math.sin((now - t0)/8000)/8; // 0.45..0.55 oscillation
      const entryPrice = 0.5300;
      const current = 0.5200 + Math.sin((now - t0)/15000) * 0.002; // small wobble
      const inPos = current < entryPrice; // fake in-position when below entry
      const payload = {
        type: seq === 0 ? 'snapshot' : 'delta',
        seq: ++seq,
        server_time: new Date().toISOString(),
        data: {
          mode: inPos ? 'price_gate' : 'buy',
          prob: { up },
          gates: { entry_price: entryPrice, current_price: current, exit_threshold: 0.5 },
          position: { in_position: inPos, avg_entry: entryPrice, scalein_legs: inPos ? 1 : 0 },
        }
      };
      handlers.onMessage && handlers.onMessage({ type: payload.type!, data: payload as any, raw: {} as any });
      timer = setTimeout(tick, 1200);
    }
    tick();
    return {
      isConnected: () => !closed,
      lastMessageAt: () => Date.now(),
      close: () => { closed = true; if (timer) clearTimeout(timer); handlers.onClose && handlers.onClose(); },
    };
  }
  const url = opts.url;
  const hbTimeout = opts.heartbeatTimeoutMs ?? 20000;
  const maxBackoff = opts.maxBackoffMs ?? 15000;
  const initialBackoff = opts.initialBackoffMs ?? 1000;

  let es: EventSource | null = null;
  let closed = false;
  let backoff = initialBackoff;
  let lastMsgAt = Date.now();
  let heartbeatTimer: any = null;

  function clearTimers() {
    if (heartbeatTimer) clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }

  function scheduleHeartbeatWatch() {
    clearTimers();
    heartbeatTimer = setInterval(() => {
      if (Date.now() - lastMsgAt > hbTimeout) {
        // force reconnect
  try { es && es.close(); } catch {}
      }
    }, Math.max(1000, Math.floor(hbTimeout / 2)));
  }

  function open() {
    if (closed) return;
  try { if (es) { es.close(); es = null; } } catch {}
    es = new EventSource(url);
    scheduleHeartbeatWatch();

    es.onopen = () => {
      backoff = initialBackoff; // reset backoff on success
      lastMsgAt = Date.now();
      handlers.onOpen && handlers.onOpen();
    };
    es.onerror = (e) => {
      lastMsgAt = Date.now();
      handlers.onError && handlers.onError(e);
      // Allow EventSource to trigger close; we'll reconnect in onclose
    };
    es.onmessage = (e) => {
      lastMsgAt = Date.now();
      // Try to parse SSE event type via e.event (not standard everywhere). Fallback to data.type
      let type = (e as any).type || (e as any).event || '';
      let data: any = null;
      try { data = e.data ? JSON.parse(e.data) : null; } catch { data = e.data; }
      // Prefer payload-declared type if exists
      if (data && typeof data === 'object' && typeof data.type === 'string') type = data.type;
      handlers.onMessage && handlers.onMessage({ type: type || 'message', data, raw: e });
    };
    // Not standard, but some browsers call onerror repeatedly without closing; guard via timeout
    // We'll use a small helper to detect closed state by listening to readyState
    const closeCheck = setInterval(() => {
      if (!es || es.readyState === (EventSource as any).CLOSED) {
        clearInterval(closeCheck);
        if (!closed) reconnect();
      }
    }, 500);
  }

  function reconnect() {
    if (closed) return;
  try { es && es.close(); } catch {}
    es = null;
    setTimeout(() => {
      backoff = Math.min(maxBackoff, Math.floor(backoff * 2));
      open();
    }, backoff);
  }

  open();

  return {
    isConnected: () => !!es && es.readyState === (EventSource as any).OPEN,
    lastMessageAt: () => lastMsgAt,
    close: () => {
      closed = true;
      clearTimers();
    try { es && es.close(); } catch {}
      es = null;
      handlers.onClose && handlers.onClose();
    },
  };
}
