"""Refund proposals: validated, immutable, bound to a pending approval.

Creating a proposal never moves money. The requested amount, order and action
are checked against the deterministic eligibility decision; a mismatch is
rejected (the model cannot talk its way into a different amount).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ActionProposal, Approval, Customer, Ticket, WorkflowRun
from app.db.session import session_scope
from app.schemas.errors import Conflict, NotFound, ValidationFailed
from app.services import audit, records
from app.services.decisions import Basis, decide_refund
from app.services.eligibility import DamageReport
from app.services.hashing import payload_hash


class ProposalResult(dict[str, Any]):
    """{'proposal_id', 'approval_id', 'payload', 'payload_hash', 'expires_at'}"""


def _owned_run(session: Session, run_id: str, customer_id: str) -> WorkflowRun:
    run = session.get(WorkflowRun, run_id)
    if run is None:
        raise NotFound("RUN_NOT_FOUND", "Workflow run not found.")
    ticket = session.get(Ticket, run.ticket_id)
    if ticket is None or ticket.customer_id != customer_id:
        raise NotFound("RUN_NOT_FOUND", "Workflow run not found.")
    return run


def create_refund_proposal(
    *,
    run_id: str,
    customer_id: str,
    order_id: str,
    amount_minor: int,
    reason: str,
    basis: Basis = "return",
    damage_report: DamageReport | None = None,
    reported_at: datetime | None = None,
    policy_references: list[dict[str, Any]] | None = None,
    evidence_references: list[str] | None = None,
    missing_information: list[str] | None = None,
    now: datetime | None = None,
) -> ProposalResult:
    now = now or datetime.now(UTC)
    settings = get_settings()
    if amount_minor <= 0:
        raise ValidationFailed("INVALID_AMOUNT", "Refund amount must be positive.")
    if missing_information:
        raise ValidationFailed(
            "MISSING_INFORMATION",
            "A proposal cannot be created while information is missing.",
            details={"missing_information": missing_information},
        )

    # 1. Facts: DB reads in a short session, provider read without an open transaction.
    provider_view = records.get_payment_status(customer_id, order_id)

    with session_scope() as session:
        run = _owned_run(session, run_id, customer_id)
        decision, facts = decide_refund(
            session,
            customer_id,
            order_id,
            basis=basis,
            provider_view=provider_view,
            now=now,
            damage_report=damage_report,
            reported_at=reported_at,
            lock=True,
        )
        if decision.disposition != "eligible":
            raise ValidationFailed(
                "NOT_ELIGIBLE",
                f"Refund is not permitted: {decision.explanation}",
                details={"reason_code": decision.reason_code},
            )
        if amount_minor != decision.amount_minor:
            # Written in its own transaction: this one is about to roll back.
            audit.record_now(
                run_id=run_id,
                ticket_id=run.ticket_id,
                actor="system",
                event_type="proposal_amount_rejected",
                payload={"requested_minor": amount_minor, "permitted_minor": decision.amount_minor},
            )
            raise ValidationFailed(
                "AMOUNT_MISMATCH",
                f"Requested amount {amount_minor} does not match the permitted amount {decision.amount_minor}.",
                details={"permitted_minor": decision.amount_minor},
            )
        active = session.scalar(
            select(ActionProposal).where(
                ActionProposal.run_id == run_id, ActionProposal.status.in_(("pending_approval", "approved"))
            )
        )
        if active is not None:
            raise Conflict(
                "PROPOSAL_ALREADY_ACTIVE",
                "This run already has an active proposal.",
                details={"proposal_id": active.id},
            )
        revision = (
            session.scalar(select(func.max(ActionProposal.revision)).where(ActionProposal.run_id == run_id)) or 0
        ) + 1
        customer = session.get(Customer, customer_id)
        payload: dict[str, Any] = {
            "action": "refund",
            "order_id": order_id,
            "customer_id": customer_id,
            "customer_name": customer.name if customer else None,
            "ticket_id": run.ticket_id,
            "amount_minor": decision.amount_minor,
            "currency": "INR",
            "reason": reason.strip()[:1000],
            "basis": basis,
            "decision_reason_code": decision.reason_code,
            "policy_document_id": decision.policy_document_id,
            "policy_version": decision.policy_version,
            "requires_senior_review": decision.requires_senior_review,
            "policy_references": policy_references or [],
            "evidence_references": evidence_references or [],
            "missing_information": [],
            "damage_report": damage_report.model_dump() if damage_report else None,
            "reported_at": reported_at.isoformat() if reported_at else None,
            "items": [i.model_dump() for i in facts.items],
            "revision": revision,
        }
        digest = payload_hash(payload)
        proposal = ActionProposal(
            run_id=run_id,
            order_id=order_id,
            action_type="refund",
            revision=revision,
            payload=payload,
            payload_hash=digest,
            status="pending_approval",
        )
        session.add(proposal)
        session.flush()
        expires_at = now + timedelta(minutes=settings.approval_ttl_minutes)
        approval = Approval(
            proposal_id=proposal.id, proposal_payload_hash=digest, decision="pending", expires_at=expires_at
        )
        session.add(approval)
        session.flush()
        audit.record(
            session,
            run_id=run_id,
            ticket_id=run.ticket_id,
            actor="system",
            event_type="proposal_created",
            payload={
                "proposal_id": proposal.id,
                "approval_id": approval.id,
                "amount_minor": payload["amount_minor"],
                "order_id": order_id,
                "payload_hash": digest,
                "revision": revision,
                "requires_senior_review": decision.requires_senior_review,
                "expires_at": expires_at.isoformat(),
            },
        )
        return ProposalResult(
            proposal_id=proposal.id,
            approval_id=approval.id,
            payload=payload,
            payload_hash=digest,
            expires_at=expires_at.isoformat(),
        )
