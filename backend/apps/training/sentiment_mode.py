"""Sentiment mode runtime state & auto-disable logic.

This module centralizes the enable/disable flag for sentiment feature usage
so that multiple components (training endpoints, future inference blending)
can consult a single source of truth.

Auto-disable policy (initial simple version):
  - Maintain a consecutive failure/exception streak counter fed by the
    sentiment sample generation logic.
  - On each 'success' (fallback path succeeded) or 'normal' (rolling path ok)
    reset the streak to 0.
  - On 'exception' or 'failure' increment the streak.
  - If streak >= DISABLE_STREAK_THRESHOLD => disable sentiment mode and record
    reason = 'failure_streak'. Further events keep counting but won't repeatedly
    re-fire disable action until manually re-enabled.

Environment overrides:
  SENTIMENT_DISABLE_FAILURE_STREAK : int (default 5)

Public functions:
  record_event(phase: str) -> None
  is_enabled() -> bool
  enable() / disable(reason)
  status_dict() -> dict
"""
from __future__ import annotations
import os
import time
from typing import Optional, Dict, Any, List, Deque
from collections import deque

_enabled: bool = True
_failure_streak: int = 0
_last_event_ts: float | None = None
_last_phase: str | None = None
_disable_reason: Optional[str] = None
_history: Deque[dict] | None = None
_v = os.getenv("SENTIMENT_HISTORY_SIZE", "100")
if isinstance(_v, str) and '#' in _v:
    _v = _v.split('#', 1)[0].strip()
_HISTORY_MAX = int(float(_v))

_v = os.getenv("SENTIMENT_DISABLE_FAILURE_STREAK", "5")
if isinstance(_v, str) and '#' in _v:
    _v = _v.split('#', 1)[0].strip()
DISABLE_STREAK_THRESHOLD = int(float(_v))

def record_event(phase: str) -> None:
    """Record a sentiment generation phase outcome.

    phase values expected: 'normal', 'success', 'exception', 'failure'
    """
    global _failure_streak, _last_event_ts, _last_phase, _enabled, _disable_reason
    _last_event_ts = time.time()
    _last_phase = phase
    if phase in ("exception", "failure"):
        _failure_streak += 1
    else:  # normal or success
        _failure_streak = 0
    if _enabled and _failure_streak >= DISABLE_STREAK_THRESHOLD:
        # Auto-disable sentiment mode
        _enabled = False
        _disable_reason = "failure_streak"
        # Note: metrics emission is handled by API layer for central Prometheus registry.
    # Append to history
    try:
        global _history
        if _history is None:
            _history = deque(maxlen=_HISTORY_MAX)
        _history.append({
            "ts": _last_event_ts,
            "phase": phase,
            "failure_streak": _failure_streak,
            "enabled": _enabled,
        })
    except Exception:
        pass

def is_enabled() -> bool:
    return _enabled

def disable(reason: str = "manual") -> None:
    global _enabled, _disable_reason
    _enabled = False
    _disable_reason = reason

def enable() -> None:
    global _enabled, _disable_reason, _failure_streak
    _enabled = True
    _disable_reason = None
    _failure_streak = 0
    # Optional history entry for manual enable
    try:
        global _history
        if _history is None:
            _history = deque(maxlen=_HISTORY_MAX)
        _history.append({
            "ts": time.time(),
            "phase": "manual_enable",
            "failure_streak": _failure_streak,
            "enabled": _enabled,
        })
    except Exception:
        pass

def status_dict() -> Dict[str, Any]:
    return {
        "enabled": _enabled,
        "failure_streak": _failure_streak,
        "disable_reason": _disable_reason,
        "disable_streak_threshold": DISABLE_STREAK_THRESHOLD,
        "last_event_ts": _last_event_ts,
        "last_phase": _last_phase,
        "history_size": len(_history) if _history else 0,
        "history": list(_history) if _history else [],
    }
