from backend.apps.features.service.feature_calculators import compute_all

def test_compute_all_basic():
    prices = [i for i in range(1, 60)]  # 1..59
    feats = compute_all(prices)
    assert feats["ma_20"] is not None
    assert feats["ma_50"] is not None
    assert feats["ret_1"] is not None
    assert feats["ret_5"] is not None
    assert feats["ret_10"] is not None
