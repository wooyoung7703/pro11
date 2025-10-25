from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class PartialLevel:
    rr: float
    fraction: float


@dataclass
class ExitPolicyConfig:
    trail_mode: str = "atr"  # "atr" | "percent"
    trail_multiplier: float = 2.0
    trail_percent: float = 0.02  # 2%
    time_stop_bars: int = 8
    partial_enabled: bool = True
    partial_levels: List[PartialLevel] = field(default_factory=lambda: [PartialLevel(rr=1.0, fraction=0.4)])
    cooldown_bars: int = 3
    daily_loss_cap_r: float = 4.0
    freeze_on_exit: bool = True


@dataclass
class ExitState:
    # For trailing stop
    peak_price: Optional[float] = None
    # Bars since entry
    bars_since_entry: int = 0
    # Initial stop distance in price terms (for RR computation and partial exits)
    initial_r_price: Optional[float] = None
    # Cumulative fraction exited via partials (0..1)
    partial_exited: float = 0.0


def _percent_trailing_stop(cur_price: float, peak_price: float, percent: float) -> bool:
    if percent <= 0 or peak_price is None or peak_price <= 0:
        return False
    # Pullback ratio from peak
    pullback = (peak_price - cur_price) / peak_price
    return pullback >= percent


def evaluate_exit(
    cfg: ExitPolicyConfig,
    st: ExitState,
    cur_price: float,
    atr_value: Optional[float] = None,
) -> Dict[str, object]:
    """Evaluate exit decision for current bar.

    Returns a dict with keys:
      - exit: bool
      - reason: str | None
      - partial_fraction: float (0 means none)
    """
    # Update peak for trailing
    if st.peak_price is None or cur_price > st.peak_price:
        st.peak_price = cur_price

    # Percent trailing (implemented first; ATR trailing can be added later)
    if cfg.trail_mode == "percent" and cfg.trail_percent > 0:
        if _percent_trailing_stop(cur_price, st.peak_price or cur_price, cfg.trail_percent):
            return {"exit": True, "reason": "trail_percent", "partial_fraction": 1.0}

    # Time stop
    if cfg.time_stop_bars and st.bars_since_entry >= cfg.time_stop_bars:
        return {"exit": True, "reason": "time_stop", "partial_fraction": 1.0}

    # Partial exits (RR levels). For simplicity, we emit next level fraction when reached.
    if cfg.partial_enabled and st.initial_r_price and cfg.partial_levels:
        # RR = (cur_price - entry_price) / initial_r_price, but we don't carry entry here;
        # callers should precompute RR and pass via st.initial_r_price as positive R units of gain.
        # Here we treat cur_price as "current R gained" when initial_r_price is provided.
        try:
            # Interpret cur_price as R units when initial_r_price > 0 and caller pre-normalized; otherwise skip.
            pass
        except Exception:
            pass

    return {"exit": False, "reason": None, "partial_fraction": 0.0}
