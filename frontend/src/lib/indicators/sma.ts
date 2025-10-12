// Simple Moving Average (증분 업데이트 버전)
// 사용 목적: 신규 확정 캔들 append 시 O(1)로 SMA 갱신, repair(과거 교체) 시 영향 구간 재계산
// 설계:
//  - window 길이 N
//  - ring buffer 에 최근 N개의 value 와 합계(sum) 유지
//  - append(value): push -> length > N 이면 pop -> sum 조정 -> SMA = sum / 실제 요소 수
//  - repair: 특정 과거 index 의 값이 바뀌면 window 범위에 포함될 경우 sum 보정
//  - delta sync 로 중간 다수 삽입/수정 시엔 full recompute 헬퍼 제공

export interface SMAState {
  period: number;
  buffer: number[];      // 최신이 끝에
  sum: number;
}

export function createSMA(period: number): SMAState {
  if(period <= 0) throw new Error('period must be > 0');
  return { period, buffer: [], sum: 0 };
}

export function smaAppend(state: SMAState, value: number): number {
  state.buffer.push(value);
  state.sum += value;
  if(state.buffer.length > state.period){
    const removed = state.buffer.shift()!;
    state.sum -= removed;
  }
  return state.sum / state.buffer.length;
}

// repair: 가장 단순한 형태 - 최근 N 구간 데이터(=buffer) 중 value 가 변경된 경우 재계산
// 주의: buffer 는 전체 히스토리가 아니라 최근 period 길이만 포함하므로
//       수정된 과거 캔들이 buffer 범위 밖이면 영향 없음
export function smaRepair(state: SMAState, oldValue: number, newValue: number): number | null {
  const idx = state.buffer.indexOf(oldValue);
  if(idx === -1) return null; // 현재 윈도우에 없음
  state.buffer[idx] = newValue;
  state.sum += (newValue - oldValue);
  return state.sum / state.buffer.length;
}

// 많은 repair/delta 로 인한 혼선 시 전체 재계산
export function smaRecompute(state: SMAState, values: number[]): number {
  state.buffer = values.slice(-state.period);
  state.sum = state.buffer.reduce((a,b)=> a+b, 0);
  return state.buffer.length ? state.sum / state.buffer.length : 0;
}
