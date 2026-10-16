"""Write tools. Called only by deterministic workflow nodes, never offered to an agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services import proposals, refunds, tickets
from app.services.decisions import Basis
from app.services.eligibility import DamageReport


def create_refund_proposal(
    ctx: Any,
    order_id: str,
    amount_minor: int,
    reason: str,
    *,
    basis: Basis = "return",
    damage_report: DamageReport | None = None,
    reported_at: datetime | None = None,
    policy_references: list[dict[str, Any]] | None = None,
    evidence_references: list[str] | None = None,
) -> dict[str, Any]:
    """Validate and persist an immutable proposal + pending approval. Never issues a refund."""
    return dict(
        proposals.create_refund_proposal(
            run_id=ctx.run_id,
            customer_id=ctx.customer_id,
            order_id=order_id,
            amount_minor=amount_minor,
            reason=reason,
            basis=basis,
            damage_report=damage_report,
            reported_at=reported_at,  # type: ignore[arg-type]
            policy_references=policy_references,
            evidence_references=evidence_references,
        )
    )


def execute_approved_refund(ctx: Any, proposal_id: str) -> refunds.RefundOutcome:
    """Execute exactly the stored, approved proposal (approval verified inside the service)."""
    return refunds.execute_approved_refund(proposal_id)


def update_ticket(
    ctx: Any,
    ticket_id: str,
    status: str,
    note: str,
    *,
    idempotency_key: str,
    category: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    return tickets.update_ticket(
        ticket_id,
        status=status,
        note=note,
        idempotency_key=idempotency_key,
        run_id=ctx.run_id,
        category=category,
        order_id=order_id,
    )
