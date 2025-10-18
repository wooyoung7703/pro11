from __future__ import annotations
import os
import time
from typing import Any, Dict, Optional
from backend.apps.ingestion.repository.ohlcv_repository import fetch_recent as fetch_ohlcv_recent
from backend.apps.trading.autopilot.models import AutopilotSignal
from backend.apps.trading.autopilot.service import AutopilotService
from backend.apps.trading.repository.signal_repository import TradingSignalRepository
from backend.apps.trading.service.trading_service import get_trading_service
from backend.apps.risk.service.risk_engine import RiskEngine
from backend.common.config.base_config import load_config


def _compute_rsi(values: list[float], period: int = 14) -> Optional[float]:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss if avg_loss > 0 else float('inf')
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_atr(candles: list[dict[str, Any]], period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(candles)):
        cur = candles[i]
        prev = candles[i - 1]
        high = float(cur["high"])
        low = float(cur["low"])
        prev_close = float(prev["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return None
    window = trs[-period:]
    return float(sum(window) / len(window)) if window else None


class LowBuyService:
    def __init__(self, risk_engine: RiskEngine, autopilot: AutopilotService | None = None):
        self._risk = risk_engine
        self._repo = TradingSignalRepository()
        self._cfg = load_config()
        self._autopilot = autopilot
        self._default_size = float(os.getenv("LOW_BUY_DEFAULT_SIZE", "1"))
        self._signal_type = "low_buy"
        self._atr_period = 14
    # Calibrated defaults to reduce noisy triggers while still surfacing candidates
        self._default_lookback = int(os.getenv("LOW_BUY_LOOKBACK", "25"))
        self._default_distance_pct = max(0.0005, float(os.getenv("LOW_BUY_DISTANCE_PCT", "0.02")))
        self._default_min_drop_pct = max(0.0, float(os.getenv("LOW_BUY_MIN_DROP_PCT", "0.01")))
        vol_ratio_env = os.getenv("LOW_BUY_VOLUME_RATIO")
        try:
            self._default_volume_ratio = float(vol_ratio_env) if vol_ratio_env is not None else None
        except ValueError:
            self._default_volume_ratio = None
        rsi_env = os.getenv("LOW_BUY_RSI_MAX")
        try:
            self._default_rsi_max = float(rsi_env) if rsi_env is not None else None
        except ValueError:
            self._default_rsi_max = None
        momentum_env = os.getenv("LOW_BUY_MAX_MOMENTUM")
        momentum_default: Optional[float]
        if momentum_env is None:
            momentum_default = 0.0
        else:
            trimmed = momentum_env.strip().lower()
            if trimmed in {"", "none", "null"}:
                momentum_default = None
            else:
                try:
                    momentum_default = float(momentum_env)
                except ValueError:
                    momentum_default = 0.0
        self._default_max_momentum = momentum_default
        truthy = {"1", "true", "t", "yes", "y", "on"}
        self._relax_enabled = str(os.getenv("LOW_BUY_RELAX_IF_FILTERED", "1")).strip().lower() in truthy
        try:
            self._relax_distance_mult = max(1.0, float(os.getenv("LOW_BUY_RELAX_DISTANCE_MULT", "1.8")))
        except ValueError:
            self._relax_distance_mult = 1.8
        try:
            self._relax_min_drop_mult = max(0.0, float(os.getenv("LOW_BUY_RELAX_DROP_MULT", "0.5")))
        except ValueError:
            self._relax_min_drop_mult = 0.5
        try:
            self._relax_rsi_delta = float(os.getenv("LOW_BUY_RELAX_RSI_DELTA", "10"))
        except ValueError:
            self._relax_rsi_delta = 10.0
        try:
            self._relax_momentum_delta = float(os.getenv("LOW_BUY_RELAX_MOMENTUM_DELTA", "0.002"))
        except ValueError:
            self._relax_momentum_delta = 0.002

    def _compute_confidence(self, metrics: Dict[str, Any], params: Dict[str, Any]) -> float:
        try:
            distance = float(metrics.get("distance_from_low"))
            threshold = params.get("distance_pct")
            if threshold is None:
                threshold = metrics.get("distance_pct_threshold")
            threshold_val = float(threshold) if threshold is not None else None
            if threshold_val and threshold_val > 0:
                ratio = 1.0 - (distance / threshold_val)
                ratio = max(0.0, min(1.0, ratio))
                return round(ratio, 4)
        except Exception:
            pass
        return 0.6

    async def _clear_autopilot(self, reason: str) -> None:
        if not self._autopilot:
            return
        try:
            await self._autopilot.clear_signal(reason=reason)
        except Exception:
            pass

    async def _notify_autopilot(
        self,
        *,
        symbol: str,
        params: Dict[str, Any],
        metrics: Dict[str, Any],
        evaluation: Dict[str, Any],
        signal_id: Optional[int],
        auto_execute: bool,
        status: str,
        reason: Optional[str] = None,
    ) -> None:
        if not self._autopilot:
            return
        try:
            emitted_ts = float(metrics.get("evaluated_at") or time.time())
        except Exception:
            emitted_ts = time.time()
        try:
            price = metrics.get("current_price")
            price_hint = f"price={float(price):.4f}" if isinstance(price, (int, float)) else None
        except Exception:
            price_hint = None
        reason_text = reason or status
        if price_hint:
            reason_text = f"{reason_text} · {price_hint}" if reason_text else price_hint
        confidence = self._compute_confidence(metrics, params)
        params_copy = {k: v for k, v in params.items()}
        metrics_copy = dict(metrics)
        extra: Dict[str, Any] = {
            "status": status,
            "auto_execute": auto_execute,
            "signal_id": signal_id,
            "params": params_copy,
            "metrics": metrics_copy,
        }
        eval_subset: Dict[str, Any] = {}
        for key in ("executed", "reasons", "risk", "error", "order_response"):
            val = evaluation.get(key)
            if val is not None:
                eval_subset[key] = val
        if eval_subset:
            extra["evaluation"] = eval_subset
        if reason_text:
            extra["reason"] = reason_text
        try:
            signal = AutopilotSignal(
                kind=self._signal_type,
                symbol=symbol,
                confidence=confidence,
                reason=reason_text,
                extra=extra,
                emitted_ts=emitted_ts,
            )
            await self._autopilot.activate_signal(signal)
        except Exception:
            pass

    async def evaluate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(params)
        relaxed_flag = bool(params.pop("_relaxed", False))
        symbol = (params.get("symbol") or self._cfg.symbol).upper()
        interval = params.get("interval") or self._cfg.kline_interval
        lookback = max(5, min(500, int(params.get("lookback", self._default_lookback))))
        distance_pct = float(params.get("distance_pct", self._default_distance_pct))
        min_drop_pct = float(params.get("min_drop_pct", self._default_min_drop_pct))
        momentum_max = params.get("momentum_max")
        if momentum_max is None:
            momentum_max = self._default_max_momentum
        else:
            try:
                momentum_max = float(momentum_max)
            except Exception:
                momentum_max = self._default_max_momentum
        volume_ratio_req = params.get("volume_ratio")
        if volume_ratio_req is None:
            volume_ratio_req = self._default_volume_ratio
        volume_ratio_req = float(volume_ratio_req) if volume_ratio_req is not None else None
        rsi_max = params.get("rsi_max")
        if rsi_max is None:
            rsi_max = self._default_rsi_max
        rsi_max = float(rsi_max) if rsi_max is not None else None

        fetch_limit = max(lookback + self._atr_period + 2, 30)
        candles_raw = await fetch_ohlcv_recent(symbol, interval, limit=fetch_limit)
        if not candles_raw or len(candles_raw) < lookback:
            return {
                "triggered": False,
                "status": "insufficient_data",
                "symbol": symbol,
                "interval": interval,
                "lookback": lookback,
                "candles_used": len(candles_raw),
                "params": params,
            }
        # fetch_recent returns DESC order -> reverse for chronological calculations
        candles = list(reversed(candles_raw))
        closes = [float(c["close"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        volumes = [float(c.get("volume") or 0.0) for c in candles]
        latest = candles[-1]
        current_price = float(latest["close"])
        current_volume = float(latest.get("volume") or 0.0)
        prev_close = closes[-2] if len(closes) >= 2 else None

        lookback_slice = candles[-lookback:]
        lows_slice = [float(c["low"]) for c in lookback_slice]
        highs_slice = [float(c["high"]) for c in lookback_slice]
        volumes_slice = [float(c.get("volume") or 0.0) for c in lookback_slice]
        lowest_low = min(lows_slice)
        highest_high = max(highs_slice)
        distance_from_low = (current_price - lowest_low) / lowest_low if lowest_low > 0 else None
        drop_from_high = (highest_high - current_price) / highest_high if highest_high > 0 else None
        avg_volume = sum(volumes_slice) / len(volumes_slice) if volumes_slice else 0.0
        volume_ratio = (current_volume / avg_volume) if avg_volume > 0 else None
        rsi = _compute_rsi(closes, period=14)
        atr = _compute_atr(candles, period=self._atr_period)
        momentum = None
        if isinstance(prev_close, (int, float)) and prev_close > 0:
            try:
                momentum = (current_price - float(prev_close)) / float(prev_close)
            except Exception:
                momentum = None

        triggered = True
        reasons: list[str] = []

        if distance_from_low is None:
            triggered = False
            reasons.append("distance_calc_failed")
        elif distance_from_low > distance_pct:
            triggered = False
            reasons.append("distance_threshold")

        if min_drop_pct > 0 and (drop_from_high is None or drop_from_high < min_drop_pct):
            triggered = False
            reasons.append("drop_threshold")

        if volume_ratio_req is not None:
            if volume_ratio is None or volume_ratio < volume_ratio_req:
                triggered = False
                reasons.append("volume_ratio")

        if rsi_max is not None:
            if rsi is None or rsi > rsi_max:
                triggered = False
                reasons.append("rsi_threshold")

        if momentum_max is not None:
            if momentum is None or momentum > momentum_max:
                triggered = False
                reasons.append("momentum_threshold")

        metrics = {
            "current_price": current_price,
            "lowest_low": lowest_low,
            "highest_high": highest_high,
            "distance_from_low": distance_from_low,
            "distance_pct_threshold": distance_pct,
            "drop_from_high": drop_from_high,
            "drop_pct_threshold": min_drop_pct,
            "volume_ratio": volume_ratio,
            "volume_ratio_threshold": volume_ratio_req,
            "avg_volume": avg_volume,
            "current_volume": current_volume,
            "rsi": rsi,
            "rsi_threshold": rsi_max,
            "atr": atr,
            "momentum": momentum,
            "momentum_threshold": momentum_max,
            "evaluated_at": time.time(),
        }

        evaluation = {
            "triggered": triggered,
            "status": "ok" if triggered else ("filtered" if reasons else "ok"),
            "symbol": symbol,
            "interval": interval,
            "lookback": lookback,
            "params": params,
            "metrics": metrics,
            "reasons": reasons,
        }
        if relaxed_flag:
            evaluation["relaxed"] = True

        if not triggered and self._relax_enabled and not relaxed_flag and reasons:
            relaxed_params = dict(params)
            if "distance_pct" not in params:
                relaxed_params["distance_pct"] = distance_pct * self._relax_distance_mult
            else:
                relaxed_params["distance_pct"] = float(params["distance_pct"]) * self._relax_distance_mult
            if "min_drop_pct" not in params:
                relaxed_params["min_drop_pct"] = min_drop_pct * self._relax_min_drop_mult
            else:
                relaxed_params["min_drop_pct"] = float(params["min_drop_pct"]) * self._relax_min_drop_mult
            if "rsi_max" in params and params["rsi_max"] is not None:
                relaxed_params["rsi_max"] = float(params["rsi_max"]) + self._relax_rsi_delta
            elif self._default_rsi_max is not None:
                relaxed_params["rsi_max"] = self._default_rsi_max + self._relax_rsi_delta
            if "momentum_max" not in params:
                base_momentum = momentum_max if momentum_max is not None else self._default_max_momentum
                if base_momentum is not None:
                    relaxed_params["momentum_max"] = float(base_momentum) + self._relax_momentum_delta
            else:
                try:
                    relaxed_params["momentum_max"] = float(params["momentum_max"]) + self._relax_momentum_delta
                except Exception:
                    base_momentum = momentum_max if momentum_max is not None else self._default_max_momentum
                    if base_momentum is not None:
                        relaxed_params["momentum_max"] = float(base_momentum) + self._relax_momentum_delta
            relaxed_params["_relaxed"] = True
            relaxed_eval = await self.evaluate(relaxed_params)
            if relaxed_eval.get("triggered"):
                relaxed_eval.setdefault("relaxed", True)
                relaxed_eval.setdefault("relaxed_reasons", reasons)
            return relaxed_eval

        return evaluation

    async def trigger(
        self,
        params: Dict[str, Any],
        *,
        auto_execute: bool,
        size: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        evaluation = await self.evaluate(params)
        evaluation.setdefault("reasons", [])
        evaluation.setdefault("executed", False)

        if not evaluation.get("triggered"):
            evaluation.setdefault("signal_id", None)
            await self._clear_autopilot("low_buy_not_triggered")
            return evaluation

        metrics = evaluation.get("metrics", {})
        symbol = str(evaluation.get("symbol") or self._cfg.symbol).upper()
        current_price = float(metrics.get("current_price") or 0.0)
        signal_extra = {"metrics": metrics}
        try:
            record = await self._repo.insert_signal(
                signal_type=self._signal_type,
                status="triggered",
                params={k: v for k, v in params.items()},
                price=current_price if current_price > 0 else None,
                extra=signal_extra,
            )
            signal_id = (record or {}).get("id")
        except Exception:
            signal_id = None
        evaluation["signal_id"] = signal_id

        base_reason = reason or "low_buy"
        if not auto_execute:
            await self._notify_autopilot(
                symbol=symbol,
                params=params,
                metrics=metrics,
                evaluation=evaluation,
                signal_id=signal_id,
                auto_execute=False,
                status="queued",
                reason=base_reason,
            )
            return evaluation

        order_size = float(size) if size else self._default_size
        if order_size <= 0:
            evaluation["reasons"].append("invalid_size")
            await self._notify_autopilot(
                symbol=symbol,
                params=params,
                metrics=metrics,
                evaluation=evaluation,
                signal_id=signal_id,
                auto_execute=True,
                status="invalid_size",
                reason="invalid_size",
            )
            return evaluation

        atr_value = metrics.get("atr")
        risk_check = self._risk.evaluate_order(
            symbol,
            current_price,
            order_size,
            atr=float(atr_value) if isinstance(atr_value, (int, float)) else None,
        )
        if not risk_check.get("allowed"):
            evaluation["risk"] = risk_check
            evaluation["reasons"].append("risk_blocked")
            if signal_id is not None:
                try:
                    await self._repo.mark_result(
                        signal_id=signal_id,
                        status="rejected",
                        order_side="buy",
                        order_size=order_size,
                        order_price=current_price,
                        error=",".join(risk_check.get("reasons") or []),
                        extra={"risk": risk_check},
                    )
                except Exception:
                    pass
            await self._notify_autopilot(
                symbol=symbol,
                params=params,
                metrics=metrics,
                evaluation=evaluation,
                signal_id=signal_id,
                auto_execute=True,
                status="risk_blocked",
                reason="risk_blocked",
            )
            return evaluation

        trading_service = get_trading_service(self._risk)
        submit_reason = base_reason
        try:
            submit_res = await trading_service.submit_market(
                symbol=symbol,
                side="buy",
                size=order_size,
                price=current_price,
                atr=float(atr_value) if isinstance(atr_value, (int, float)) else None,
                reason=submit_reason,
            )
        except Exception as exc:
            evaluation["error"] = str(exc)
            evaluation["reasons"].append("submit_error")
            if signal_id is not None:
                try:
                    await self._repo.mark_result(
                        signal_id=signal_id,
                        status="rejected",
                        order_side="buy",
                        order_size=order_size,
                        order_price=current_price,
                        error=str(exc),
                        extra={"submit_exception": True},
                    )
                except Exception:
                    pass
            await self._notify_autopilot(
                symbol=symbol,
                params=params,
                metrics=metrics,
                evaluation=evaluation,
                signal_id=signal_id,
                auto_execute=True,
                status="submit_error",
                reason=str(exc),
            )
            return evaluation

        status = str(submit_res.get("status") or "submitted").lower()
        evaluation["order_response"] = submit_res
        evaluation["executed"] = status == "filled"
        if signal_id is not None:
            try:
                await self._repo.mark_result(
                    signal_id=signal_id,
                    status="filled" if evaluation["executed"] else status,
                    order_side="buy",
                    order_size=order_size,
                    order_price=current_price,
                    error=None if evaluation["executed"] else submit_res.get("error"),
                    extra={"order_response": submit_res},
                )
            except Exception:
                pass

        final_status = "filled" if evaluation["executed"] else status
        await self._notify_autopilot(
            symbol=symbol,
            params=params,
            metrics=metrics,
            evaluation=evaluation,
            signal_id=signal_id,
            auto_execute=True,
            status=final_status,
            reason=submit_reason,
        )
        return evaluation


__all__ = ["LowBuyService"]
