import { http, HttpResponse, delay } from 'msw';

// Simple in-memory state for demo
let enabled = false;
let lastTradeTs: number | null = null;
let params = {
  base_size: 1,
  cooldown_sec: 60,
  last_trade_ts: lastTradeTs,
  allow_scale_in: true,
  scale_in_size_ratio: 0.5,
  scale_in_max_legs: 5,
  scale_in_min_price_move: 0.015,
  scale_in_cooldown_sec: 45,
  scale_in_freeze_on_exit: false,
  exit_slice_seconds: 10,
};
const equity = { starting: 10000, current: 10000, peak: 10000, cumulative_pnl: 0 };
let positions: any[] = [];
let orders: any[] = [];
let siLegsUsed = 0;

export const handlers = [
  http.get('/api/trading/live/status', async ({ request }: { request: Request }) => {
    await delay(200);
    const url = new URL(request.url);
    const source = url.searchParams.get('source') || 'db';
    return HttpResponse.json({
      enabled,
      params,
      equity,
      positions,
      orders,
      orders_source: source,
    });
  }),
  http.get('/api/trading/no_signal_breakdown', async () => {
    await delay(200);
    const nowSec = Math.floor(Date.now()/1000);
    const latestInference = {
      probability: 0.42 + (Math.sin(nowSec/30)/20),
      threshold: 0.5,
      computed_now: true,
      time: new Date().toISOString(),
    };
    const hasPos = positions.length > 0;
    const gates = {
      current_price: 0.5200,
      entry_price: hasPos ? positions[0].entry_price : undefined,
      price_drop: hasPos ? Math.max(0, ((positions[0].entry_price - 0.5200)/positions[0].entry_price)) : 0,
    };
    const scale_in = {
      allow: params.allow_scale_in,
      ready: params.allow_scale_in && positions.length>0,
      parameters: { max_legs: params.scale_in_max_legs, min_price_move: params.scale_in_min_price_move },
      gates,
      state: { cooldown_remaining_sec: 0, legs_used: siLegsUsed },
      min_price_move: params.scale_in_min_price_move,
    };
    const summary = {
      live_enabled: enabled,
      params,
      cooldown_remaining_sec: 0,
      latest_inference: latestInference,
      risk: {},
      scale_in,
    };
    return HttpResponse.json(summary);
  }),
  http.get('/api/trading/scale_in/recommend', async () => {
    await delay(200);
    return HttpResponse.json({ recommendation: {
      scale_in_size_ratio: 0.5,
      scale_in_max_legs: 5,
      scale_in_min_price_move: 0.015,
      scale_in_cooldown_sec: 45,
    }, context: { atr_14: 0.012, equity: equity.current, q_delta: 0.15, cooldown_median_sec: 30, window_seconds: 900, delta_samples_win: 120, delta_samples_pos: 220 } });
  }),
  http.post('/api/trading/live/params', async ({ request }: { request: Request }) => {
    await delay(150);
    const body = await request.json() as any;
    params = { ...params, ...body };
    return HttpResponse.json({ ok: true });
  }),
  http.post('/api/trading/live/enable', async ({ request }: { request: Request }) => {
    await delay(100);
    const url = new URL(request.url);
    enabled = (url.searchParams.get('enabled') === 'true');
    return HttpResponse.json({ ok: true, enabled });
  }),
  http.post('/api/trading/submit', async ({ request }: { request: Request }) => {
    await delay(150);
    const body = await request.json() as any;
    const id = Date.now();
    const side = body.side || 'buy';
    const size = Number(body.size || 0.01);
    const price = 0.5200;
    let reason = body.reason || (side==='buy'?'entry':'manual');
    if (side === 'buy' && body.scale_in) {
      siLegsUsed = Math.min((params.scale_in_max_legs||0), siLegsUsed + 1);
      reason = `scale-in ${siLegsUsed}/${params.scale_in_max_legs || 0}`;
    } else if (side === 'buy') {
      siLegsUsed = 0; // reset legs on fresh entry
    }
    orders.unshift({ id, side, size, price, ts: Math.floor(Date.now()/1000), status: 'filled', reason });
    if (side === 'buy') {
      const entry_price = price;
      positions = [{ symbol: 'XRPUSDT', size, entry_price }];
      equity.current -= (entry_price * size * 0.001); // tiny fee
      lastTradeTs = Math.floor(Date.now()/1000);
  params.last_trade_ts = lastTradeTs as any;
    } else {
      positions = [];
      siLegsUsed = 0;
    }
    return HttpResponse.json({ status: 'ok', id });
  }),
  http.delete('/api/trading/orders/:id', async ({ params: p }: { params: Record<string,string> }) => {
    await delay(100);
    const id = Number(p.id);
    orders = orders.filter(o => o.id !== id);
    return HttpResponse.json({ ok: true });
  }),
  http.get('/api/inference/logs', async () => {
    await delay(120);
    const now = Date.now();
    const logs = Array.from({ length: 200 }).map((_, i) => ({
      created_at: new Date(now - i*60000).toISOString(),
      probability: 0.5 + Math.sin(i/10)/5,
      threshold: 0.5,
      symbol: 'XRPUSDT',
      interval: '1m',
    }));
    return HttpResponse.json({ logs });
  }),
];
