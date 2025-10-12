# Frontend (Vite + Vue 3)

Note: This README change is used to validate CI triggers for the develop branch.

## Quick start
- Install: npm i
- Dev: npm run dev
- Build: npm run build
- Preview: npm run preview
- Test (once): npm run test
- Test (watch): npm run test:watch

## Realtime SSE tuning (env)
- Copy variables from `.env.example` to `.env` or `.env.local` and adjust as needed.
- Available knobs:
  - `VITE_SIG_FRESH_MS` (default 20000)
  - `VITE_RISK_FRESH_MS` (default 15000)
  - `VITE_SEVERE_LAG_MULT` (default 3)

## Path alias
- `@` → `src` is configured in both `vite.config.ts` and `vitest.config.js`.
- TypeScript `paths` also maps `@/*` to `src/*` in `tsconfig.json`.

## Tests
- Unit tests use Vitest + Vue Test Utils (jsdom environment).
- Realtime logic covered in `tests/realtime.spec.ts`.
- SSE badge visuals/a11y covered in `tests/sseStatusBadge.spec.ts`.

## OHLCV + DCA Simulator
- Menu: 시세 ohlcv
- How to use
  - 상단에서 Interval/Limit 조정 후 Refresh 가능. WS/Gap/Year Backfill 컨트롤 제공.
  - 우측의 "DCA Simulator"에서 파라미터 입력 후 Simulate 클릭:
    - base notional: 초기 매수 금액(quote)
    - add ratio: 추가매수 비율(이전 레그 노셔널 × 비율)
    - max legs: 물타기 최대 레그(초기 포함)
    - cooldown(s): 추가매수 간 최소 시간(초)
    - min move %: 최근 진입가 대비 최소 하락폭(예: 0.005 = 0.5%)
    - fee rate: 수수료율(예: 0.001 = 0.1%)
    - take profit %: 목표 수익률(예: 0.01 = 1%)
  - 거래 내역은 우측 표로 확인, 차트에는 매수(초록)/매도(빨강) 마커 오버레이.
  - Lightweight 차트 토글을 통해 lightweight-charts 기반 차트를 사용할 수 있으며, 동일하게 마커 표시를 지원합니다.
