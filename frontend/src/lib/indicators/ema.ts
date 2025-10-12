// Exponential Moving Average (EMA) 증분 구현
// EMA_t = alpha * price_t + (1-alpha) * EMA_{t-1}
// alpha = 2 / (period + 1)
// append: O(1)
// repair: 영향 구간이 현재 EMA 축적에 파급되므로 단순 보정 어렵다.
//   - 전략: repair 가 윈도우 영향권(최근 K개) 적은 수라면 해당 시점부터 재적용
//   - 헬퍼: emaRecomputeFrom(values, period, startIndex) 전체/부분 재계산

export interface EMAState {
  period: number;
  alpha: number;
  last?: number; // 마지막 EMA 값
  values: number[]; // 원시 값(필요시 최근 period*2 정도 유지)
  maxStore: number; // values 최대 저장 길이
}

export function createEMA(period: number, maxStoreMultiplier = 3): EMAState {
  if(period <= 0) throw new Error('period must be > 0');
  const alpha = 2 / (period + 1);
  return { period, alpha, values: [], maxStore: period * maxStoreMultiplier };
}

export function emaAppend(state: EMAState, value: number): number {
  state.values.push(value);
  if(state.values.length > state.maxStore) state.values.splice(0, state.values.length - state.maxStore);
  if(state.last === undefined){
    state.last = value; // 초기화: 첫 값 그대로
  } else {
    state.last = state.alpha * value + (1 - state.alpha) * state.last;
  }
  return state.last;
}

// repair: 특정 과거 index 의 값이 변경된 상황.
//   state.values 의 어디에 위치하는지 찾고 해당 인덱스부터 다시 EMA 계산.
//   만약 state.values 에 존재하지 않으면 (너무 오래된 값) 무시.
export function emaRepair(state: EMAState, oldValue: number, newValue: number): number | null {
  const idx = state.values.indexOf(oldValue);
  if(idx === -1) return null;
  state.values[idx] = newValue;
  // idx 부터 재계산
  let ema: number | undefined = undefined;
  for(let i=0;i<state.values.length;i++){
    const v = state.values[i];
    if(ema === undefined) ema = v; else if(i >= idx) ema = state.alpha * v + (1 - state.alpha) * ema;
  }
  state.last = ema;
  return state.last!;
}

// 전체 재계산
export function emaRecompute(values: number[], period: number): { last: number; series: number[] } {
  if(period <= 0) throw new Error('period must be > 0');
  const alpha = 2 / (period + 1);
  let ema: number | undefined;
  const out: number[] = [];
  for(const v of values){
    if(ema === undefined) ema = v; else ema = alpha * v + (1 - alpha) * ema;
    out.push(ema);
  }
  return { last: ema ?? 0, series: out };
}
