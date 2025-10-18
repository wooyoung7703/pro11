import os
from typing import Optional

import httpx
import pytest

from backend.apps.api.main import app
from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository

API_KEY = os.getenv("API_KEY", "dev-key")

@pytest.mark.asyncio
async def test_training_run_with_cv_and_promotion(monkeypatch):
    """cv_splits>1 + store=true 시 promotion 필드 존재 및 구조 검증.

    실제 DB 접근 대신 fetch_latest/ register/ promote 메서드를 모킹.
    """
    # Capture registered models in memory
    registry_rows = []

    async def fake_register(self, name: str, version: str, model_type: str, status: str = "staging", artifact_path: Optional[str] = None, metrics=None):  # type: ignore
        registry_rows.append({
            "id": len(registry_rows) + 1,
            "name": name,
            "version": version,
            "model_type": model_type,
            "status": status,
            "artifact_path": artifact_path,
            "metrics": metrics or {},
        })
        return len(registry_rows)

    async def fake_fetch_latest(self, name: str, model_type: str, limit: int = 5):  # type: ignore
        # Return rows newest first; emulate production row if exists
        rows = [r for r in registry_rows if r["name"] == name and r["model_type"] == model_type]
        # Sort by insertion (latest last) reversed
        rows_sorted = list(reversed(rows))
        return rows_sorted[:limit]

    async def fake_promote(self, model_id: int):  # type: ignore
        for r in registry_rows:
            if r["id"] == model_id:
                r["status"] = "production"
                return r
        return None

    monkeypatch.setattr(ModelRegistryRepository, "register", fake_register)
    monkeypatch.setattr(ModelRegistryRepository, "fetch_latest", fake_fetch_latest)
    monkeypatch.setattr(ModelRegistryRepository, "promote", fake_promote)

    # Pre-seed a production model with moderate metrics for baseline comparison
    registry_rows.append({
        "id": 1,
        "name": "baseline_predictor",
        "version": "1111",
        "model_type": "supervised",
        "status": "production",
        "artifact_path": None,
        "metrics": {"samples": 500, "auc": 0.55, "brier": 0.25, "ece": 0.06},
    })

    # Monkeypatch TrainingService.load_recent_features to yield synthetic ascending feature rows
    from backend.apps.training.training_service import TrainingService
    async def fake_load_recent_features(self, limit: int = 1000):  # type: ignore
        rows = []
        # produce synthetic rows with simple increasing pattern and required fields
        for i in range(300):  # enough to exceed thresholds
            rows.append({
                "open_time": i*60000,
                "close_time": i*60000 + 59000,
                "ret_1": 0.001*(i%5 - 2),
                "ret_5": 0.002*(i%5 - 2),
                "ret_10": 0.003*(i%5 - 2),
                "rsi_14": 50 + (i % 14),
                "rolling_vol_20": 0.01 + (i % 10)*0.0005,
                "ma_20": 1.0 + i*0.0001,
                "ma_50": 1.1 + i*0.0001,
            })
        return rows
    monkeypatch.setattr(TrainingService, "load_recent_features", fake_load_recent_features)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/training/run", params={"mode": "baseline", "limit": 300, "store": True, "cv_splits": 3}, headers={"X-API-Key": API_KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"
        # CV report should exist
        metrics = data.get("metrics") or {}
        cv_report = metrics.get("cv_report")
        assert isinstance(cv_report, dict), "cv_report missing"
        # Promotion block exists
        promotion = data.get("promotion")
        assert promotion is not None, "promotion info missing"
        assert "promoted" in promotion, "promotion.promoted missing"
        # If not promoted, should have a reason
        if not promotion.get("promoted"):
            assert "reason" in promotion, "missing reason for not promoted"
        # Ensure model_id present when stored
        assert data.get("model_id") is not None

@pytest.mark.asyncio
async def test_training_run_promotion_calibration_block(monkeypatch):
    """Brier/ECE 악화로 승격 차단되는 케이스를 모킹하여 multi-metric gate 검증."""
    registry_rows = []

    async def fake_register(self, name: str, version: str, model_type: str, status: str = "staging", artifact_path: Optional[str] = None, metrics=None):  # type: ignore
        rid = len(registry_rows) + 1
        registry_rows.append({
            "id": rid,
            "name": name,
            "version": version,
            "model_type": model_type,
            "status": status,
            "artifact_path": artifact_path,
            "metrics": metrics or {},
        })
        return rid

    async def fake_fetch_latest(self, name: str, model_type: str, limit: int = 5):  # type: ignore
        rows = [r for r in registry_rows if r["name"] == name and r["model_type"] == model_type]
        return list(reversed(rows))[:limit]

    async def fake_promote(self, model_id: int):  # type: ignore
        # Should not be called if calibration gate blocks
        for r in registry_rows:
            if r["id"] == model_id:
                r["status"] = "production"
                return r
        return None

    from backend.apps.training.training_service import TrainingService
    async def fake_load_recent_features(self, limit: int = 1000):  # type: ignore
        rows = []
        for i in range(250):
            rows.append({
                "open_time": i*60000,
                "close_time": i*60000 + 59000,
                "ret_1": 0.0005*(i%5 - 2),
                "ret_5": 0.001*(i%5 - 2),
                "ret_10": 0.0015*(i%5 - 2),
                "rsi_14": 48 + (i % 14),
                "rolling_vol_20": 0.012 + (i % 10)*0.0004,
                "ma_20": 1.0 + i*0.00008,
                "ma_50": 1.1 + i*0.00008,
            })
        return rows

    monkeypatch.setattr(ModelRegistryRepository, "register", fake_register)
    monkeypatch.setattr(ModelRegistryRepository, "fetch_latest", fake_fetch_latest)
    monkeypatch.setattr(ModelRegistryRepository, "promote", fake_promote)
    monkeypatch.setattr(TrainingService, "load_recent_features", fake_load_recent_features)

    # Seed production row with better calibration (lower brier/ece)
    registry_rows.append({
        "id": 1,
        "name": "baseline_predictor",
        "version": "p1",
        "model_type": "supervised",
        "status": "production",
        "artifact_path": None,
        "metrics": {"samples": 400, "auc": 0.58, "brier": 0.200, "ece": 0.040},
    })

    # 환경 변수로 허용 열화 한계 낮게 설정 (강하게 차단)
    os.environ["PROMOTION_MAX_BRIER_DEGRADATION"] = "0.0001"
    os.environ["PROMOTION_MAX_ECE_DEGRADATION"] = "0.0001"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/training/run", params={"mode": "baseline", "limit": 250, "store": True, "cv_splits": 3}, headers={"X-API-Key": API_KEY})
        assert resp.status_code == 200
        data = resp.json()
        promotion = data.get("promotion")
        assert promotion is not None
        # Calibration gate로 인한 차단 사유 중 하나여야 함
        assert promotion.get("promoted") is False
        assert promotion.get("reason") in {"brier_degradation_too_large", "ece_degradation_too_large", "brier_worse_blocked", "ece_worse_blocked", "insufficient_auc_improvement"}

    # 환경 변수 정리
    os.environ.pop("PROMOTION_MAX_BRIER_DEGRADATION", None)
    os.environ.pop("PROMOTION_MAX_ECE_DEGRADATION", None)
