import { ref, computed, onBeforeUnmount, watch } from 'vue';
import { useOhlcvStore, type Candle } from '@/stores/ohlcv';

// ---- WebSocket 이벤트 타입 (백엔드 프로토콜 스켈레톤 반영) ----
// 실제 필드(예: symbol, interval)는 백엔드 구현에 따라 추가될 수 있으므로 optional 로 둔다.
interface SnapshotEvent { type: 'snapshot'; symbol?: string; interval?: string; candles: Candle[]; }
interface AppendEvent { type: 'append'; symbol?: string; interval?: string; candle: Candle; }
interface RepairEvent { type: 'repair'; symbol?: string; interval?: string; candles: Candle[]; }
interface PartialUpdateEvent { type: 'partial_update'; symbol?: string; interval?: string; candle: Candle; }
interface PartialCloseEvent { type: 'partial_close'; symbol?: string; interval?: string; candle: Candle; }
interface GapDetectedEvent { type: 'gap_detected'; symbol?: string; interval?: string; detail?: any; }
interface GapRepairedEvent { type: 'gap_repaired'; symbol?: string; interval?: string; detail?: any; }

export type OhlcvWsEvent =
  | SnapshotEvent
  | AppendEvent
  | RepairEvent
  | PartialUpdateEvent
  | PartialCloseEvent
  | GapDetectedEvent
  | GapRepairedEvent
  | { type: string; [k: string]: any }; // Fallback (미정 이벤트 대비)

interface UseOhlcvWsOptions {
  // 필요 시 향후 다중 심볼 지원을 위해 symbol 전달 구조화 가능
  includeOpen?: boolean; // snapshot 시 open 포함 여부 옵션 (백엔드 정책과 맞춤)
  autoConnect?: boolean; // 생성 시 즉시 연결
  log?: boolean;         // 콘솔 로깅 on/off
}

// 재연결 백오프 파라미터
const BACKOFF_BASE = 500;  // ms
const BACKOFF_MAX = 10_000; // ms

