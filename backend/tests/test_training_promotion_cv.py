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

    # Pre-seed a production model with moderate metrics for comparison (bottom predictor)
    registry_rows.append({
        "id": 1,
        "name": "bottom_predictor",
        "version": "1111",
        "model_type": "supervised",
        "status": "production",
        "artifact_path": None,
        "metrics": {"samples": 500, "auc": 0.55, "brier": 0.25, "ece": 0.06},
    })

    # Monkeypatch bottom training to return a successful result with cv_report
    from backend.apps.training.training_service import TrainingService
    async def fake_run_training_bottom(self, limit: int = 300, lookahead: int = 30, drawdown: float = 0.005, rebound: float = 0.003, **kwargs):  # type: ignore
        return {
            "status": "ok",
            "model_id": len(registry_rows) + 1,
            "version": "vtest1",
            "artifact_path": "/tmp/bottom_predictor__vtest1.json",
            "metrics": {
                "samples": 240,
                "val_samples": 60,
                "auc": 0.62,
                "accuracy": 0.58,
                "brier": 0.22,
                "ece": 0.04,
                "cv_report": {
                    "folds": [
                        {"fold":1, "train_size": 160, "val_size": 40, "auc": 0.60, "accuracy": 0.56, "brier": 0.23},
                        {"fold":2, "train_size": 200, "val_size": 40, "auc": 0.63, "accuracy": 0.59, "brier": 0.22},
                    ],
                    "auc": {"mean": 0.615, "std": 0.02},
                    "accuracy": {"mean": 0.575, "std": 0.02},
                    "brier": {"mean": 0.225, "std": 0.01},
                    "splits_used": 2,
                    "requested_splits": 3,
                    "time_based": True,
                },
            },
        }
    monkeypatch.setattr(TrainingService, "run_training_bottom", fake_run_training_bottom)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/training/run", params={"limit": 300, "store": True, "cv_splits": 3}, headers={"X-API-Key": API_KEY})
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

    # Monkeypatch bottom training to produce worse calibration relative to prod to trigger block
    from backend.apps.training.training_service import TrainingService
    async def fake_run_training_bottom_bad(self, limit: int = 250, **kwargs):  # type: ignore
        return {
            "status": "ok",
            "model_id": len(registry_rows) + 1,
            "version": "vtest2",
            "artifact_path": "/tmp/bottom_predictor__vtest2.json",
            "metrics": {"samples": 200, "auc": 0.59, "brier": 0.205, "ece": 0.041},
        }

    monkeypatch.setattr(ModelRegistryRepository, "register", fake_register)
    monkeypatch.setattr(ModelRegistryRepository, "fetch_latest", fake_fetch_latest)
    monkeypatch.setattr(ModelRegistryRepository, "promote", fake_promote)
    monkeypatch.setattr(TrainingService, "run_training_bottom", fake_run_training_bottom_bad)

    # Seed production row with better calibration (lower brier/ece) for bottom predictor
    registry_rows.append({
        "id": 1,
        "name": "bottom_predictor",
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
        resp = await ac.post("/api/training/run", params={"limit": 250, "store": True, "cv_splits": 3}, headers={"X-API-Key": API_KEY})
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
