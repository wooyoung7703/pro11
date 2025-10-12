#!/usr/bin/env python3
import json
import random
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = '127.0.0.1'
PORT = 8089

REASONS = [
    'daily_loss_cap',
    'max_drawdown',
    'symbol_exposure',
    'volatility_filter',
    'slippage_guard'
]

class SSEHandler(BaseHTTPRequestHandler):
    def _send_headers(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()

    def do_GET(self):
        if self.path != '/stream/risk':
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')
            return
        self._send_headers()
        epoch = datetime.now(timezone.utc).isoformat()
        seq = 0
        daily_budget = 50.0  # e.g., USDT budget of daily loss
        daily_used = 0.0
        dd_pct = 0.0
        exposure = 0.0
        reasons = []

        snapshot = {
            'type': 'snapshot',
            'seq': seq,
            'epoch': epoch,
            'server_time': datetime.now(timezone.utc).isoformat(),
            'channel': 'risk',
            'data': {
                'daily_loss_budget': daily_budget,
                'daily_loss_used': daily_used,
                'drawdown_pct': dd_pct,
                'symbol_exposure': exposure,
                'blocking_reasons': reasons
            }
        }
        self.wfile.write(f"data: {json.dumps(snapshot)}\n\n".encode())
        self.wfile.flush()

        try:
            while True:
                time.sleep(2)
                seq += 1
                server_time = datetime.now(timezone.utc).isoformat()
                # random walk of risk metrics
                daily_used = max(0.0, min(daily_budget, daily_used + random.uniform(-2.0, 5.0)))
                dd_pct = max(0.0, min(0.25, dd_pct + random.uniform(-0.01, 0.02)))
                exposure = max(0.0, min(300.0, exposure + random.uniform(-20.0, 40.0)))

                reasons = []
                if daily_used / daily_budget > 0.9:
                    reasons.append('daily_loss_cap')
                if dd_pct > 0.2:
                    reasons.append('max_drawdown')
                if exposure > 200.0:
                    reasons.append('symbol_exposure')
                if random.random() < 0.05:
                    reasons.append('volatility_filter')
                if random.random() < 0.03:
                    reasons.append('slippage_guard')

                delta = {
                    'type': 'delta',
                    'seq': seq,
                    'epoch': epoch,
                    'server_time': server_time,
                    'channel': 'risk',
                    'data': {
                        'daily_loss_used': daily_used,
                        'drawdown_pct': dd_pct,
                        'symbol_exposure': exposure,
                        'blocking_reasons': reasons
                    }
                }
                self.wfile.write(f"data: {json.dumps(delta)}\n\n".encode())
                self.wfile.flush()

                if seq % 5 == 0:
                    hb = {
                        'type': 'heartbeat',
                        'seq': seq,
                        'epoch': epoch,
                        'server_time': server_time,
                        'channel': 'risk',
                        'data': {}
                    }
                    self.wfile.write(f"data: {json.dumps(hb)}\n\n".encode())
                    self.wfile.flush()
        except BrokenPipeError:
            pass

if __name__ == '__main__':
    httpd = HTTPServer((HOST, PORT), SSEHandler)
    print(f"Mock Risk SSE server running at http://{HOST}:{PORT}/stream/risk")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
