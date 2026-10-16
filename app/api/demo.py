"""Demo operations for reviewers: inject mock-service faults to show recovery paths.

Only available when DEMO_AUTH_ENABLED is true. Proxies to the mock services'
admin API so the admin token stays server-side.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends
from sqlalchemy import text

from app.auth.deps import Principal, require_reviewer
from app.config import get_settings
from app.schemas.errors import ExternalServiceError, Forbidden

router = APIRouter(prefix="/demo", tags=["demo"])


def _admin_url() -> str:
    return get_settings().payments_base_url.rstrip("/").rsplit("/", 1)[0] + "/admin"


def _require_demo(principal: Principal = Depends(require_reviewer)) -> Principal:
    if not get_settings().demo_auth_enabled:
        raise Forbidden("DEMO_DISABLED", "Demo operations are disabled.")
    return principal


def _call(method: str, path: str, json: dict[str, Any] | None = None) -> Any:
    try:
        r = httpx.request(
            method,
            f"{_admin_url()}{path}",
            json=json,
            timeout=5,
            headers={"X-Admin-Token": get_settings().mock_admin_token},
        )
    except httpx.HTTPError as exc:
        raise ExternalServiceError("mock_admin", "unavailable", "Mock services are unreachable.") from exc
    if r.status_code >= 400:
        raise ExternalServiceError(
            "mock_admin",
            "client_error",
            f"Mock admin returned {r.status_code}",
            http_status=r.status_code,
            body=r.json(),
        )
    return r.json()


@router.get("/faults", summary="List active mock-service faults")
def list_faults(_: Principal = Depends(_require_demo)) -> Any:
    return _call("GET", "/faults")


@router.post("/faults", status_code=201, summary="Inject a mock-service fault")
def add_fault(
    rule: dict[str, Any] = Body(
        examples=[{"target": "payments.create_refund", "mode": "commit_then_timeout", "count": 1, "delay_ms": 5000}]
    ),
    _: Principal = Depends(_require_demo),
) -> Any:
    return _call("POST", "/faults", rule)


@router.delete("/faults", summary="Clear all mock-service faults")
def clear_faults(_: Principal = Depends(_require_demo)) -> Any:
    return _call("DELETE", "/faults")


@router.get("/provider-refunds", summary="Refunds recorded by the mock payment provider")
def provider_refunds(_: Principal = Depends(_require_demo)) -> Any:
    return _call("GET", "/refunds")


@router.post("/reset", summary="Reset demo data and mock services (senior reviewer, demo mode only)")
def reset_demo(principal: Principal = Depends(_require_demo)) -> dict[str, Any]:
    if principal.role != "senior_reviewer":
        raise Forbidden("SENIOR_REVIEWER_ONLY", "Only a senior reviewer can reset the demo.")
    from app.db.session import session_scope
    from scripts.ingest_policies import ingest
    from scripts.seed_data import reset, seed

    ingest(prune=True)
    with session_scope() as session:
        session.execute(text("SET LOCAL lock_timeout = '10s'"))  # fail clearly rather than wait forever
        reset(session)
        anchor = seed(session)
    _call("POST", "/reset", {"anchor": anchor.isoformat()})
    return {"status": "reset", "anchor": anchor.isoformat()}
