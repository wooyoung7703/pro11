from __future__ import annotations
import json
import os
import time
from typing import Any, Dict
import pickle
import base64
import hashlib
from prometheus_client import Counter

# Metrics (lazy global initialization, safe if imported multiple times)
ARTIFACT_CHECKSUM_MISMATCH = Counter("artifact_checksum_mismatch_total", "Count of artifact checksum mismatches detected")
ARTIFACT_CHECKSUM_MISSING = Counter("artifact_checksum_missing_total", "Count of artifacts missing expected checksum field")


class LocalModelStorage:
    """Lightweight local filesystem artifact storage.
    Stores model metadata + weights (if any) as JSON. For now our 'model' is just stats.
    """

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _path(self, name: str, version: str) -> str:
        safe_name = name.replace("/", "_")
        safe_version = version.replace("/", "_")
        return os.path.join(self.base_dir, f"{safe_name}__{safe_version}.json")

    def save(self, name: str, version: str, payload: Dict[str, Any]) -> str:
        """Persist payload.
        If payload contains key 'sk_model', it's pickled & stored as base64 under 'sk_model_b64' then removed (to keep JSON serializable).
        """
        path = self._path(name, version)
        payload = dict(payload)
        payload.setdefault("saved_at", time.time())
        model_bytes: bytes | None = None
        if "sk_model" in payload:
            try:
                model_bytes = pickle.dumps(payload["sk_model"])  # type: ignore
                payload["sk_model_b64"] = base64.b64encode(model_bytes).decode("ascii")
                del payload["sk_model"]
            except Exception as e:  # pragma: no cover
                payload["pickle_error"] = str(e)
        # Compute checksum over serialized model + core metrics if available
        try:
            h = hashlib.sha256()
            if model_bytes:
                h.update(model_bytes)
            metrics_part = payload.get("metrics")
            if metrics_part is not None:
                try:
                    h.update(json.dumps(metrics_part, sort_keys=True).encode("utf-8"))
                except Exception:
                    pass
            payload["checksum_sha256"] = h.hexdigest()
        except Exception as e:  # pragma: no cover
            payload["checksum_error"] = str(e)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path

    def load(self, name: str, version: str, materialize: bool = True) -> Dict[str, Any] | None:
        path = self._path(name, version)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Verify checksum first
        try:
            stored_checksum = data.get("checksum_sha256")
            if not stored_checksum:
                ARTIFACT_CHECKSUM_MISSING.inc()
            else:
                h = hashlib.sha256()
                raw_model_bytes: bytes | None = None
                if "sk_model_b64" in data:
                    try:
                        raw_model_bytes = base64.b64decode(data["sk_model_b64"])
                        h.update(raw_model_bytes)
                    except Exception:
                        pass
                metrics_part = data.get("metrics")
                if metrics_part is not None:
                    try:
                        h.update(json.dumps(metrics_part, sort_keys=True).encode("utf-8"))
                    except Exception:
                        pass
                if h.hexdigest() != stored_checksum:
                    data["checksum_mismatch"] = True
                    ARTIFACT_CHECKSUM_MISMATCH.inc()
                else:
                    data["checksum_mismatch"] = False
        except Exception:
            pass
        if materialize and "sk_model_b64" in data and "sk_model" not in data:
            try:
                raw = base64.b64decode(data["sk_model_b64"])
                data["sk_model"] = pickle.loads(raw)
            except Exception as e:  # pragma: no cover
                data["unpickle_error"] = str(e)
        return data

__all__ = ["LocalModelStorage"]
