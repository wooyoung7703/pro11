#!/usr/bin/env python
"""
OHLCV 플래그 재검증 스크립트

목표:
 1. REST recent / history / meta 호출
 2. WebSocket snapshot 수신
 3. self_check 호출 및 probe delta 확인
 4. REST vs WS partial candle / open 필드 정책 차이 비교

사용 방법:
  python scripts/verify_ohlcv_flags.py --base http://localhost:8000 --ws ws://localhost:8000/ws/ohlcv

출력 Legend:
 [OK] 정상 / [WARN] 경고 / [ERR] 오류
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from typing import Any, List, Dict, Optional

try:
    import websockets  # type: ignore
    import httpx
except ImportError as e:  # pragma: no cover
    print(f"[ERR] 라이브러리 누락: {e}. pip install httpx websockets 필요", file=sys.stderr)
    sys.exit(1)


@dataclass
class EndpointResult:
    name: str
    ok: bool
    data: Any
    warn: List[str]
    error: Optional[str] = None


def jdump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)[:800]  # 길이 제한 출력


async def fetch_json(client: httpx.AsyncClient, url: str) -> EndpointResult:
    name = url.split('/')[-1]
    try:
        r = await client.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        return EndpointResult(name=name, ok=True, data=data, warn=[])
    except Exception as e:  # noqa: BLE001
        return EndpointResult(name=name, ok=False, data=None, warn=[], error=str(e))


async def ws_snapshot(uri: str) -> EndpointResult:
    try:
        async with websockets.connect(uri, ping_interval=None) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(raw)
            return EndpointResult(name='ws_snapshot', ok=True, data=data, warn=[])
    except Exception as e:  # noqa: BLE001
        return EndpointResult(name='ws_snapshot', ok=False, data=None, warn=[], error=str(e))


def analyze(recent: EndpointResult, history: EndpointResult, meta: EndpointResult, snapshot: EndpointResult, self_check: EndpointResult):
    print('\n=== 분석 결과 ===')
    # 1. recent vs snapshot partial 비교
    if recent.ok and snapshot.ok:
        rc = recent.data.get('candles', [])
        sc = snapshot.data.get('candles', [])
        rc_latest_ot = rc[-1]['open_time'] if rc else None
        sc_latest = sc[-1] if sc else None
        sc_latest_closed = sc_latest.get('is_closed') if sc_latest else None
        delta_len = len(sc) - len(rc)
        # 기대: WS snapshot 이 partial 1개 더 많을 수 있음
        if sc_latest_closed is False and delta_len in (0, 1):
            print(f"[OK] WS snapshot 마지막 partial 감지 (delta_len={delta_len}) is_closed=False")
        else:
            print(f"[WARN] partial 정책 예상과 다를 수 있음 (snapshot_last_is_closed={sc_latest_closed}, delta_len={delta_len})")
        # open 필드 비교
        if rc and 'open' in rc[0]:
            pass  # REST open 포함됨 -> 현재 정책이 'open 제거' 아니라면 정상
        # 존재하지만 정책 상 제거 기대였다면 경고 (flag=false 인데 여전히 포함)
        # Flag = OHLCV_INCLUDE_OPEN_DEFAULT=false 라면 REST open 제거를 기대했다면 아래 경고
        # 구현이 open 필드를 계속 유지하도록 되어 있다면 설명 필요
        if 'open' in rc[0] if rc else False:
            print('[INFO] REST recent open 필드 존재: 구현이 open 필드를 항상 유지하는지 확인 필요')
        if 'open' in sc_latest if sc_latest else False:
            print('[OK] WS snapshot partial 에 open 필드 존재')
    # 2. meta 일관성
    if meta.ok and snapshot.ok:
        m_latest = meta.data.get('latest_open_time') or meta.data.get('meta', {}).get('latest_open_time')
        s_latest = snapshot.data.get('meta', {}).get('latest_open_time')
        if m_latest == s_latest:
            print('[OK] meta.latest_open_time 일치')
        else:
            print(f'[WARN] latest_open_time mismatch meta={m_latest} snapshot={s_latest}')
    # 3. self_check 경고
    if self_check.ok:
        warns = self_check.data.get('warnings', [])
        if not warns:
            print('[OK] self_check warnings 비어있음')
        else:
            print(f'[WARN] self_check warnings={warns}')
        deltas = self_check.data.get('metrics_delta', {})
        if deltas.get('append_attempts_delta', 0) >= 1:
            print('[OK] append_probe delta >=1')
        else:
            print('[WARN] append_probe delta 미증가')


async def main(base: str, ws_uri: str, with_partial: bool):
    async with httpx.AsyncClient(base_url=base) as client:
        recent = await fetch_json(client, f"{base}/api/ohlcv/recent")
        history = await fetch_json(client, f"{base}/api/ohlcv/history?limit=30")
        meta = await fetch_json(client, f"{base}/api/ohlcv/meta")
        snapshot = await ws_snapshot(ws_uri)
        self_check = await fetch_json(client, f"{base}/api/ohlcv/self_check")
        partial_resp = None
        recent_after_partial = None
        snapshot_after_partial = None
        if with_partial:
            partial_resp = await fetch_json(client, f"{base}/admin/ohlcv/mock_partial")
            recent_after_partial = await fetch_json(client, f"{base}/api/ohlcv/recent?limit=50&debug=true")
            snapshot_after_partial = await ws_snapshot(ws_uri)
    # 요약 출력
    for res in [recent, history, meta, snapshot, self_check]:
        status = 'OK' if res.ok else 'ERR'
        print(f"[{status}] {res.name}")
        if res.error:
            print('  error:', res.error)
    # 상세 (필요시 일부만)
    if recent.ok:
        print('\n--- recent 샘플 ---')
        print(jdump({"len": len(recent.data.get('candles', [])), "tail": recent.data.get('candles', [])[-1:] }))
    if snapshot.ok:
        print('\n--- snapshot 마지막 2개 ---')
        sc = snapshot.data.get('candles', [])
        print(jdump(sc[-2:]))
    if self_check.ok:
        print('\n--- self_check metrics_delta ---')
        print(jdump(self_check.data.get('metrics_delta')))    
    analyze(recent, history, meta, snapshot, self_check)

    if with_partial:
        print('\n=== PARTIAL WORKFLOW ===')
        if partial_resp:
            print('[INFO] mock_partial:', jdump(partial_resp.data) if partial_resp.ok else partial_resp.error)
        if recent_after_partial and recent_after_partial.ok:
            candles = recent_after_partial.data.get('candles', [])
            last = candles[-1] if candles else {}
            print('[INFO] recent_after_partial last:', {k: last.get(k) for k in ('open_time','is_closed')})
            dbg = recent_after_partial.data.get('_debug')
            if dbg:
                print('[INFO] recent_after_partial _debug:', dbg)
        if snapshot_after_partial and snapshot_after_partial.ok:
            sc = snapshot_after_partial.data.get('candles', [])
            last = sc[-1] if sc else {}
            print('[INFO] snapshot_after_partial last:', {k: last.get(k) for k in ('open_time','is_closed')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='http://localhost:8000')
    parser.add_argument('--ws', default='ws://localhost:8000/ws/ohlcv')
    parser.add_argument('--with-partial', action='store_true', help='partial 캔들 mock 생성 및 재검증 수행')
    args = parser.parse_args()
    try:
        asyncio.run(main(args.base, args.ws, args.with_partial))
    except KeyboardInterrupt:
        print('Interrupted')
