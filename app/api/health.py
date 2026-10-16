"""Liveness and readiness."""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import text

from app.config import get_settings
from app.db.session import get_engine
from app.schemas.api import HealthOut
from app.services.external import check_mock_services

router = APIRouter(tags=["health"])
VERSION = "0.1.0"


def _db_check() -> dict[str, str]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            seeded = conn.execute(text("SELECT count(*) FROM customers")).scalar()
            policies = conn.execute(text("SELECT count(*) FROM policy_chunks")).scalar()
        return {"database": "ok", "migration": str(rev), "customers": str(seeded), "policy_chunks": str(policies)}
    except Exception as exc:  # report, never raise from a health check
        return {"database": f"error: {type(exc).__name__}"}


@router.get("/health", response_model=HealthOut, summary="Health check (database connectivity included)")
def health(response: Response) -> HealthOut:
    checks: dict[str, object] = dict(_db_check())
    settings = get_settings()
    checks["llm_provider"] = settings.llm_provider
    if settings.llm_fallback_reason:
        checks["llm_note"] = settings.llm_fallback_reason
    if checks["database"] != "ok":
        response.status_code = 503
        return HealthOut(status="unavailable", checks=checks, version=VERSION)
    return HealthOut(status="ok", checks=checks, version=VERSION)


@router.get("/health/ready", response_model=HealthOut, summary="Readiness: database, data and external services")
def ready(response: Response) -> HealthOut:
    checks: dict[str, object] = dict(_db_check())
    services = check_mock_services()
    checks.update({f"service_{k}": v for k, v in services.items()})
    ok = (
        checks.get("database") == "ok"
        and all(v == "ok" for v in services.values())
        and checks.get("customers", "0") != "0"
        and checks.get("policy_chunks", "0") != "0"
    )
    if not ok:
        response.status_code = 503
    return HealthOut(status="ok" if ok else "degraded", checks=checks, version=VERSION)
