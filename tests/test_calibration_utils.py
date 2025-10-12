import pytest
from backend.apps.training.service.calibration_utils import compute_calibration

def test_compute_calibration_basic():
    probs = [0.1,0.2,0.8,0.9]
    labels = [0,0,1,1]
    res = compute_calibration(probs, labels, bins=4)
    assert res['count'] == 4
    assert res['brier'] is not None
    assert res['ece'] is not None
    assert res['mce'] is not None
    assert len(res['reliability_bins']) == 4
    # Perfect grouping expectation: bins with data have sensible structure
    non_empty = [b for b in res['reliability_bins'] if b['count']>0]
    assert len(non_empty) >= 2
