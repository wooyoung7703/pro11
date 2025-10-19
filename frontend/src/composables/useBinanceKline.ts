import { ref, watch, type Ref } from 'vue';

export interface BinanceKlineUpdate {
  symbol: string;
  openTime: number;
  closeTime: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  tradeCount: number;
  isFinal: boolean;
}

const BINANCE_WS_BASE = 'wss://stream.binance.com:9443/ws';

export function useBinanceKline(symbol: Ref<string>, interval: string = '1m') {
  const latest = ref<BinanceKlineUpdate | null>(null);
  const connected = ref(false);
  let socket: WebSocket | null = null;
  let closing = false;
  let seq = 0; // guard against stale handlers
  let connectTimer: any = null;

  function cleanup(): Promise<void> {
    connected.value = false;
    if (!socket) return Promise.resolve();
    try {
      // prevent duplicate close sequences
      if (closing) return Promise.resolve();
      closing = true;
      // detach handlers first to minimize event noise
      socket.onopen = null;
      socket.onclose = null;
      socket.onerror = null;
      socket.onmessage = null;
      if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
        try { socket.close(1000, 'normal closure'); } catch {}
      }
      const s = socket;
      socket = null;
      return new Promise<void>((resolve) => {
        const to = setTimeout(() => { closing = false; resolve(); }, 400);
        try {
          s.addEventListener('close', () => { clearTimeout(to); closing = false; resolve(); }, { once: true });
        } catch {
          clearTimeout(to); closing = false; resolve();
        }
      });
    } finally {
      // fallback safety
      if (!socket) { /* noop */ }
    }
  }

  function buildUrl(sym: string){
    return `${BINANCE_WS_BASE}/${sym.toLowerCase()}@kline_${interval}`;
  }

  function parseMessage(raw: MessageEvent, mySeq: number){
    try {
      const data = JSON.parse(raw.data);
      if (mySeq !== seq) return; // ignore stale messages from previous socket
      if(!data || data.e !== 'kline' || !data.k) return;
      const k = data.k;
      latest.value = {
        symbol: data.s || symbol.value,
        openTime: Number(k.t),
        closeTime: Number(k.T),
        open: Number(k.o),
        high: Number(k.h),
        low: Number(k.l),
        close: Number(k.c),
        volume: Number(k.v),
        tradeCount: Number(k.n ?? 0),
        isFinal: Boolean(k.x),
      };
    } catch(_err){
      /* swallow parse errors */
    }
  }

  async function connect(){
    const sym = symbol.value;
    if(!sym){
      await cleanup();
      return;
    }
    if (connectTimer) { clearTimeout(connectTimer); connectTimer = null; }
    await cleanup();
    try {
      const mySeq = ++seq;
      socket = new WebSocket(buildUrl(sym));
      socket.onopen = () => {
        connected.value = true;
      };
      socket.onclose = () => {
        if (mySeq !== seq) return; // already superseded
        connected.value = false;
      };
      socket.onerror = () => {
        if (mySeq !== seq) return;
        connected.value = false;
      };
      socket.onmessage = (ev) => parseMessage(ev, mySeq);
    } catch(_err){
      await cleanup();
    }
  }

  function disconnect(){
    if (connectTimer) { clearTimeout(connectTimer); connectTimer = null; }
    // best-effort close; ignore unhandled rejections
    void cleanup();
  }

  watch(symbol, () => {
    if (connectTimer) clearTimeout(connectTimer);
    // debounce reconnects to avoid close/open races
    connectTimer = setTimeout(() => { connect(); }, 150);
  }, { immediate: true });

  return {
    latest,
    connected,
    connect,
    disconnect,
  };
}
