export interface Candle {
  open_time: number; // ms epoch
  close_time: number; // ms epoch
  open: number; high: number; low: number; close: number;
  volume?: number;
}

export interface DcaParams {
  baseNotional: number;         // quote currency spend for initial buy (e.g., USDT)
  addRatio: number;             // additional buy notional = prev notional × addRatio (e.g., 0.5)
  maxLegs: number;              // total legs including initial buy
  cooldownSec: number;          // minimum seconds between scale-ins
  minPriceMovePct: number;      // minimum relative drop from last entry (e.g., 0.005 = 0.5%) to allow next buy
  feeRate: number;              // taker fee per side (e.g., 0.001 = 0.1%)
  takeProfitPct: number;        // close all when net profit >= this fraction (e.g., 0.01 = 1%)
  trailingTakeProfitPct?: number; // optional: when price pulls back this fraction from peak since entry (and ROI>0), close all
  maxHoldingBars?: number;        // optional: maximum number of bars to hold before forcing exit
}

export interface TradeEvent {
  time: number;                 // use candle open_time (ms)
  side: 'buy' | 'sell';
  price: number;
  qty: number;                  // base asset quantity
  notional: number;             // quote spent/received before fees
  fee: number;                  // quote fee for this trade
  leg: number;                  // 1..maxLegs for buys, 0 for sell-all
  note?: string;
}

export interface DcaResult {
  trades: TradeEvent[];
  realizedPnl: number;          // quote
  realizedRoi: number | null;   // fraction, relative to totalCost (including fees)
  totalFees: number;            // quote
  closed: boolean;              // whether TP was hit and position closed
  avgEntry?: number;            // avg entry price (if open)
  positionQty?: number;         // base qty (if open)
  positionCost?: number;        // total quote spent (if open)
}

/**
 * Simulate a simple long-only DCA with scale-in and take-profit.
 * - Executes initial buy at the first candle's close price.
 * - Optionally scales in on sufficient drop and cooldown, capped by maxLegs.
 * - Closes entire position when net profit >= takeProfitPct (after all fees including exit fee).
 */
