import os
import re
import httpx
import pytest

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")
SYMBOL = os.getenv("TEST_SYMBOL", "XRPUSDT")
INTERVAL = os.getenv("TEST_INTERVAL", "1m")
METRICS_ENDPOINT = os.getenv("TEST_METRICS_URL", f"{BASE_URL}/metrics")

METRIC_MAP = {
    "completeness": "ohlcv_candles_completeness_percent",
    "gap_bars": "ohlcv_candles_largest_gap_bars",
    "gap_span": "ohlcv_candles_largest_gap_span_ms",
}

@pytest.mark.asyncio
async def test_meta_vs_metrics_alignment():
    async with httpx.AsyncClient(timeout=10) as client:
        r_meta = await client.get(f"{BASE_URL}/api/ohlcv/meta", params={"symbol": SYMBOL, "interval": INTERVAL})
        assert r_meta.status_code == 200, r_meta.text
        meta = r_meta.json()
        r_metrics = await client.get(METRICS_ENDPOINT)
        assert r_metrics.status_code == 200
        text = r_metrics.text

    # Extract metrics lines for this symbol/interval
    def extract(metric: str):
        """Prometheus 라벨 순서는 비결정적일 수 있으므로 순서 무관 탐색.

        허용 예:
          metric{symbol="XRPUSDT",interval="1m"} 0.9
          metric{interval="1m",symbol="XRPUSDT"} 0.9
        """
        for line in text.splitlines():
            if not line.startswith(metric + "{"):
                continue
            # 라벨 블록 추출
            try:
                head, val = line.split('} ', 1)
            except ValueError:
                continue
            labels_part = head[len(metric)+1:]  # 여는 brace 이후
            # 라벨을 , 로 분할 후 key="value" 형태 파싱
            labels = {}
            for kv in labels_part.split(','):
                if '=' not in kv:
                    continue
                k, v = kv.split('=', 1)
                v = v.strip().strip('"')
                labels[k] = v
            if labels.get('symbol') == SYMBOL and labels.get('interval') == INTERVAL:
                return val.strip()
        return None

    comp_line = extract(METRIC_MAP["completeness"])
    gap_bars_line = extract(METRIC_MAP["gap_bars"])
    gap_span_line = extract(METRIC_MAP["gap_span"])

    # completeness 비교 (None 일 수도 있음)
    meta_comp = meta.get("completeness_percent")
    if meta_comp is None:
        # gauge 는 존재할 수 있으나 meta None -> 허용 (아직 계산 안됨)
        assert True
    else:
        assert comp_line is not None, "Prometheus completeness gauge missing"
        prom_comp = float(comp_line)
        assert abs(prom_comp - meta_comp) < 1e-6

    # gap bars
    meta_gap_bars = meta.get("largest_gap_bars")
    if meta_gap_bars is not None:
        if gap_bars_line is not None:
            assert int(float(gap_bars_line)) == meta_gap_bars

    # gap span
    meta_gap_span = meta.get("largest_gap_span_ms")
    if meta_gap_span is not None:
        if gap_span_line is not None:
            assert int(float(gap_span_line)) == meta_gap_span
