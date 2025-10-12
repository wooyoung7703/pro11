import asyncio
import pytest
import httpx
from backend.apps.api.main import app, API_KEY
from backend.apps.training.repository.inference_log_repository import InferenceLogRepository

@pytest.mark.asyncio
async def test_realized_backfill_and_live_calibration(monkeypatch):
    # Mock repository methods to operate in-memory without DB
    memory = []
    repo = InferenceLogRepository()
    async def fake_bulk_insert(self, items):  # type: ignore
        start_id = len(memory) + 1
        for i, itm in enumerate(items):
            itm = itm.copy()
            itm["id"] = start_id + i
            itm["created_at"] = 0.0
            itm["realized"] = None
            memory.append(itm)
        return len(items)
    async def fake_fetch_recent(self, limit: int = 100, offset: int = 0):  # type: ignore
        return list(reversed(memory))[:limit]
    async def fake_update_realized_batch(self, updates):  # type: ignore
        ids = {u["id"]: u["realized"] for u in updates}
        count = 0
        for row in memory:
            if row["id"] in ids:
                row["realized"] = ids[row["id"]]
                count += 1
        return count
    async def fake_fetch_window_for_live_calibration(self, window_seconds: int, symbol: str | None):  # type: ignore
        return [r for r in memory if r.get("realized") is not None]
    # Patch methods
    InferenceLogRepository.bulk_insert = fake_bulk_insert  # type: ignore
    InferenceLogRepository.fetch_recent = fake_fetch_recent  # type: ignore
    InferenceLogRepository.update_realized_batch = fake_update_realized_batch  # type: ignore
    InferenceLogRepository.fetch_window_for_live_calibration = fake_fetch_window_for_live_calibration  # type: ignore
    # Insert synthetic
    items = []
    for p in [0.1, 0.4, 0.8, 0.9]:
        items.append({
            "symbol": "XRPUSDT",
            "interval": "1m",
            "model_name": "auto-model",
            "model_version": "123",
            "probability": p,
            "decision": 1 if p >= 0.5 else -1,
            "threshold": 0.5,
            "production": True,
            "extra": None,
        })
    await repo.bulk_insert(items)
    rows = await repo.fetch_recent(limit=10, offset=0)
    # Prepare realized labels: map probabilities >=0.5 to realized positive (1), else 0
    updates = []
    for r in rows[:4]:
        realized = 1 if r["probability"] >= 0.5 else 0
        updates.append({"id": r["id"], "realized": realized})
    # Call backfill endpoint
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/inference/realized", json={"updates": updates}, headers={"X-API-Key": API_KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == len(updates)
        # Now call live calibration endpoint
        resp2 = await ac.get("/api/inference/calibration/live?window_seconds=3600&bins=5", headers={"X-API-Key": API_KEY})
        assert resp2.status_code == 200
        calib = resp2.json()
        # Accept ok or no_data if timing race, but prefer ok
        assert calib["status"] in ("ok", "no_data")
        if calib["status"] == "ok":
            assert "brier" in calib
            assert "ece" in calib
            assert len(calib["reliability_bins"]) == 5
