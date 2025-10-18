#!/usr/bin/env python3
"""
Bottom 라벨링 간단 테스트
"""

# 간단한 가상 캔들 데이터로 bottom 라벨링 테스트
def test_bottom_labeling():
    """가상 데이터로 bottom 라벨링 로직 테스트"""
    
    # 가상 캔들 데이터 (하락 후 반등 패턴)
    candles = [
        {"open": 100, "high": 102, "low": 99, "close": 100, "close_time": 1000},     # 0
        {"open": 100, "high": 101, "low": 98, "close": 99, "close_time": 1001},      # 1 - 시작점
        {"open": 99, "high": 100, "low": 97, "close": 98, "close_time": 1002},       # 2 - 하락
        {"open": 98, "high": 99, "low": 96, "close": 97, "close_time": 1003},        # 3 - 더 하락 (저점)
        {"open": 97, "high": 98, "low": 96.5, "close": 97.5, "close_time": 1004},   # 4 - 횡보
        {"open": 97.5, "high": 99, "low": 97, "close": 98.5, "close_time": 1005},   # 5 - 반등 시작
        {"open": 98.5, "high": 100, "low": 98, "close": 99.5, "close_time": 1006},  # 6 - 반등 계속
        {"open": 99.5, "high": 101, "low": 99, "close": 100, "close_time": 1007},   # 7 - 원래 가격 회복
        {"open": 100, "high": 102, "low": 100, "close": 101, "close_time": 1008},   # 8 - 추가 상승
    ]
    
    def compute_bottom_event_label(candles, start_idx, lookahead, drawdown, rebound):
        """Bottom 라벨링 로직 (원본과 동일)"""
        n = len(candles)
        if start_idx < 0 or start_idx >= n:
            return None
        
        end_idx = min(n - 1, start_idx + lookahead)
        if end_idx <= start_idx:
            return None
            
        try:
            p0 = float(candles[start_idx]["close"])
        except Exception:
            return None
        
        # find min low in (start_idx+1 .. end_idx)
        min_low = None
        min_idx = None
        for i in range(start_idx + 1, end_idx + 1):
            try:
                lo = float(candles[i]["low"])
            except Exception:
                continue
            if min_low is None or lo < min_low:
                min_low = lo
                min_idx = i
        
        if min_low is None or min_idx is None:
            return None
            
        try:
            drop = (min_low - p0) / p0
        except Exception:
            return None
        
        if drop > (-abs(drawdown)):
            return 0
        
        # rebound: from min_idx to end_idx, max high
        max_high = None
        for i in range(min_idx, end_idx + 1):
            try:
                hi = float(candles[i]["high"])
            except Exception:
                continue
            if max_high is None or hi > max_high:
                max_high = hi
        
        if max_high is None:
            return 0
            
        try:
            rb = (max_high - min_low) / min_low
        except Exception:
            return 0
        
        return 1 if rb >= abs(rebound) else 0
    
    print("🧪 Bottom 라벨링 테스트")
    print("=" * 50)
    
    # 다양한 파라미터로 테스트
    test_cases = [
        {"lookahead": 6, "drawdown": 0.005, "rebound": 0.003, "name": "보수적 (기존)"},
        {"lookahead": 6, "drawdown": 0.002, "rebound": 0.001, "name": "중간"},
        {"lookahead": 6, "drawdown": 0.001, "rebound": 0.0005, "name": "민감한 (새로운)"},
        {"lookahead": 6, "drawdown": 0.02, "rebound": 0.01, "name": "매우 보수적"},
    ]
    
    for case in test_cases:
        print(f"\n📊 {case['name']} 파라미터:")
        print(f"   LOOKAHEAD: {case['lookahead']}")
        print(f"   DRAWDOWN: {case['drawdown']} ({case['drawdown']*100:.2f}%)")
        print(f"   REBOUND: {case['rebound']} ({case['rebound']*100:.3f}%)")
        
        # 각 시점에서 라벨 계산
        labels = []
        print(f"   캔들 수: {len(candles)}, 테스트 범위: 0 ~ {len(candles) - case['lookahead'] - 1}")
        for i in range(len(candles) - case['lookahead']):
            label = compute_bottom_event_label(candles, i, case['lookahead'], 
                                             case['drawdown'], case['rebound'])
            labels.append(label)
            
            # 상세 분석 (시점 1에서만)
            if i == 1:
                p0 = candles[i]["close"]  # 99
                print(f"   🔍 시점 {i} 상세 분석 (시작가격: {p0}):")
                
                # 미래 범위에서 최저가 찾기
                end_idx = min(len(candles) - 1, i + case['lookahead'])
                min_low = None
                min_idx = None
                for j in range(i + 1, end_idx + 1):
                    lo = candles[j]["low"]
                    if min_low is None or lo < min_low:
                        min_low = lo
                        min_idx = j
                        
                if min_low is not None:
                    drop = (min_low - p0) / p0
                    print(f"      최저가: {min_low} (시점 {min_idx})")
                    print(f"      하락률: {drop:.4f} ({drop*100:.2f}%)")
                    print(f"      필요 하락률: {-case['drawdown']} ({-case['drawdown']*100:.2f}%)")
                    print(f"      하락 조건: {drop} > {-case['drawdown']} = {drop > -case['drawdown']}")
                    
                    if drop <= -case['drawdown']:  # 충분한 하락
                        # 반등 확인
                        max_high = None
                        for k in range(min_idx, end_idx + 1):
                            hi = candles[k]["high"]
                            if max_high is None or hi > max_high:
                                max_high = hi
                        
                        if max_high is not None:
                            rb = (max_high - min_low) / min_low
                            print(f"      최고가: {max_high}")
                            print(f"      반등률: {rb:.4f} ({rb*100:.3f}%)")
                            print(f"      필요 반등률: {case['rebound']} ({case['rebound']*100:.3f}%)")
                            print(f"      반등 조건: {rb} >= {case['rebound']} = {rb >= case['rebound']}")
            
            if label is not None:
                p0 = candles[i]["close"]
                print(f"   시점 {i}: 가격 {p0} → 라벨 {label}")
        
        positive_count = sum(1 for l in labels if l == 1)
        valid_count = sum(1 for l in labels if l is not None)
        
        print(f"   결과: {positive_count}/{valid_count} positive 라벨")
        if valid_count > 0:
            print(f"   비율: {positive_count/valid_count:.1%}")

if __name__ == "__main__":
    test_bottom_labeling()