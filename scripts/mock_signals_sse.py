#!/usr/bin/env python3
import json
import random
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = '127.0.0.1'
PORT = 8088

class SSEHandler(BaseHTTPRequestHandler):
    def _send_headers(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()

    def do_GET(self):
        if self.path != '/stream/signals':
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')
            return
        self._send_headers()
        epoch = datetime.now(timezone.utc).isoformat()
        seq = 0
        # initial snapshot
        snapshot = {
            'type': 'snapshot',
            'seq': seq,
            'epoch': epoch,
            'server_time': datetime.now(timezone.utc).isoformat(),
            'channel': 'signals',
            'data': {
                'mode': 'buy',
                'prob': {'up': 0.55},
                'gates': {
                    'entry_price': 0.5200,
                    'anchor_price': 0.5200,
                    'exit_threshold': 0.65,
                    'exit_remain': 0.10
                },
                'position': {
                    'in_position': False,
                    'avg_entry': None,
                    'scalein_legs': 0
                }
            }
        }
        self.wfile.write(f"data: {json.dumps(snapshot)}\n\n".encode())
        self.wfile.flush()

        mode = 'buy'
        price = 0.5200
        prob_up = 0.55
        in_pos = False
        scale_legs = 0

        try:
            while True:
                time.sleep(1.5)
                seq += 1
                server_time = datetime.now(timezone.utc).isoformat()
                # random walk
                prob_up = max(0.0, min(1.0, prob_up + random.uniform(-0.02, 0.02)))
                price = max(0.3, price + random.uniform(-0.003, 0.003))
                delta_data = {}
                # simple mode switch demo
                if not in_pos and prob_up > 0.6:
                    in_pos = True
                    mode = 'price_gate'
                    delta_data.update({
                        'mode': mode,
                        'position': {'in_position': True, 'avg_entry': price, 'scalein_legs': 0},
                        'gates': {'entry_price': price, 'anchor_price': price}
                    })
                elif in_pos and price >= (snapshot['data']['gates']['entry_price'] * 1.01):
                    mode = 'exit'
                    delta_data.update({'mode': mode})
                else:
                    delta_data.update({'prob': {'up': prob_up}})

                event = {
                    'type': 'delta',
                    'seq': seq,
                    'epoch': epoch,
                    'server_time': server_time,
                    'channel': 'signals',
                    'data': delta_data
                }
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                self.wfile.flush()

                if seq % 8 == 0:
                    hb = {
                        'type': 'heartbeat',
                        'seq': seq,
                        'epoch': epoch,
                        'server_time': server_time,
                        'channel': 'signals',
                        'data': {}
                    }
                    self.wfile.write(f"data: {json.dumps(hb)}\n\n".encode())
                    self.wfile.flush()
        except BrokenPipeError:
            pass

if __name__ == '__main__':
    httpd = HTTPServer((HOST, PORT), SSEHandler)
    print(f"Mock SSE server running at http://{HOST}:{PORT}/stream/signals")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
