# 캘리브레이션 & 라벨링 동작 가이드

본 문서는 서비스 구동 이후 "추론 로그 → 라벨링(실현 레이블) → 라이브 캘리브레이션"이 어떻게 연결되는지와, 로컬에서 빠르게 검증하는 절차를 정리합니다.

## 전체 흐름

1. 서비스 구동
   - DB 풀 생성, 스키마 보장, 베이스라인 모델(bottom_predictor) 시딩
   - 피처 스케줄러/인제션, 자동 추론 루프, 추론 로그 큐, 자동 라벨러, 캘리브레이션 모니터 구동
2. 추론 로그 적재
   - /api/inference/predict 또는 자동 루프가 decision을 생성
   - inference log가 큐(배치)로 DB에 적재됨(extra.target='bottom')
3. 자동 라벨러
   - AUTO_LABELER_MIN_AGE_SECONDS 경과한 미라벨 후보를 조회
   - OHLCV를 이용해 bottom-event 라벨(lookahead/drawdown/rebound) 계산 후 realized 업데이트
4. 라이브 캘리브레이션
   - 주어진 window_seconds 내 realized 로그로 Brier/ECE/MCE 및 신뢰도 빈 계산
   - Prod ECE 대비 delta로 드리프트 판단 및 streak 관리

## 주요 엔드포인트

- 추론 호출: GET /api/inference/predict?threshold=0.5
- 큐 플러시: POST /admin/inference/queue/flush
- 수동 라벨러 1회 실행(오버라이드 지원):
  - POST /api/inference/labeler/run?min_age_seconds=0&limit=200&lookahead=5&drawdown=0.005&rebound=0.003
- 라벨러 상태: GET /admin/inference/labeler/status
- 라이브 캘리브레이션: GET /api/inference/calibration/live?window_seconds=1800&bins=10&target=bottom
   - (신규) eager labeling: 윈도우 내 실현 레이블이 없으면 기본적으로 제한된 동기 라벨링을 시도하여 `no_data`를 줄입니다.
      - 쿼리 파라미터:
         - `eager_label` (기본: true) — false로 주면 동기 시도를 비활성화합니다.
         - `eager_limit` — 이번 호출에서 동기 라벨링 시 처리할 최대 후보 수(안전 상한 적용).
         - `eager_min_age_seconds` — 동기 라벨링 시 후보로 인정할 최소 경과 시간(초).
      - `no_data` 응답 시 `attempted_eager_label` 플래그가 포함되어 실제 동기 시도 여부를 확인할 수 있습니다.
- 모니터 상태: GET /api/monitor/calibration/status
- 피처 백필(필요 시): POST /admin/features/backfill?target=600

## 빠른 로컬 검증 레시피

