"""Authentication and authorization dependencies.

Identity is derived only from the verified token. Request bodies never carry
customer or reviewer identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.tokens import verify_token
from app.db.models import Customer, StaffUser
from app.db.session import get_db
from app.schemas.errors import Forbidden, Unauthorized


@dataclass(frozen=True)
class Principal:
    kind: Literal["customer", "staff"]
    id: str
    subject: str
    name: str
    role: str  # "customer" | "reviewer" | "senior_reviewer"
    approval_limit_minor: int = 0

    @property
    def is_reviewer(self) -> bool:
        return self.kind == "staff" and self.role in ("reviewer", "senior_reviewer")

    @property
    def actor(self) -> str:
        return f"{'customer' if self.kind == 'customer' else 'reviewer'}:{self.id}"


def _bearer(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("AUTH_REQUIRED", "A bearer token is required.")
    return token


def get_principal(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> Principal:
    claims = verify_token(_bearer(authorization))
    subject = claims["sub"]
    if claims.get("kind") == "staff":
        staff = db.scalar(select(StaffUser).where(StaffUser.external_auth_subject == subject))
        if staff is None or not staff.active:
            raise Unauthorized("UNKNOWN_PRINCIPAL", "The authenticated user is not recognised.")
        principal = Principal("staff", staff.id, subject, staff.name, staff.role, staff.approval_limit_minor)
        db.commit()  # end the read transaction: identity lookups must not hold locks for the whole request
        return principal
    customer = db.scalar(select(Customer).where(Customer.external_auth_subject == subject))
    if customer is None:
        raise Unauthorized("UNKNOWN_PRINCIPAL", "The authenticated user is not recognised.")
    principal = Principal("customer", customer.id, subject, customer.name, "customer")
    db.commit()  # end the read transaction: identity lookups must not hold locks for the whole request
    return principal


def require_customer(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.kind != "customer":
        raise Forbidden("CUSTOMER_ONLY", "This action is available to customers only.")
    return principal


def require_reviewer(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_reviewer:
        raise Forbidden("REVIEWER_ONLY", "This action requires a support reviewer.")
    return principal
