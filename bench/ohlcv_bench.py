import asyncio
import random
import time
from statistics import mean
from typing import List, Dict, Any

# 간단한 성능 벤치 스크립트
# 시나리오:
#  - in-memory candle state 유지
#  - partial_update 이벤트 다수 + partial_close → append 흐름
#  - repair 이벤트 (과거 n개 랜덤 수정)
#  - 일정 라운드 후 처리량(ops), 평균/최대 처리 지연 측정
# 사용: python bench/ohlcv_bench.py

class CandleState:
    def __init__(self):
        self.candles: Dict[int, Dict[str, Any]] = {}
        self.last_open_time: int | None = None
        self.partial: Dict[str, Any] | None = None

    def apply_partial_update(self, c: Dict[str, Any]):
        # partial 은 close 이전 임시
        if not self.partial or self.partial['open_time'] != c['open_time']:
            self.partial = c.copy()
        else:
            p = self.partial
            p['high'] = max(p['high'], c['high'])
            p['low'] = min(p['low'], c['low'])
            p['close'] = c['close']
            if 'volume' in c:
                p['volume'] = c['volume']

    def apply_partial_close(self):
        if not self.partial:
            return
        c = self.partial.copy()
        c['is_closed'] = True
        ot = c['open_time']
        self.candles[ot] = c
        self.last_open_time = ot
        self.partial = None

    def apply_append(self, c: Dict[str, Any]):
        c2 = c.copy()
        c2['is_closed'] = True
        ot = c2['open_time']
        self.candles[ot] = c2
        self.last_open_time = ot

    def apply_repair(self, c: Dict[str, Any]):
        # 과거 수정
        c2 = c.copy()
        c2['is_closed'] = True
        self.candles[c2['open_time']] = c2

    def snapshot_len(self):
        return len(self.candles)


def gen_candle(open_time: int, base_price: float) -> Dict[str, Any]:
    close = base_price + random.uniform(-0.5, 0.5)
    high = max(base_price, close) + random.uniform(0, 0.3)
    low = min(base_price, close) - random.uniform(0, 0.3)
    return {
        'open_time': open_time,
        'close_time': open_time + 60_000,
        'open': base_price,
        'high': high,
        'low': low,
        'close': close,
        'volume': random.randint(100, 1000)
    }

async def run_bench(
    rounds: int = 2000,
    partial_updates_per_candle: int = 5,
    repair_ratio: float = 0.05,
    base_price: float = 100.0,
):
    state = CandleState()
    latencies_partial: List[float] = []
    latencies_repair: List[float] = []
    start_all = time.perf_counter()
    now_open_time = int(time.time() // 60 * 60_000)  # 분 단위 정규화(ms)

    for i in range(rounds):
        # partial lifecycle
        cbase = gen_candle(now_open_time, base_price)
        # partial updates
        for _ in range(partial_updates_per_candle):
            t0 = time.perf_counter()
            state.apply_partial_update(cbase)
            latencies_partial.append(time.perf_counter() - t0)
            # 가격 변동 시뮬레이션
            cbase['close'] += random.uniform(-0.2, 0.2)
            cbase['high'] = max(cbase['high'], cbase['close'])
            cbase['low'] = min(cbase['low'], cbase['close'])
        # close
        t0 = time.perf_counter()
        state.apply_partial_close()
        latencies_partial.append(time.perf_counter() - t0)

        # 다음 candle append (확정) - 일부 러프하게 skip
        if random.random() < 0.3:
            t0 = time.perf_counter()
            next_c = gen_candle(now_open_time + 60_000, base_price + random.uniform(-1, 1))
            state.apply_append(next_c)
            latencies_partial.append(time.perf_counter() - t0)
            now_open_time += 60_000
        else:
            now_open_time += 60_000

        # repair 확률적으로 수행
        if state.snapshot_len() > 10 and random.random() < repair_ratio:
            pick_ot = random.choice(list(state.candles.keys()))
            old = state.candles[pick_ot]
            mutated = old.copy()
            mutated['close'] += random.uniform(-0.5, 0.5)
            mutated['high'] = max(mutated['high'], mutated['close'])
            mutated['low'] = min(mutated['low'], mutated['close'])
            t0 = time.perf_counter()
            state.apply_repair(mutated)
            latencies_repair.append(time.perf_counter() - t0)

    total_elapsed = time.perf_counter() - start_all
    total_ops = len(latencies_partial) + len(latencies_repair)

    def stats(arr: List[float]):
        if not arr:
            return {'count': 0, 'mean_ms': 0, 'p95_ms': 0, 'max_ms': 0}
        s = sorted(arr)
        p95 = s[int(len(s)*0.95)-1] if len(s) >= 1 else 0
        return {
            'count': len(arr),
            'mean_ms': mean(arr)*1000,
            'p95_ms': p95*1000,
            'max_ms': max(arr)*1000,
        }

    result = {
        'rounds': rounds,
        'partial_updates_per_candle': partial_updates_per_candle,
        'repair_ratio': repair_ratio,
        'ops_total': total_ops,
        'elapsed_sec': total_elapsed,
        'ops_per_sec': total_ops / total_elapsed if total_elapsed else 0,
        'partial_stats': stats(latencies_partial),
        'repair_stats': stats(latencies_repair),
        'candles_final': state.snapshot_len(),
    }
    return result


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--rounds', type=int, default=2000)
    ap.add_argument('--partial-updates', type=int, default=5)
    ap.add_argument('--repair-ratio', type=float, default=0.05)
    args = ap.parse_args()

    res = asyncio.run(run_bench(rounds=args.rounds, partial_updates_per_candle=args.partial_updates, repair_ratio=args.repair_ratio))
    print("=== OHLCV BENCH RESULT ===")
    for k,v in res.items():
        print(f"{k}: {v}")

if __name__ == '__main__':
    main()
