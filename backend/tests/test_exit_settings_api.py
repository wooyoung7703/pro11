import json
from backend.apps.api.main import app
from starlette.testclient import TestClient


client = TestClient(app)


def get_setting(key: str):
    r = client.get(f"/admin/settings/{key}")
    return r


def put_setting(key: str, value, apply: bool = True):
    payload = {"value": value, "apply": apply}
    r = client.put(f"/admin/settings/{key}", data=json.dumps(payload), headers={"Content-Type": "application/json"})
    return r


def test_exit_partial_levels_sum_validation():
    # Sum > 1 should be rejected
    r = put_setting("exit.partial.levels", [0.6, 0.5])
    assert r.status_code in (400, 422), r.text


def test_exit_trail_percent_validation_range():
    # Negative not allowed
    r_neg = put_setting("exit.trail.percent", -0.01)
    assert r_neg.status_code in (400, 422), r_neg.text
    # Too large (e.g., > 0.5 == 50%) not allowed if server enforces cap
    r_big = put_setting("exit.trail.percent", 0.9)
    assert r_big.status_code in (400, 422), r_big.text


def test_enable_new_policy_toggle_returns_200():
    # Toggling the feature flag should succeed regardless of DB availability
    r1 = put_setting("exit.enable_new_policy", True)
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1.get("status") in ("ok", True)

    r2 = put_setting("exit.enable_new_policy", False)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2.get("status") in ("ok", True)