export function useOhlcvWs(opts: UseOhlcvWsOptions = {}) {
  const store = useOhlcvStore();

  const ws = ref<WebSocket|null>(null);
  const connected = ref(false);
  const connecting = ref(false);
  const lastEventAt = ref<number|null>(null);
  const lastEventType = ref<string|null>(null);
  const lastError = ref<string|null>(null);
  const reconnectAttempts = ref(0);
  const manualClosed = ref(false);
  const partialCandle = ref<Candle|null>(null); // 진행 중 partial (close 되기 전)
  const latencyMs = ref<number|null>(null); // ping-pong 확장 대비 (현재 미사용)

  // ---- 인터셉터 (append / repair) ----
  // 반환값이 true 이면 기본 처리 소비(중단)
  const appendInterceptors: Array<(ev: AppendEvent)=> boolean|void> = [];
  const repairInterceptors: Array<(ev: RepairEvent)=> boolean|void> = [];

  const symbol = computed(()=> store.symbol); // 현재 스토어 심볼 사용 (단일 고정)
  const interval = computed(()=> store.interval); // 사용자가 입력한 interval

  function log(...args:any[]) { if(opts.log !== false) console.log('[ohlcv-ws]', ...args); }

  // WS URL 구성 (환경변수 → 기본 window.location):
  // Vite: VITE_WS_BASE=ws://localhost:8000 형태 기대. 없으면 현 origin 기반.
  function buildUrl() {
    const base = (import.meta as any).env?.VITE_WS_BASE || window.location.origin.replace(/^http/, 'ws');
    const params = new URLSearchParams();
    if(symbol.value) params.set('symbol', symbol.value);
    if(interval.value) params.set('interval', interval.value);
    if(opts.includeOpen) params.set('include_open', 'true');
    return `${base.replace(/\/$/, '')}/ws/ohlcv?${params.toString()}`;
  }

  function connect() {
    if(!interval.value) { log('interval 미설정 - 연결 보류'); return; }
    if(connected.value || connecting.value) return;
    manualClosed.value = false;
    const url = buildUrl();
    log('connecting', url);
    connecting.value = true;
    try {
      const socket = new WebSocket(url);
      ws.value = socket;
      socket.onopen = () => {
        connecting.value = false;
        connected.value = true;
        reconnectAttempts.value = 0;
        lastError.value = null;
        log('connected');
      };
      socket.onerror = (ev) => {
        lastError.value = 'ws_error';
        log('error', ev);
      };
      socket.onclose = (ev) => {
        log('closed', ev.code, ev.reason);
        connected.value = false;
        connecting.value = false;
        ws.value = null;
        if(!manualClosed.value) scheduleReconnect();
      };
      socket.onmessage = (msg) => {
        lastEventAt.value = Date.now();
        try {
          const data: OhlcvWsEvent = JSON.parse(msg.data);
          lastEventType.value = data.type;
          handleEvent(data);
        } catch(e:any) {
          log('message parse error', e, msg.data);
        }
      };
    } catch(e:any) {
      connecting.value = false;
      lastError.value = e.message || 'connect_fail';
      log('connect exception', e);
      scheduleReconnect();
    }
  }

  function scheduleReconnect() {
    if(manualClosed.value) return;
    const attempt = reconnectAttempts.value + 1;
    reconnectAttempts.value = attempt;
    const delay = Math.min(BACKOFF_MAX, BACKOFF_BASE * 2 ** (attempt - 1));
    log(`reconnect in ${delay}ms (attempt ${attempt})`);
    setTimeout(()=> { if(!manualClosed.value) connect(); }, delay);
  }

  function disconnect() {
    manualClosed.value = true;
    if(ws.value && (connected.value || connecting.value)) {
      ws.value.close();
    }
  }

  // ---- 이벤트 처리 ----
  function handleEvent(ev: OhlcvWsEvent) {
    switch(ev.type) {
      case 'snapshot': return onSnapshot(ev as SnapshotEvent);
      case 'append': return onAppend(ev as AppendEvent);
      case 'repair': return onRepair(ev as RepairEvent);
      case 'partial_update': return onPartialUpdate(ev as PartialUpdateEvent);
      case 'partial_close': return onPartialClose(ev as PartialCloseEvent);
      case 'gap_detected': return onGapDetected(ev as GapDetectedEvent);
      case 'gap_repaired': return onGapRepaired(ev as GapRepairedEvent);
      default:
        log('unknown event', ev);
    }
  }

  function onSnapshot(ev: SnapshotEvent) {
    log('snapshot', ev.candles.length);
    store.candles = [...ev.candles];
    partialCandle.value = null; // 초기화
  }

  function onAppend(ev: AppendEvent) {
    // 인터셉터 실행 (소비되면 기본 처리 중단)
    for(const fn of appendInterceptors){
      try { if(fn(ev)) return; } catch(e){ log('append interceptor error', e); }
    }
    const c = ev.candle;
    const arr = store.candles;
    const last = arr[arr.length-1];
    if(!last || c.open_time > last.open_time) arr.push(c); else if(c.open_time === last.open_time) arr[arr.length-1] = c; // 동일 구간 최신 덮어쓰기
  }

  function onRepair(ev: RepairEvent) {
    for(const fn of repairInterceptors){
      try { if(fn(ev)) return; } catch(e){ log('repair interceptor error', e); }
    }
    // 다수 캔들 upsert
    const arr = store.candles;
    for(const rc of ev.candles){
      const idx = arr.findIndex(x=> x.open_time === rc.open_time);
      if(idx >= 0) arr[idx] = rc; else arr.push(rc); // 존재 없으면 삽입 (이후 정렬)
    }
    arr.sort((a,b)=> a.open_time - b.open_time);
  }

  function onPartialUpdate(ev: PartialUpdateEvent) {
    // 진행 중 partial 캔들을 머지 (close 이전)
    const c = ev.candle;
    if(!partialCandle.value || partialCandle.value.open_time !== c.open_time) {
      partialCandle.value = { ...c, is_closed: false };
    } else {
      // 고/저 재평가 및 종가/볼륨 등 최신화
      const pc = partialCandle.value;
      pc.high = Math.max(pc.high, c.high);
      pc.low = Math.min(pc.low, c.low);
      pc.close = c.close;
      if(c.volume!=null) pc.volume = c.volume;
      if(c.trade_count!=null) pc.trade_count = c.trade_count;
    }
    applyOrPreviewPartial();
  }

  function onPartialClose(ev: PartialCloseEvent) {
    const c = ev.candle; // 확정된 최종 캔들
    partialCandle.value = null;
    // append 로직과 유사 (확정)
    const arr = store.candles;
    const last = arr[arr.length-1];
    if(!last || c.open_time > last.open_time) arr.push({ ...c, is_closed: true });
    else if(c.open_time === last.open_time) arr[arr.length-1] = { ...c, is_closed: true }; // 교체
  }

  function onGapDetected(ev: GapDetectedEvent) { log('gap_detected', ev.detail); }
  function onGapRepaired(ev: GapRepairedEvent) { log('gap_repaired', ev.detail); }

  function applyOrPreviewPartial() {
    const pc = partialCandle.value; if(!pc) return;
    const arr = store.candles;
    const last = arr[arr.length-1];
    if(!last || pc.open_time > last.open_time) {
      // 미리보기(닫히지 않은 캔들) - is_closed false 유지
      arr.push(pc);
    } else if(pc.open_time === last.open_time) {
      arr[arr.length-1] = pc; // 진행 중 업데이트
    }
  }

  // 사용자가 interval 변경 시 다시 snapshot 을 새로 받기 위해 재연결
  watch(interval, (nv, ov) => {
    if(nv && nv !== ov) {
      log('interval changed -> reconnect');
      reconnect();
    }
  });

  function reconnect() { disconnect(); reconnectAttempts.value = 0; setTimeout(connect, 20); }

  onBeforeUnmount(()=>{ disconnect(); });

  if(opts.autoConnect) connect();

  // ---- 인터셉터 등록/해제 API ----
  function registerAppendInterceptor(fn: (ev: AppendEvent)=> boolean|void){
    appendInterceptors.push(fn);
    return () => { const i = appendInterceptors.indexOf(fn); if(i>=0) appendInterceptors.splice(i,1); };
  }
  function registerRepairInterceptor(fn: (ev: RepairEvent)=> boolean|void){
    repairInterceptors.push(fn);
    return () => { const i = repairInterceptors.indexOf(fn); if(i>=0) repairInterceptors.splice(i,1); };
  }

  return {
    // state
    connected, connecting, lastEventAt, lastEventType, lastError, latencyMs,
    reconnectAttempts, partialCandle: computed(()=> partialCandle.value),
    // actions
    connect, disconnect, reconnect,
    registerAppendInterceptor, registerRepairInterceptor,
  };
}
