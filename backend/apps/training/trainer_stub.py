"""Training stub module.

This placeholder illustrates where future predictive modeling (sequence model + RL PPO) 
logic will live. It avoids importing heavy libraries unless the optional 'training' 
extra is installed (see pyproject extras section).
"""
from __future__ import annotations
from typing import Any, Dict

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # fallback

#aaaa

def prepare_dataset(rows: list[Dict[str, Any]]):
    """Convert raw DB rows (feature_snapshot) into model-ready arrays.
    For now returns a simple placeholder.
    """
    if np is None:
        return rows
    # Example: extract a few numeric fields if present
    feats = []
    for r in rows:
        feats.append([
            r.get("ret_1", 0.0) or 0.0,
            r.get("ret_5", 0.0) or 0.0,
            r.get("ret_10", 0.0) or 0.0,
            r.get("rsi_14", 0.0) or 0.0,
            r.get("rolling_vol_20", 0.0) or 0.0,
        ])
    return np.array(feats, dtype=float)


def train_predictor(features):  # type: ignore
    """Placeholder for supervised training.
    Returns a dummy model object.
    """
    return {"status": "noop", "num_samples": len(features)}


def train_policy(env_steps: int = 1000):
    """Placeholder for PPO training.
    Returns summary metadata only.
    """
    return {"algo": "PPO", "env_steps": env_steps, "status": "noop"}

__all__ = ["prepare_dataset", "train_predictor", "train_policy"]
