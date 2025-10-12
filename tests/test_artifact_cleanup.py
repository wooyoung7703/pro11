import asyncio
import os
from pathlib import Path
import json
import pytest

from backend.apps.model_registry.service.artifact_cleanup import ArtifactCleanupService
from backend.apps.model_registry.repository.registry_repository import ModelRegistryRepository

@pytest.mark.asyncio
async def test_artifact_cleanup_keeps_production_and_latest_staging(monkeypatch, tmp_path):
    base = tmp_path / "models"
    base.mkdir()
    # Prepare fake registry data
    # Model name: baseline_predictor; versions: v1(prod), v2(staging), v3(staging), v4(staging), v5(staging), v6(staging), v7(staging older extra)
    # Keep staging = 3 (override)
    versions = ["v1","v2","v3","v4","v5","v6","v7"]
    prod_version = "v1"
    # Create artifact files for all versions
    for v in versions:
        (base / f"baseline_predictor__{v}.json").write_text(json.dumps({"version": v}))
    # Fake registry fetch_latest returning rows ordered newest first (simulate typical ordering)
    rows = []
    # newest staging first downwards, production included
    ordering = ["v6","v5","v4","v3","v2","v1"]  # v7 absent to simulate orphan disk artifact
    for ver in ordering:
        row = {"version": ver, "status": "production" if ver == prod_version else "staging", "metrics": {"auc": 0.5}}
        rows.append(row)
    async def fake_fetch_latest(name, model_type, limit):  # type: ignore
        assert name == "baseline_predictor"
        return rows[:limit]
    monkeypatch.setattr(ModelRegistryRepository, "fetch_latest", fake_fetch_latest)  # type: ignore

    svc = ArtifactCleanupService(str(base), keep_staging=3)
    result = await svc.run_once()
    assert result["status"] == "ok"
    # Determine kept versions: production (v1) + first 3 staging from ordering (v6,v5,v4)
    expected_keep = {"v1","v6","v5","v4"}
    remaining_files = {p.name.split("__",1)[1].rsplit('.json',1)[0] for p in base.glob('*.json')}
    assert expected_keep == remaining_files, f"Expected {expected_keep} got {remaining_files}"
    # Removed should include v2,v3,v7 (v7 orphan)
    removed_versions = {Path(r).name.split('__',1)[1].rsplit('.json',1)[0] for r in result["removed"]}
    assert {"v2","v3","v7"} == removed_versions
    # Metrics coherence
    assert result["removed_count"] == 3
    assert result["kept_count"] == 4

@pytest.mark.asyncio
async def test_artifact_cleanup_no_directory():
    svc = ArtifactCleanupService("nonexistent_dir_abc123", keep_staging=2)
    result = await svc.run_once()
    assert result["status"] == "no_dir"
