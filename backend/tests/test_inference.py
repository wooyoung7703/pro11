import pytest
import asyncio
from backend.apps.training.training_service import TrainingService

class DummyModel:
    def __init__(self, prob=0.7):
        self._prob = prob
    def predict_proba(self, X):  # type: ignore
        import numpy as np
        return np.array([[1-self._prob, self._prob] for _ in range(len(X))])

@pytest.mark.asyncio
async def test_predict_no_data(monkeypatch):
    svc = TrainingService("XRPUSDT","1m","artifacts/models")
    async def _load_recent_features(self, limit=2):
        return []
    monkeypatch.setattr(TrainingService, "load_recent_features", _load_recent_features)
    out = await svc.predict_latest()
    assert out["status"] == "no_data"

@pytest.mark.asyncio
async def test_predict_insufficient_features(monkeypatch):
    svc = TrainingService("XRPUSDT","1m","artifacts/models")
    async def _load_recent_features(self, limit=2):
        return [{"ret_1": None}]
    monkeypatch.setattr(TrainingService, "load_recent_features", _load_recent_features)
    out = await svc.predict_latest()
    assert out["status"] in ("no_data","insufficient_features")

@pytest.mark.asyncio
async def test_predict_success(monkeypatch, tmp_path):
    svc = TrainingService("XRPUSDT","1m", str(tmp_path))
    # Provide recent features
    feat_row = {"ret_1":0.01,"ret_5":0.02,"ret_10":0.03,"rsi_14":55.0,"rolling_vol_20":0.1,"ma_20":0.5,"ma_50":0.45}
    async def _load_recent_features(self, limit=2):
        return [feat_row]
    monkeypatch.setattr(TrainingService, "load_recent_features", _load_recent_features)
    # Mock registry fetch to supply a production model
    from types import SimpleNamespace
    class FakeRecord(dict):
        def __getattr__(self, k):
            return self[k]
    async def _fetch_latest(self, name, model_type, limit):
        import json, base64, pickle, os
        version = "123"
        artifact_path = tmp_path / f"baseline_predictor__{version}.json"
        model = DummyModel(prob=0.8)
        b = pickle.dumps(model)
        payload = {"sk_model_b64": base64.b64encode(b).decode("ascii")}
        with open(artifact_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        rec = FakeRecord({"id":1, "name":"baseline_predictor","version":version,"model_type":"supervised","status":"production","artifact_path": str(artifact_path), "metrics": {"samples":800, "auc":0.6}})
        return [rec]
    from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository
    monkeypatch.setattr(ModelRegistryRepository, "fetch_latest", _fetch_latest)
    out = await svc.predict_latest()
    assert out["status"] == "ok"
    assert 0 <= out["probability"] <= 1
    assert out["decision"] in (1,-1)
