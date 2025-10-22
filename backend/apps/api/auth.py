from __future__ import annotations
import os
from typing import Optional
from fastapi import Request, Header, HTTPException, status

API_KEY_HEADER = "X-API-Key"
API_KEY = os.getenv("API_KEY", "dev-key")

async def require_api_key(request: Request, x_api_key: Optional[str] = Header(default=None)):
    """API Key authentication for protected endpoints.

    Dev conveniences:
    - DISABLE_API_KEY=1 disables auth entirely (local/dev only).
    - APP_ENV in {local, dev, development} bypasses auth (local/dev only).
    - Docs endpoints (/docs, /redoc, /openapi.json) and their referers are allowed.
    - ALLOW_DOCS_NOAUTH=1 keeps back-compat for doc access.
    - ALLOW_ADMIN_NOAUTH=1 allows /admin/* without key (dev only).
    - ALLOW_PUBLIC_STATUS=1 allows public /api/* reads (dev default; set to 0 for prod).
    - ALLOW_API_KEY_QUERY=1 allows ?x_api_key=... or ?apikey=... in query.
    """
    # Full disable flag
    if os.getenv("DISABLE_API_KEY", "0").lower() in {"1", "true", "yes", "on"}:
        return True
    # Local/dev bypass
    app_env = os.getenv("APP_ENV", "").lower()
    if app_env in {"local", "dev", "development"}:
        return True

    current_key = getattr(request.app.state, "current_api_key", API_KEY)  # type: ignore[attr-defined]

    # Docs and schema always allowed
    path = request.url.path
    if path.startswith("/docs") or path.startswith("/redoc") or path == "/openapi.json":
        return True
    # Referer-based doc fetches
    ref = request.headers.get("referer", "")
    if "/docs" in ref or "/redoc" in ref:
        return True
    # Back-compat flag for docs
    if os.getenv("ALLOW_DOCS_NOAUTH", "0") in {"1", "true", "TRUE", "on", "yes"}:
        return True
    # Optional: allow /admin without auth (dev only)
    try:
        allow_admin = os.getenv("ALLOW_ADMIN_NOAUTH", "0").lower() in {"1", "true", "yes", "on"}
    except Exception:
        allow_admin = False
    if allow_admin and path.startswith("/admin/"):
        return True
    # Optional: allow public status/summary endpoints
    try:
        allow_public = os.getenv("ALLOW_PUBLIC_STATUS", "1").lower() in {"1", "true", "yes", "on"}
    except Exception:
        allow_public = False
    if allow_public and path.startswith("/api/"):
        return True

    # Optional: query-based API key
    try:
        allow_q = os.getenv("ALLOW_API_KEY_QUERY", "0").lower() in {"1", "true", "yes", "on"}
    except Exception:
        allow_q = False
    if allow_q and x_api_key is None:
        try:
            qp = request.query_params
            qkey = qp.get("x_api_key") or qp.get("apikey")
            if qkey and qkey == current_key:
                return True
        except Exception:
            pass

    # Default check
    if x_api_key != current_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    return True
