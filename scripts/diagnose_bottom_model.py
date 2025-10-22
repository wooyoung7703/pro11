#!/usr/bin/env python3
"""
Bottom 모델 학습 진단 스크립트
- 데이터 상태 확인
- 라벨 분포 분석
- 파라미터 조정 제안
"""

import asyncio
import sys
import os
sys.path.append('/Users/wooyoungyoon/Desktop/pro11')

from backend.common.config import get_config
from backend.apps.training.training_service import TrainingService

async def diagnose_bottom_training():
    """Bottom 모델 학습 상태 진단"""
    print("🔍 Bottom 모델 학습 진단 시작...")
    
    try:
        cfg = get_config()
        print(f"✅ 설정 로드 완료")
        print(f"   - TRAINING_TARGET_TYPE: {getattr(cfg, 'training_target_type', 'NOT SET')}")
        print(f"   - BOTTOM_LOOKAHEAD: {getattr(cfg, 'bottom_lookahead', 'NOT SET')}")
        print(f"   - BOTTOM_DRAWDOWN: {getattr(cfg, 'bottom_drawdown', 'NOT SET')}")
        print(f"   - BOTTOM_REBOUND: {getattr(cfg, 'bottom_rebound', 'NOT SET')}")
        print()
        
        # TrainingService 초기화
        svc = TrainingService(cfg.symbol, cfg.kline_interval)
        print(f"✅ TrainingService 초기화 완료")
        print(f"   - 심볼: {cfg.symbol}")
        print(f"   - 인터벌: {cfg.kline_interval}")
        print()
        
        # 1. 기본 데이터 확인
        print("1️⃣ 기본 피처 데이터 확인...")
        try:
            rows = await svc.load_recent_features(limit=1000)
            print(f"   ✅ 피처 데이터: {len(rows)}개")
            if len(rows) < 200:
                print(f"   ⚠️  경고: 데이터가 부족합니다 (필요: 200개, 현재: {len(rows)}개)")
            print()
        except Exception as e:
            print(f"   ❌ 피처 데이터 로드 실패: {e}")
            return
        
        # 2. OHLCV 데이터 확인
        print("2️⃣ OHLCV 캔들 데이터 확인...")
        try:
            from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as _fetch_kline_recent
            k_rows = await _fetch_kline_recent(cfg.symbol, cfg.kline_interval, limit=1000)
            candles = list(reversed(k_rows))
            print(f"   ✅ OHLCV 데이터: {len(candles)}개")
            if len(candles) < 100:
                print(f"   ⚠️  경고: OHLCV 데이터가 부족합니다")
            print()
        except Exception as e:
            print(f"   ❌ OHLCV 데이터 로드 실패: {e}")
            return
        
        # 3. Bottom 라벨 시뮬레이션
        print("3️⃣ Bottom 라벨 생성 테스트...")
        try:
            # 현재 파라미터로 테스트
            lookahead = getattr(cfg, 'bottom_lookahead', 30)
            drawdown = getattr(cfg, 'bottom_drawdown', 0.005)
            rebound = getattr(cfg, 'bottom_rebound', 0.003)
            
            print(f"   📊 파라미터:")
            print(f"      - LOOKAHEAD: {lookahead}")
            print(f"      - DRAWDOWN: {drawdown} ({drawdown*100:.2f}%)")
            print(f"      - REBOUND: {rebound} ({rebound*100:.2f}%)")
            print()
            
            # 샘플 데이터로 라벨링 테스트
            from backend.apps.training.service.bottom_labeler import compute_bottom_event_label
            
            labels = []
            for i in range(min(100, len(candles) - lookahead)):
                try:
                    label = compute_bottom_event_label(candles, i, lookahead, drawdown, rebound)
                    if label is not None:
                        labels.append(label)
                except Exception:
                    continue
            
            if labels:
                positive_count = sum(labels)
                total_count = len(labels)
                positive_ratio = positive_count / total_count if total_count > 0 else 0
                
                print(f"   📈 라벨 통계 (최근 100개 샘플):")
                print(f"      - 총 라벨: {total_count}개")
                print(f"      - Positive (1): {positive_count}개 ({positive_ratio:.2%})")
                print(f"      - Negative (0): {total_count - positive_count}개 ({1-positive_ratio:.2%})")
                
                if positive_ratio < 0.05:
                    print(f"   ⚠️  경고: Positive 라벨이 너무 적습니다 ({positive_ratio:.2%})")
                    print(f"      파라미터를 더 민감하게 조정하는 것을 권장합니다:")
                    print(f"      - BOTTOM_DRAWDOWN을 0.001-0.003으로 낮추기")
                    print(f"      - BOTTOM_REBOUND를 0.0005-0.002로 낮추기")
                    print(f"      - BOTTOM_LOOKAHEAD를 40-60으로 늘리기")
                elif positive_ratio > 0.3:
                    print(f"   ⚠️  경고: Positive 라벨이 너무 많습니다 ({positive_ratio:.2%})")
                    print(f"      파라미터를 더 보수적으로 조정하는 것을 권장합니다")
                else:
                    print(f"   ✅ 라벨 분포가 적절합니다")
            else:
                print(f"   ❌ 라벨 생성 실패: 유효한 라벨이 없습니다")
            print()
            
        except Exception as e:
            print(f"   ❌ 라벨 테스트 실패: {e}")
            print()
        
        # 4. 실제 학습 시도
        print("4️⃣ 실제 Bottom 모델 학습 테스트...")
        try:
            if hasattr(svc, 'run_training_bottom'):
                result = await svc.run_training_bottom(limit=500, store=False)
                print(f"   📊 학습 결과:")
                print(f"      - 상태: {result.get('status', 'UNKNOWN')}")
                
                if result.get('status') == 'insufficient_labels':
                    have = result.get('have', 0)
                    required = result.get('required', 150)
                    pos_ratio = result.get('pos_ratio', 0)
                    print(f"      - 라벨 부족: {have}개 (필요: {required}개)")
                    print(f"      - Positive 비율: {pos_ratio:.2%}" if pos_ratio else "")
                elif result.get('status') == 'insufficient_data':
                    have = result.get('have', 0)
                    required = result.get('required', 200)
                    print(f"      - 데이터 부족: {have}개 (필요: {required}개)")
                elif result.get('status') == 'ok':
                    metrics = result.get('metrics', {})
                    print(f"      ✅ 학습 성공!")
                    print(f"      - 정확도: {metrics.get('accuracy', 'N/A')}")
                    print(f"      - AUC: {metrics.get('auc', 'N/A')}")
                    print(f"      - F1: {metrics.get('f1', 'N/A')}")
                else:
                    print(f"      - 기타 상태: {result}")
            else:
                print(f"   ❌ run_training_bottom 메서드를 찾을 수 없습니다")
                
        except Exception as e:
            print(f"   ❌ 학습 테스트 실패: {e}")
        
        print()
        print("🎯 진단 완료!")
        print()
        print("💡 권장사항:")
        print("1. OHLCV 데이터 수집이 정상 동작하는지 확인")
        print("2. Bottom 파라미터를 조정하여 라벨 분포 최적화")
        print("3. 충분한 데이터 축적 후 재시도")
        
    except Exception as e:
        print(f"❌ 진단 실패: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(diagnose_bottom_training())