export function simulateDCA(candles: Candle[], params: DcaParams): DcaResult {
  const { baseNotional, addRatio, maxLegs, cooldownSec, minPriceMovePct, feeRate, takeProfitPct } = params;
  const trailingTakeProfitPct = params.trailingTakeProfitPct ?? 0;
  const maxHoldingBars = params.maxHoldingBars ?? 0;
  const out: TradeEvent[] = [];
  if (!candles || candles.length === 0) return { trades: out, realizedPnl: 0, realizedRoi: null, totalFees: 0, closed: false };

  // State
  let legs = 0;
  let positionQty = 0;           // base
  let totalCost = 0;             // quote, sum of notional spent on buys
  let totalFees = 0;             // quote, all fees (buy + sell)
  let lastEntryPrice = NaN;      // for min price move filter
  let lastBuyTimeSec = -1e12;    // seconds
  let prevLegNotional = baseNotional;
  let peakPrice = NaN;           // track peak price since entry for trailing TP

  const first = candles[0];
  // Initial buy at first candle close
  const p0 = first.close;
  const q0 = baseNotional / p0;
  const fee0 = baseNotional * feeRate;
  out.push({ time: first.open_time, side: 'buy', price: p0, qty: q0, notional: baseNotional, fee: fee0, leg: 1, note: 'initial' });
  legs = 1;
  positionQty += q0;
  totalCost += baseNotional;
  totalFees += fee0;
  lastEntryPrice = p0;
  lastBuyTimeSec = Math.floor(first.open_time / 1000);
  peakPrice = p0;

  for (let i = 1; i < candles.length; i++) {
    const c = candles[i];
    const tSec = Math.floor(c.open_time / 1000);
    const price = c.close;

    // Scale-in conditions
    if (legs < maxLegs) {
      const cooldownOk = (tSec - lastBuyTimeSec) >= cooldownSec;
      const drop = (lastEntryPrice > 0) ? (lastEntryPrice - price) / lastEntryPrice : 0;
      const dropOk = drop >= minPriceMovePct;
      if (cooldownOk && dropOk) {
        const legNotional = prevLegNotional * addRatio;
        if (legNotional > 0) {
          const qty = legNotional / price;
          const fee = legNotional * feeRate;
          legs += 1;
          out.push({ time: c.open_time, side: 'buy', price, qty, notional: legNotional, fee, leg: legs, note: `scale-in x${addRatio}` });
          positionQty += qty;
          totalCost += legNotional;
          totalFees += fee;
          lastEntryPrice = price;
          lastBuyTimeSec = tSec;
          prevLegNotional = legNotional;
        }
      }
    }

    // Update trailing peak
    if (price > peakPrice) peakPrice = price;

    // Check take-profit on each bar close, considering exit fee
    const grossValue = positionQty * price;                   // quote value
    const exitFee = grossValue * feeRate;                     // selling all now
    const netProfit = grossValue - exitFee - totalCost - totalFees; // after all fees
    const roi = totalCost > 0 ? (netProfit / totalCost) : null;
    if (roi != null && roi >= takeProfitPct) {
      // Close position: sell all
      const notional = positionQty * price;
      const fee = exitFee;
      out.push({ time: c.open_time, side: 'sell', price, qty: positionQty, notional, fee, leg: 0, note: `TP ${Math.round(takeProfitPct*1000)/10}%` });
      totalFees += fee;
      const realizedPnl = notional - fee - totalCost - (totalFees - fee); // net of all fees
      return { trades: out, realizedPnl, realizedRoi: totalCost>0? realizedPnl/totalCost : null, totalFees, closed: true };
    }

    // Trailing take-profit: if enabled and ROI positive, sell when pullback >= trailingTakeProfitPct from peak
    if (trailingTakeProfitPct > 0 && roi != null && roi > 0 && peakPrice > 0) {
      const pullback = (peakPrice - price) / peakPrice;
      if (pullback >= trailingTakeProfitPct) {
        const notional = positionQty * price;
        const fee = exitFee; // reuse computed exit fee at current price
        out.push({ time: c.open_time, side: 'sell', price, qty: positionQty, notional, fee, leg: 0, note: `TRAIL ${Math.round(trailingTakeProfitPct*1000)/10}%` });
        totalFees += fee;
        const realizedPnl = notional - fee - totalCost - (totalFees - fee);
        return { trades: out, realizedPnl, realizedRoi: totalCost>0? realizedPnl/totalCost : null, totalFees, closed: true };
      }
    }

    // Max holding bars: force exit after N bars from initial entry
    if (maxHoldingBars > 0) {
      const barsSinceEntry = i; // i counts bars after first entry
      if (barsSinceEntry >= maxHoldingBars) {
        const notional = positionQty * price;
        const fee = exitFee;
        out.push({ time: c.open_time, side: 'sell', price, qty: positionQty, notional, fee, leg: 0, note: `MAX-HOLD ${maxHoldingBars} bars` });
        totalFees += fee;
        const realizedPnl = notional - fee - totalCost - (totalFees - fee);
        return { trades: out, realizedPnl, realizedRoi: totalCost>0? realizedPnl/totalCost : null, totalFees, closed: true };
      }
    }
  }

  // Not closed: report open position snapshot
  return {
    trades: out,
    realizedPnl: 0,
    realizedRoi: null,
    totalFees,
    closed: false,
    avgEntry: positionQty>0? totalCost/positionQty : undefined,
    positionQty: positionQty>0? positionQty : undefined,
    positionCost: positionQty>0? totalCost : undefined,
  };
}

export interface TradeMarker {
  time: number; // ms
  side: 'buy'|'sell';
  price: number;
}

export function toMarkers(trades: TradeEvent[]): TradeMarker[] {
  return trades.map(t => ({ time: t.time, side: t.side, price: t.price }));
}
