from __future__ import annotations
from typing import List, Dict, Any, Optional

def compute_calibration(probs: List[float], labels: List[int], bins: int = 10) -> Dict[str, Any]:
    """Compute Brier, ECE, MCE, reliability bins.

    probs: predicted probabilities (0-1)
    labels: binary {0,1}
    bins: number of reliability bins
    """
    n = len(probs)
    if n == 0:
        return {"count": 0, "brier": None, "ece": None, "mce": None, "reliability_bins": []}
    brier = sum((p - y) ** 2 for p, y in zip(probs, labels)) / n
    bin_width = 1.0 / bins
    counts = [0] * bins
    sum_pred = [0.0] * bins
    sum_label = [0.0] * bins
    for p, y in zip(probs, labels):
        if p < 0: p = 0.0
        if p > 1: p = 1.0
        idx = int(p / bin_width)
        if idx == bins:
            idx = bins - 1
        counts[idx] += 1
        sum_pred[idx] += p
        sum_label[idx] += y
    reliability_bins = []
    ece = 0.0
    mce = 0.0
    for i in range(bins):
        lower = i * bin_width
        upper = (i + 1) * bin_width
        c = counts[i]
        if c > 0:
            mean_pred = sum_pred[i] / c
            empirical = sum_label[i] / c
            gap = abs(empirical - mean_pred)
            ece += (c / n) * gap
            if gap > mce:
                mce = gap
        else:
            mean_pred = None
            empirical = None
            gap = None
        reliability_bins.append({
            "bin_lower": lower,
            "bin_upper": upper,
            "count": c,
            "mean_pred_prob": mean_pred,
            "empirical_prob": empirical,
            "gap": gap,
        })
    return {
        "count": n,
        "brier": brier,
        "ece": ece,
        "mce": mce,
        "reliability_bins": reliability_bins,
    }

__all__ = ["compute_calibration"]