옵션 A) 원샷(Windows 친화, 루프/파이프 없이)
- POST /admin/inference/quick-validate?count=5&labeler_min_age_seconds=0&labeler_limit=200&lookahead=5&drawdown=0.005&rebound=0.003&target=bottom
   - 동작: 예측 배치 → 큐 플러시 → (선택) 대기 → 라벨러 1회 → 라이브 캘리브레이션 요약 반환
   - symbol, interval을 지정하면 동일 스코프로 라벨러에 only_symbol/only_interval 이 전달되어 즉시 라벨 생성 확률이 높아집니다.
   - (신규) 선택 옵션:
     - 모델 선택: prefer_latest, version (배치 예측에 전달)
     - 단계 건너뛰기: skip_predict, skip_label (문제 구간만 국소 검증 가능)
     - 타이밍 제어: pre_label_sleep_ms, calibration_retries, retry_delay_ms
      - (신규) 응답 확장: timing.predict_sec/label_sec, calibration_before/calibration_delta, diagnose_before/after + delta
      - (신규) 요약 코드: summary.result_code, summary.progress
       - result_code: 'labeled' | 'calibrated' | 'no_data' | 'ok' 중 하나로 빠른 상태 판단을 돕습니다.
       - progress: { diagnose_scoped_reduced, diagnose_total_reduced, labeled_positive, calibration_improved, samples_increase }
          - diagnose_*_reduced: 진단(미라벨 후보) 개수가 줄었는지
          - labeled_positive: 이번 호출에서 라벨이 실제로 생성되었는지
          - calibration_improved: ECE가 개선(감소)되었는지
          - samples_increase: 캘리브레이션 샘플 수가 증가했는지
      - (신규) 헬스 크리테리아: summary.health + 파라미터
         - 요청 파라미터
            - ece_target: 허용 목표 ECE 상한 (예: 0.10). live ECE > ece_target 이면 실패 사유에 ece_above_target 추가
            - min_samples: 최소 샘플 수 (예: 10). live samples < min_samples 이면 insufficient_samples 추가
            - max_mce: 허용 MCE 상한 (예: 0.20). live MCE > max_mce 이면 mce_above_max 추가
         - 응답 필드
            - summary.health: { pass: boolean, reasons: string[], ece_target, min_samples, max_mce }
            - reasons 값 예시: ["ece_above_target", "insufficient_samples", "mce_above_max"] 중 해당사항만 포함
         - 활용 팁: CI 체크/대시보드에서 summary.health.pass 를 단일 배지로 사용하고, 실패 시 reasons 로 원인 표기
    - (신규) compact 모드: `compact=true` 로 호출하면 대시보드/폴링 친화 축약 응답을 반환합니다.
             - summary: { result_code, progress, scope, now_ts, batch_attempted, batch_success, flushed, labeled, queue, health, run_id, total_sec, request_id, ece_gap_to_prod, ece_gap_to_prod_rel }
         - queue: { before, after } 형태로 플러시 전/후 추정 큐 사이즈를 보여줍니다.
         - health: 위의 헬스 크리테리아 결과가 그대로 포함됩니다(pass/reasons 등).
         - run_id/total_sec/request_id 도 포함되어 요청 상관관계와 소요시간을 빠르게 파악할 수 있습니다.
       - calibration_delta 는 유지되며, calibration_before/calibration 같은 중량 필드는 제외됩니다.
    - (신규) 실행 식별 및 소요시간:
       - summary.run_id: 각 호출을 고유하게 식별하는 ID (예: qv-<epoch_ms>-<rand>)
       - summary.timing.total_sec: 전체 흐름(예측→플러시→라벨→캘리브레이션) 소요시간
       - compact 모드도 run_id, total_sec 포함 → 대시보드에서 요청-응답 상관관계 및 성능 추세 추적 용이
    - (신규) 숫자 라운딩 옵션: `decimals=<정수>`
       - calibration/calibration_before/calibration_delta의 ece, brier, mce, samples와 timing.predict_sec/label_sec/total_sec을 지정한 소수점 자리로 반올림합니다.
       - compact=true 응답에서도 calibration_delta와 total_sec 라운딩이 반영됩니다.
    - (신규) 경고/힌트 목록: `summary.warnings`
       - calibration_no_data: 라이브 캘리브레이션 창(window) 내 실현 레이블 데이터가 없어 no_data
       - predict_skipped / label_skipped / flush_skipped: 해당 단계가 스킵됨
       - calibration_retried: no_data로 인해 calibration_retries>0로 재시도 수행됨
       - 대시보드에서 배지로 표기하면 상태 파악이 빨라집니다.
    - (신규) 에러 목록: `summary.errors`
       - seed_error / batch_error / flush_error / label_error / calibration_error
       - 어느 단계에서 실패했는지 한눈에 확인 가능하며, run_id / request_id와 같이 사용하면 근본 원인 파악이 빨라집니다.
      - (신규) 요청 상관관계: `summary.request_id`
       - 미들웨어가 X-Request-ID 헤더 또는 uuid4로 부여하는 상관관계 ID입니다.
       - run_id와 함께 로그/추적 시스템에서 동일 요청을 쉽게 찾을 수 있습니다.

### 헬스 크리테리아 예시

아래는 헬스 기준을 함께 사용하는 예시입니다.

요청 예: (ECE는 0.1 이하, 샘플은 10 이상, MCE는 0.2 이하를 기대)

POST /admin/inference/quick-validate?target=bottom&ece_target=0.1&min_samples=10&max_mce=0.2

응답 요약 예:

summary.health = {
   pass: true,
   reasons: [],
   ece_target: 0.1,
   min_samples: 10,
   max_mce: 0.2
}

만약 live ECE=0.23, MCE=0.35, samples=5 라면:

summary.health = {
   pass: false,
   reasons: ["ece_above_target", "insufficient_samples", "mce_above_max"],
   ece_target: 0.1,
   min_samples: 10,
   max_mce: 0.2
}

### Prod 대비 격차 (ECE Gap) 표시

