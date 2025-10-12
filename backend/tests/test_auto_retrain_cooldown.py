import pytest
import time
from backend.apps.training.auto_retrain_scheduler import STATE, auto_retrain_status
from backend.apps.training.auto_retrain_scheduler import CALIBRATION_RETRAIN_COOLDOWN_SECONDS

# 신규 테스트: calibration cooldown 계산 검증
@pytest.mark.asyncio
async def test_auto_status_calibration_cooldown(monkeypatch):
    # 초기화
    STATE.last_calibration_retrain_ts = time.time() - 30  # 30초 전에 retrain
    monkeypatch.setattr('backend.apps.training.auto_retrain_scheduler.CFG.calibration_retrain_min_interval', 120, raising=False)
    monkeypatch.setattr('backend.apps.training.auto_retrain_scheduler.CFG.auto_retrain_min_interval', 60, raising=False)
    status = auto_retrain_status()
    # min_gap = max(60, 120) = 120 -> remaining ~ 90 초 (허용 오차 내)
    rem = status.get('calibration_cooldown_remaining_seconds')
    assert rem is not None and 85 <= rem <= 95
    assert status.get('calibration_next_earliest_retrain_ts') == pytest.approx(STATE.last_calibration_retrain_ts + 120, rel=1e-3)
    # Gauge 값도 0보다 커야 함 (set 호출은 loop 중에만 되지만 status 계산은 직접)
    # 여기서는 Gauge 직접 설정 로직이 loop 안에서만 실행되므로 값 검증 대신 status 값으로 충분.