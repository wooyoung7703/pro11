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

  function cleanup(){
    connected.value = false;
    if(socket){
      socket.onopen = null;
      socket.onclose = null;
      socket.onerror = null;
      socket.onmessage = null;
      socket.close();
      socket = null;
    }
  }

  function buildUrl(sym: string){
    return `${BINANCE_WS_BASE}/${sym.toLowerCase()}@kline_${interval}`;
  }

  function parseMessage(raw: MessageEvent){
    try {
      const data = JSON.parse(raw.data);
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

  function connect(){
    const sym = symbol.value;
    if(!sym){
      cleanup();
      return;
    }
    cleanup();
    try {
      socket = new WebSocket(buildUrl(sym));
      socket.onopen = () => {
        connected.value = true;
      };
      socket.onclose = () => {
        cleanup();
      };
      socket.onerror = () => {
        cleanup();
      };
      socket.onmessage = parseMessage;
    } catch(_err){
      cleanup();
    }
  }

  function disconnect(){
    cleanup();
  }

  watch(symbol, () => {
    connect();
  }, { immediate: true });

  return {
    latest,
    connected,
    connect,
    disconnect,
  };
}