- quick-validate 응답에는 production_ece(전체 응답)와 ece_gap_to_prod 값이 포함됩니다.
   - production_ece: 현재 프로덕션(또는 최근) 모델의 검증 ECE
   - ece_gap_to_prod: live ECE - production ECE (양수이면 현재 라이브가 더 나쁨)
   - ece_gap_to_prod_rel: (live ECE - production ECE) / production ECE (비율, 0.2는 20% 악화 의미)
- compact=true 응답에서도 summary.ece_gap_to_prod 및 ece_gap_to_prod_rel 이 포함되며, decimals 파라미터로 반올림됩니다.
- hints 에 "live ECE가 prod보다 높음" 문구가 추가될 수 있으며, 드리프트 가능성을 빠르게 인지하는 데 유용합니다.

옵션 B) 수동 단계별 실행
1) 로그 생성과 적재
- (여러 번) GET /api/inference/predict?threshold=0.5
- POST /admin/inference/queue/flush

2) 빠른 라벨 생성(서버 재시작 없이)
- POST /api/inference/labeler/run?min_age_seconds=0&limit=200&lookahead=5&drawdown=0.005&rebound=0.003
- pending이면 미래 봉(lookahead) 부족 → 잠시 후 재실행

3) 라이브 캘리브레이션/모니터 확인
- GET /api/inference/calibration/live?window_seconds=1800&bins=10&target=bottom
- GET /api/monitor/calibration/status

옵션 C) 합성 로그로 즉시 후보 만들기(개발/테스트)
- POST /admin/inference/seed-logs?count=20&probability=0.6&threshold=0.5&symbol=XRPUSDT&interval=1m&backdate_seconds=180
   - backdate_seconds>0 이면 생성 직후 min_age 를 바로 충족하도록 created_at 을 과거로 이동합니다.
   - 그 다음 /api/inference/labeler/run → /api/inference/calibration/live 순으로 확인하세요.

모델 선택 팁
- 최신 스테이징 모델로 바로 점검하려면 quick-validate 호출 시 prefer_latest=true 를 주면 됩니다.
- 특정 버전 문자열이 필요한 경우 version="v2025-10-21-001" 같은 값을 함께 전달하세요. 둘 다 지정되면 version 우선.

스텝 스킵 활용 예시
- 최근 생성한 예측과 별개로 라벨러와 캘리브레이션만 보고 싶다면 skip_predict=true 로 호출합니다.
- 반대로 큐에 쌓이는 예측량과 threshold 동작만 보고 싶다면 skip_label=true 로 라벨링 단계를 건너뜁니다.

## 주의 사항 / 팁

- 운영 기본 파라미터(CFG)는 모듈 로드시 고정됩니다. /admin/env/reload로는 BOTTOM_LOOKAHEAD 같은 값이 즉시 반영되지 않습니다.
  - 수동 라벨러 실행 시 오버라이드 파라미터(lookahead/drawdown/rebound)를 활용하세요.
- 후보가 없거나(pending/no_candidates) 미래 봉이 부족하면 라벨이 생성되지 않을 수 있습니다. lookahead를 낮추거나 시간을 두고 재실행하세요.
- 멀티 심볼/인터벌 확장 시에도 정확도를 위해 라벨러는 후보를 (symbol, interval, target)별로 그룹화하여 해당 OHLCV로 계산합니다.

## 트러블슈팅

- /api/inference/labeler/run 에러: 'feature_snapshot' 관련 스키마 에러 힌트가 나오면 먼저 POST /admin/features/backfill로 피처 스냅샷을 채우고 재시도하세요.
- /api/inference/calibration/live가 no_data: 라벨러가 아직 실현 레이블을 생성하지 못했거나 window에 데이터가 없는 경우입니다. 위 레시피대로 라벨 생성 후 재시도하세요.
   - (개선) 서버가 자동으로 백그라운드 트리거 + (기본) 동기 라벨링을 시도합니다. 즉시 해소되지 않으면 시간이 조금 지난 뒤 재시도하거나, `/api/inference/labeler/run`을 통해 강제로 라벨을 채울 수 있습니다.
- Windows 셸에서 curl/jq 루프가 130(exit) 등으로 중단되는 경우가 있습니다. 이때는 원샷 엔드포인트(/admin/inference/quick-validate)를 사용하면 루프 없이 동일 효과를 얻을 수 있습니다.
- quick-validate는 내부적으로 only_target/symbol/interval 필터를 라벨러에 전달합니다. 방금 생성된 예측과 동일 범위를 즉시 라벨링하므로 no_data 발생 가능성이 낮습니다.
