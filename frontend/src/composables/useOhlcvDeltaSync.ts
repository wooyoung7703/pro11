import { ref } from 'vue';
import http from '@/lib/http';
import { useOhlcvStore, type Candle } from '@/stores/ohlcv';
import { useOhlcvWs } from './useOhlcvWs';

/**
 * 재연결(or 초기) 시 delta 로 누락된 확정 캔들과 repairs를 채우고, 그 사이 WS 로 들어온 이벤트는 버퍼링 후 적용.
 * 시나리오:
 * 1) WS 연결 직후 snapshot 수신 (candles baseline)
 * 2) 네트워크 끊김 → 재연결
 * 3) 재연결 과정에서 일부 append/repair 이벤트가 delta 이전 도착할 수 있음 → 버퍼에 저장
 * 4) delta 완료 후 버퍼 flush (타임스탬프 순 정렬) → 최종 상태 일관
 */
export function useOhlcvDeltaSync() {
  const store = useOhlcvStore();
  // WS composable (autoConnect=false 로 생성, 외부에서 connect 제어)
  const wsCtl = useOhlcvWs({ autoConnect: false, includeOpen: true, log: true });

  const syncing = ref(false);
  const lastDeltaSince = ref<number|null>(null);
  const wsBuffer: any[] = []; // {type, ...}
  const appliedRepairs = ref(0);
  const appliedAppends = ref(0);
  const appliedPartialDrops = ref(0);

  // WS 이벤트를 가로채기 위해 원 handle 을 wrapping 할 수도 있으나 현재 composable 구조 상
  // 간단히 store 변동 후 delta 이전 시점이면 버퍼에 push 하는 hook 형태로 교체 필요.
  // P1: 간소화 - useOhlcvWs 자체를 수정하지 않고 delta 이전에 온 append/repair 를 캔슬하고 버퍼 저장 위해
  // monkey patch 스타일로 store 배열 직접 조작 이전에 차단.

  // WS composable 인터셉터 등록 (append / repair). delta 동기화 중이면 버퍼에 저장 후 소비.
  const disposeAppend = wsCtl.registerAppendInterceptor(ev => {
    if(!syncing.value) return false;
    wsBuffer.push({ type: 'append', candles: [ev.candle] });
    return true; // 소비
  });
  const disposeRepair = wsCtl.registerRepairInterceptor(ev => {
    if(!syncing.value) return false;
    wsBuffer.push({ type: 'repair', candles: ev.candles });
    return true;
  });

  // store 조작 유틸
  function applyAppend(candles: Candle[]) {
    const arr = store.candles;
    for(const c of candles){
      const last = arr[arr.length-1];
      if(!last || c.open_time > last.open_time) arr.push(c);
      else if(c.open_time === last.open_time) arr[arr.length-1] = c; // replace
    }
  }
  function applyRepair(candles: Candle[]) {
    const arr = store.candles;
    for(const rc of candles){
      const idx = arr.findIndex(x=> x.open_time === rc.open_time);
      if(idx>=0) arr[idx] = rc; else arr.push(rc);
    }
    arr.sort((a,b)=> a.open_time - b.open_time);
  }

  async function runDelta() {
    if(!store.candles.length) return; // snapshot baseline 없는 경우 skip
    const lastClosed = (()=>{
      for(let i=store.candles.length-1;i>=0;i--){
        if(store.candles[i].is_closed!==false) return store.candles[i].open_time;
      }
      return null;
    })();
    if(lastClosed==null) return;
    syncing.value = true;
    lastDeltaSince.value = lastClosed;
    try {
      const r = await http.get('/api/ohlcv/delta', { params: { since: lastClosed, limit: 1000 }});
      const d = r.data || {};
      const newCandles: Candle[] = d.candles||[];
      const repairs: any[] = d.repairs||[];
      if(newCandles.length){
        applyAppend(newCandles);
        appliedAppends.value += newCandles.length;
      }
      if(repairs.length){
        const list: Candle[] = repairs.map(rp=> rp.candle).filter(Boolean);
        applyRepair(list);
        appliedRepairs.value += list.length;
      }
      flushBuffer();
    } finally {
      syncing.value = false;
    }
  }

  function flushBuffer(){
    if(!wsBuffer.length) return;
    // append 먼저 시간순, repair 이후: repair 는 과거 조정 가능하므로 순서는 open_time ASC 기준
    const appends: Candle[] = [];
    const repairs: Candle[] = [];
    for(const ev of wsBuffer){
      if(ev.type==='append') appends.push(...ev.candles);
      else if(ev.type==='repair') repairs.push(...ev.candles);
    }
    wsBuffer.length = 0;
    if(appends.length){
      appends.sort((a,b)=> a.open_time - b.open_time);
      applyAppend(appends);
      appliedAppends.value += appends.length;
    }
    if(repairs.length){
      repairs.sort((a,b)=> a.open_time - b.open_time);
      applyRepair(repairs);
      appliedRepairs.value += repairs.length;
    }
  }

  return {
    // state
    syncing, lastDeltaSince, appliedAppends, appliedRepairs, appliedPartialDrops,
    // actions
    runDelta, flushBuffer,
    wsCtl,
  };
}
