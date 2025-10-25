from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class BocpdConfig:
    enabled: bool = False
    hazard: float = 250.0  # interpreted as typical run-length scale (higher -> fewer detections)
    min_down: float = 0.005  # relative drop required to activate gate (e.g., 0.005 => 0.5%)
    cooldown_sec: float = 300.0  # suppress repeated events for this duration


class BocpdGuardService:
    """Lightweight, online drop-change guard inspired by BOCPD.

    We avoid heavy dependencies and implement a simple change detector:
    - Track a local peak_price.
    - When current price has dropped by >= min_down from the peak, we mark a 'down_detected' event
      and allow entries while in this state.
    - A cooldown throttles repeated event emissions (status endpoint visibility), not gating.
    - When an entry is filled, reset the anchor/peak to current price and clear the detection state.

    This is intentionally simple and conservative; it can be swapped for a full BOCPD later
    without changing the integration points.
    """

    def __init__(self, cfg: Optional[BocpdConfig] = None):
        self._cfg = cfg or BocpdConfig()
        self._peak_price: Optional[float] = None
        self._last_event_ts: float = 0.0
        self._down_detected: bool = False
        self._last_price: Optional[float] = None

    def configure(
        self,
        *,
        enabled: Optional[bool] = None,
        hazard: Optional[float] = None,
        min_down: Optional[float] = None,
        cooldown_sec: Optional[float] = None,
    ) -> None:
        if enabled is not None:
            self._cfg.enabled = bool(enabled)
        if hazard is not None and hazard > 0:
            self._cfg.hazard = float(hazard)
        if min_down is not None and min_down >= 0:
            self._cfg.min_down = float(min_down)
        if cooldown_sec is not None and cooldown_sec >= 0:
            self._cfg.cooldown_sec = float(cooldown_sec)

    def update(self, price: Optional[float], ts: Optional[float] = None) -> Dict[str, Any] | None:
        """Update detector with latest price. Returns an event dict when a new state is detected.

        Event schema: { ts, kind: 'down_detected', drop, peak, price }
        """
        if not self._cfg.enabled:
            self._last_price = price if isinstance(price, (int, float)) else self._last_price
            return None
        if not isinstance(price, (int, float)) or price <= 0:
            return None
        now = ts if isinstance(ts, (int, float)) else time.time()
        # initialize / refresh peak
        if self._peak_price is None:
            self._peak_price = float(price)
            self._last_price = float(price)
            return None
        # 새 고점 형성: 피크 갱신과 함께 하락 감지 상태를 해제하여 잘못된 지속 허용을 방지
        if price > self._peak_price:
            self._peak_price = float(price)
            self._last_price = float(price)
            # 새 고점이면 현재 드롭 기준이 초기화되므로 게이트 상태 초기화
            self._down_detected = False
            return None
        # compute drop from peak
        peak = float(self._peak_price)
        drop = (peak - float(price)) / peak if peak > 0 else 0.0
        self._last_price = float(price)
        # 회복: 임계 미만으로 회복하면 게이트 해제
        if self._down_detected and self._cfg.min_down > 0 and drop < self._cfg.min_down:
            self._down_detected = False
        # detection
        if (not self._down_detected) and (self._cfg.min_down > 0) and (drop >= self._cfg.min_down):
            self._down_detected = True
            # emit event if cooldown passed
            if (now - self._last_event_ts) >= max(0.0, self._cfg.cooldown_sec):
                self._last_event_ts = now
                return {
                    "ts": now,
                    "kind": "down_detected",
                    "drop": drop,
                    "peak": peak,
                    "price": float(price),
                }
        return None

    def evaluate(self, price: Optional[float], ts: Optional[float] = None) -> Dict[str, Any]:
        """Return a gate evaluation.

        Returns: { allow_entry: bool, reasons: [..], state: {...} }
        Policy:
          - If disabled: pass-through (allow_entry True)
          - Else: allow_entry only if we are in 'down_detected' state (i.e., sufficient drop observed)
        """
        if not self._cfg.enabled:
            return {"allow_entry": True, "reasons": ["disabled"], "state": self.status()}
        # min_down <= 0 인 경우 게이트를 사실상 비활성 처리(통과)
        if self._cfg.min_down <= 0:
            return {"allow_entry": True, "reasons": ["min_down_disabled"], "state": self.status()}
        allow = bool(self._down_detected)
        reasons: list[str] = []
        if not allow:
            reasons.append("min_down_not_met")
        return {"allow_entry": allow, "reasons": reasons, "state": self.status()}

    def on_entry_filled(self, price: Optional[float], ts: Optional[float] = None) -> None:
        """Reset detection state after an entry fill to avoid chained immediate entries."""
        px = float(price) if isinstance(price, (int, float)) and price > 0 else None
        if px is not None:
            self._peak_price = px
        self._down_detected = False
        # keep last_event_ts for cooldown visibility only

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self._cfg.enabled,
            "hazard": self._cfg.hazard,
            "min_down": self._cfg.min_down,
            "cooldown_sec": self._cfg.cooldown_sec,
            "peak_price": self._peak_price,
            "last_price": self._last_price,
            "down_detected": self._down_detected,
            "last_event_ts": self._last_event_ts,
        }


# Singleton accessor -----------------------------------------------------------
_INSTANCE: BocpdGuardService | None = None


def get_bocpd_guard_service() -> BocpdGuardService:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = BocpdGuardService()
    return _INSTANCE


__all__ = ["BocpdGuardService", "BocpdConfig", "get_bocpd_guard_service"]
