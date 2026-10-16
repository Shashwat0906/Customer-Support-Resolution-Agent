"""Demo authentication: issue tokens for synthetic personas."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, get_principal
from app.auth.tokens import issue_token
from app.config import get_settings
from app.db.models import Customer, StaffUser
from app.db.session import get_db
from app.schemas.api import DemoLoginRequest, TokenResponse
from app.schemas.errors import Forbidden, Unauthorized

router = APIRouter(prefix="/auth", tags=["auth"])


def _require_demo() -> None:
    if not get_settings().demo_auth_enabled:
        raise Forbidden("DEMO_AUTH_DISABLED", "Demo login is disabled; use the configured identity provider.")


@router.get("/demo-users", summary="List synthetic personas available for demo login")
def demo_users(db: Session = Depends(get_db)) -> dict[str, list[dict[str, Any]]]:
    _require_demo()
    customers = db.scalars(select(Customer).order_by(Customer.id)).all()
    staff = db.scalars(select(StaffUser).where(StaffUser.active).order_by(StaffUser.id)).all()
    return {
        "customers": [{"id": c.id, "name": c.name, "subject": c.external_auth_subject} for c in customers],
        "reviewers": [
            {
                "id": s.id,
                "name": s.name,
                "role": s.role,
                "subject": s.external_auth_subject,
                "approval_limit_minor": s.approval_limit_minor,
            }
            for s in staff
        ],
    }


@router.post("/demo-login", response_model=TokenResponse, summary="Issue a token for a synthetic persona")
def demo_login(body: DemoLoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    _require_demo()
    customer = db.scalar(select(Customer).where(Customer.external_auth_subject == body.subject))
    if customer is not None:
        token, exp = issue_token(customer.external_auth_subject, "customer")
        return TokenResponse(
            access_token=token,
            expires_at=exp,
            principal={"kind": "customer", "id": customer.id, "name": customer.name, "role": "customer"},
        )
    staff = db.scalar(select(StaffUser).where(StaffUser.external_auth_subject == body.subject, StaffUser.active))
    if staff is not None:
        token, exp = issue_token(staff.external_auth_subject, "staff")
        return TokenResponse(
            access_token=token,
            expires_at=exp,
            principal={
                "kind": "staff",
                "id": staff.id,
                "name": staff.name,
                "role": staff.role,
                "approval_limit_minor": staff.approval_limit_minor,
            },
        )
    raise Unauthorized("UNKNOWN_PRINCIPAL", "Unknown demo persona.")


@router.get("/me", summary="The authenticated principal")
def me(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return {
        "kind": principal.kind,
        "id": principal.id,
        "name": principal.name,
        "role": principal.role,
        "approval_limit_minor": principal.approval_limit_minor,
    }
