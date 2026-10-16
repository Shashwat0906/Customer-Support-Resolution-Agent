"""Reviewer approval endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, require_reviewer
from app.db.models import Approval
from app.db.session import get_db
from app.schemas.api import DecisionRequest
from app.schemas.errors import NotFound
from app.services import approvals

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", summary="Approvals visible to the authenticated reviewer")
def list_approvals(
    principal: Principal = Depends(require_reviewer),
    db: Session = Depends(get_db),
    status: Literal["pending", "decided", "all"] = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    stmt = (
        select(Approval).where(~Approval.id.startswith("apr_hist_")).order_by(Approval.created_at.desc()).limit(limit)
    )
    if status == "pending":
        stmt = stmt.where(Approval.decision == "pending", Approval.expires_at > datetime.now(UTC))
    elif status == "decided":
        stmt = stmt.where(Approval.decision != "pending")
    views = []
    for a in db.scalars(stmt).all():
        view = approvals.approval_view(db, a)
        payload = view["proposal"]["payload"]
        view["can_approve"] = (
            a.decision == "pending"
            and principal.approval_limit_minor >= payload["amount_minor"]
            and (not payload.get("requires_senior_review") or principal.role == "senior_reviewer")
        )
        views.append(view)
    return views


@router.get("/{approval_id}", summary="Approval details with evidence and citations")
def get_approval(
    approval_id: str, principal: Principal = Depends(require_reviewer), db: Session = Depends(get_db)
) -> dict[str, Any]:
    approval = db.get(Approval, approval_id)
    if approval is None:
        raise NotFound("APPROVAL_NOT_FOUND", "Approval not found.")
    return approvals.approval_view(db, approval)


@router.post("/{approval_id}/decision", summary="Approve or reject a proposal (reviewer identity from token)")
def decide(approval_id: str, body: DecisionRequest, principal: Principal = Depends(require_reviewer)) -> dict[str, Any]:
    return approvals.decide(approval_id, principal, body.decision, body.note)
