# ML 신호 전환 빠른 시작

이 문서는 룰 기반(low_buy) 대신 ML 기반 신호로 전환하고 스모크 테스트 하는 절차를 간단히 정리합니다.

## 1) 환경 변수 설정
`.env` 또는 `.env.private`에 아래 키를 추가/수정하세요.

- SIGNAL_SOURCE=ml
- ML_AUTO_LOOP_ENABLED=true (자동 루프 켜기)
- ML_AUTO_LOOP_INTERVAL=10 (초)
- ML_SIGNAL_THRESHOLD=0.7 (의사결정 임계값)
- ML_SIGNAL_COOLDOWN_SEC=120 (중복 신호 쿨다운)
- ML_AUTO_EXECUTE_ENABLED=true (자동 주문 허용)
- ML_AUTO_EXECUTE_PAPER=true (실거래 방지 - 시뮬)
- ML_DEFAULT_SIZE=1
- EXCHANGE_TRADING_ENABLED=0 (실거래 비활성)

주의: 운영이 아니라면 실거래 키/옵션을 비활성으로 유지하세요.

## 2) 백엔드 재시작
개발 환경에서 uvicorn을 재시작합니다. VS Code Tasks: Run Backend (uvicorn)

## 3) 엔드포인트 스모크
API 키 헤더: X-API-Key: dev-key (기본)

- POST /api/trading/ml/preview {"debug": false}
- POST /api/trading/ml/trigger {"auto_execute": false}
- POST /api/trading/ml/trigger {"auto_execute": true, "size": 1}

응답에 probability, decision, model_version, triggered, executed 등이 포함됩니다.

## 4) 자동 루프 확인
SIGNAL_SOURCE=ml, ML_AUTO_LOOP_ENABLED=true 일 때 백엔드는 주기적으로 evaluate→trigger를 수행합니다. 쿨다운이 적용되어 과도한 신호를 억제합니다.

## 5) 문제 해결 팁
- 401 Unauthorized: 헤더 X-API-Key 확인
- no_data/insufficient_features: /admin/features/compute-now 실행, 피처 파이프라인 상태 확인
- artifact_not_found 등: /admin/models/artifacts/verify 확인 후 재학습 트리거

---
더 자세한 설계/튜닝은 코드 주석과 구성(`backend/common/config/base_config.py`)을 참고하세요.
