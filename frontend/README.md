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

  ### Dev Troubleshooting: Inference Playground

  증상: 개발 환경에서 "추론 플레이그라운드"가 401/404/No Data 등으로 동작하지 않을 수 있습니다. 아래 체크리스트를 순서대로 확인하세요.

  1) API Key 설정
  - 백엔드 대부분의 API는 `X-API-Key`가 필요합니다.
  - 방법 A: `.env.development`에 `VITE_DEV_API_KEY=dev-key` 추가 (개발 전용 키)
  - 방법 B: 앱 상단 우측 "API Key" 입력란에 키를 수동 입력 (localStorage에 저장됨)

  2) 백엔드 기동 여부 확인
  - 로컬에서 Uvicorn 백엔드를 8000 포트로 실행하세요.
  - VS Code Tasks에 "Run Backend (uvicorn)"가 포함되어 있습니다.

  3) 프록시 타겟 확인 (포트/호스트)
  - 기본값은 `http://localhost:8000` 입니다.
  - 백엔드 포트가 다르면 개발 서버 실행 전에 환경변수를 지정하세요: `VITE_BACKEND_URL=http://localhost:8010`
  - Vite 개발 서버는 `/api`, `/admin`, `/metrics`, `/health`, `/stream`, `/ws`를 백엔드로 프록시합니다.

  3-1) 원격 개발(다른 호스트)에서 WS 연결 실패 시
  - 증상: 콘솔에 `WebSocket connection to 'ws://<vite-host>:5173/ws/ohlcv?...' failed` 표시
  - 원인: 프록시 기본 타겟이 `localhost:8000`로 설정되어 있어, Vite가 실행 중인 머신에서만 통합니다.
  - 해결 택1)
    - Vite 서버를 시작하기 전에 백엔드 URL을 명시: `VITE_BACKEND_URL=http://<backend-host>:8000`
    - 이렇게 하면 `/ws` 프록시가 해당 백엔드로 웹소켓을 터널링합니다.
  - 해결 택2)
    - 웹소켓을 프록시 없이 직접 연결: `VITE_WS_BASE=ws://<backend-host>:8000`
    - 앱은 `VITE_WS_BASE`가 있으면 이를 우선 사용하고, 없으면 `VITE_BACKEND_URL`을 자동으로 ws(s)로 변환합니다.
  - 환경변수 변경 후에는 반드시 `npm run dev`를 재시작하세요.

  4) 데이터 부족(No Data) 시 즉시 점검
  - 피처/라벨 데이터가 부족하면 Playground가 `no_data`를 표시할 수 있습니다.
  - Admin 패널에서 "Inference Playground Controls" 또는 다음 순서로 빠르게 생성/검증:
    - POST `/admin/inference/quick-validate?count=5&labeler_min_age_seconds=0&labeler_limit=200&lookahead=5&drawdown=0.005&rebound=0.003&target=bottom`
    - 또는 피처 강제 생성: POST `/admin/features/compute-now?symbol=XRPUSDT&interval=1m`

  5) 인증 에러/프록시 오류 가시화
  - 인증 실패(401)가 반복되면 상단 API Key를 확인하세요.
  - 프록시 대상과 백엔드 포트가 일치하는지 확인하세요(`vite.config.ts`의 proxy.target).

  참고: Playground는 axios 인스턴스(`src/lib/http.ts`)를 사용하며, 인터셉터가 `X-API-Key` 헤더를 자동 주입합니다. 키는 `src/lib/apiKey.ts`에서 `.env`/localStorage/window 전역에서 읽습니다.

  ### Quick-validate 뱃지 임계값(환경변수)
  빠른 점검(quick-validate) 결과의 뱃지 색상 임계값을 환경변수로 조정할 수 있습니다. 설정하지 않으면 기본값을 사용합니다.

  - `VITE_QV_ECE_GREEN` (기본 0.08): 프로덕션 ECE가 이 값 이하이면 초록
  - `VITE_QV_ECE_AMBER` (기본 0.12): 프로덕션 ECE가 이 값 이하이면 노랑, 초과 시 빨강
  - `VITE_QV_GAP_REL_GREEN` (기본 0.05): 라이브 vs 프로덕션 ECE 상대 격차가 이 값 이하이면 초록
  - `VITE_QV_GAP_REL_AMBER` (기본 0.10): 상대 격차가 이 값 이하이면 노랑, 초과 시 빨강

  상대 격차(`ece_gap_to_prod_rel`)가 제공되지 않는 경우, 절대 격차(`ece_gap_to_prod`)를 대신 표시합니다. 뱃지에 마우스를 올리면(tooltip) 현재 기준과 임계값을 확인할 수 있습니다.
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
