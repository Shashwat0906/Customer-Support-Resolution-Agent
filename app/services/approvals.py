"""Reviewer approvals: authenticated, permission-checked, bound to a proposal hash."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal
from app.db.models import ActionProposal, Approval, StaffUser, Ticket, WorkflowRun
from app.db.session import session_scope
from app.schemas.errors import AppError, Conflict, Forbidden, NotFound
from app.services import audit, jobs
from app.services.hashing import payload_hash
from app.workflows.transitions import transition


class ApprovalExpired(AppError):
    status_code = 410


def check_reviewer_may_approve(reviewer: Principal | StaffUser, payload: dict[str, Any]) -> None:
    role = reviewer.role
    limit = reviewer.approval_limit_minor
    if role not in ("reviewer", "senior_reviewer"):
        raise Forbidden("REVIEWER_ONLY", "This action requires a support reviewer.")
    if payload.get("requires_senior_review") and role != "senior_reviewer":
        raise Forbidden("SENIOR_REVIEW_REQUIRED", "This refund requires a senior reviewer.")
    if int(payload["amount_minor"]) > limit:
        raise Forbidden(
            "APPROVAL_LIMIT_EXCEEDED",
            "The refund amount exceeds your approval limit.",
            details={"approval_limit_minor": limit},
        )


def _mark_expired(session: Session, approval: Approval, proposal: ActionProposal, now: datetime) -> None:
    approval.decision = "expired"
    approval.decided_at = now
    proposal.status = "expired"
    run = session.get(WorkflowRun, proposal.run_id, with_for_update=True)
    audit.record(
        session,
        run_id=proposal.run_id,
        ticket_id=run.ticket_id if run else None,
        actor="system",
        event_type="approval_expired",
        payload={"approval_id": approval.id, "proposal_id": proposal.id},
    )
    if run is not None and run.status == "awaiting_approval":
        transition(session, run, "queued")
        jobs.enqueue(session, run.id, "resume", {"approval_id": approval.id, "event": "approval_expired"})


def decide(
    approval_id: str,
    reviewer: Principal,
    decision: Literal["approve", "reject"],
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    expired = False
    with session_scope() as session:
        approval = session.get(Approval, approval_id, with_for_update=True)
        if approval is None:
            raise NotFound("APPROVAL_NOT_FOUND", "Approval not found.")
        proposal = session.get(ActionProposal, approval.proposal_id, with_for_update=True)
        assert proposal is not None
        if approval.decision != "pending":
            raise Conflict(
                "APPROVAL_ALREADY_DECIDED",
                f"This approval is already {approval.decision}.",
                details={"decision": approval.decision},
            )
        if now >= approval.expires_at:
            _mark_expired(session, approval, proposal, now)
            expired = True
        else:
            # Integrity: the reviewer decides on exactly the stored, hashed proposal.
            if (
                payload_hash(proposal.payload) != proposal.payload_hash
                or proposal.payload_hash != approval.proposal_payload_hash
            ):
                raise Conflict("PROPOSAL_INTEGRITY_ERROR", "The proposal does not match the approval request.")
            if decision == "approve":
                check_reviewer_may_approve(reviewer, proposal.payload)
            approval.decision = "approved" if decision == "approve" else "rejected"
            approval.reviewer_id = reviewer.id
            approval.decided_at = now
            approval.note = (note or "").strip()[:2000] or None
            proposal.status = "approved" if decision == "approve" else "rejected"
            run = session.get(WorkflowRun, proposal.run_id, with_for_update=True)
            assert run is not None
            audit.record(
                session,
                run_id=run.id,
                ticket_id=run.ticket_id,
                actor=reviewer.actor,
                event_type=f"approval_{approval.decision}",
                payload={
                    "approval_id": approval.id,
                    "proposal_id": proposal.id,
                    "amount_minor": proposal.payload["amount_minor"],
                    "note": approval.note,
                    "payload_hash": approval.proposal_payload_hash,
                },
            )
            if run.status == "awaiting_approval":
                transition(session, run, "queued")
                jobs.enqueue(session, run.id, "resume", {"approval_id": approval.id})
        result = {
            "approval_id": approval.id,
            "decision": approval.decision,
            "proposal_id": proposal.id,
            "run_id": proposal.run_id,
            "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        }
    if expired:
        raise ApprovalExpired(
            "APPROVAL_EXPIRED", "This approval request has expired; a new approval is required.", details=result
        )
    return result


def expire_due(now: datetime | None = None) -> list[str]:
    """Sweeper: expire pending approvals past their deadline and resume their runs."""
    now = now or datetime.now(UTC)
    with session_scope() as session:
        due = session.scalars(
            select(Approval)
            .where(Approval.decision == "pending", Approval.expires_at <= now)
            .with_for_update(skip_locked=True)
        ).all()
        for approval in due:
            proposal = session.get(ActionProposal, approval.proposal_id, with_for_update=True)
            assert proposal is not None
            _mark_expired(session, approval, proposal, now)
        return [a.id for a in due]


def approval_view(session: Session, approval: Approval) -> dict[str, Any]:
    proposal = session.get(ActionProposal, approval.proposal_id)
    assert proposal is not None
    run = session.get(WorkflowRun, proposal.run_id)
    ticket = session.get(Ticket, run.ticket_id) if run else None
    reviewer = session.get(StaffUser, approval.reviewer_id) if approval.reviewer_id else None
    snapshot = run.state_snapshot if run else {}
    return {
        "approval_id": approval.id,
        "decision": approval.decision,
        "expires_at": approval.expires_at.isoformat(),
        "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
        "reviewer": {"id": reviewer.id, "name": reviewer.name} if reviewer else None,
        "note": approval.note,
        "proposal": {
            "proposal_id": proposal.id,
            "status": proposal.status,
            "revision": proposal.revision,
            "payload": proposal.payload,
            "payload_hash": proposal.payload_hash,
        },
        "run_id": run.id if run else None,
        "ticket": {
            "ticket_id": ticket.id,
            "summary": ticket.summary,
            "category": ticket.category,
            "status": ticket.status,
        }
        if ticket
        else None,
        "verified_facts": snapshot.get("verified_facts", {}),
        "policy_references": snapshot.get("policy_references", []),
        "investigation_summary": snapshot.get("investigation_summary"),
    }
