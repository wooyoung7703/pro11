# Indicators Module (P1)

초기 목표:
- OHLCV 확정 캔들 append / repair 흐름에 증분 적용 가능한 핵심 지표 SMA, EMA 제공
- Partial 캔들은 is_closed=false 상태에서 제외 (확정된 close 이후 append 시 반영)

구성 파일:
- `sma.ts`: 단순 이동 평균 (SMA)
  - `createSMA(period)` → 상태 객체 생성
  - `smaAppend(state, value)` → 신규 확정 값 반영 후 현재 SMA 반환
  - `smaRepair(state, oldValue, newValue)` → 최근 window 내 값 교체 시 sum 보정
  - `smaRecompute(state, values)` → 다수 변경/동기화 후 전체 재계산
- `ema.ts`: 지수 이동 평균 (EMA)
  - `createEMA(period)` → 상태 객체
  - `emaAppend(state, value)` → 값 추가 및 EMA 갱신
  - `emaRepair(state, oldValue, newValue)` → 값 교체 후 해당 지점부터 누적 재계산(존재하는 경우)
  - `emaRecompute(values, period)` → 전체 재계산 (series 포함)

향후(P2) 예정:
- VWAP, RSI, ATR
- repair 영향을 더 정밀히 추적하기 위한 변경 로그 기반 부분 재계산 최적화
- partial preview 상태에서 임시 지표 예측(가중) 기능
