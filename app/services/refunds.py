"""Safe refund execution and reconciliation.

Lifecycle (see docs/architecture.md §7):

1. TX1 (short): lock proposal + order; verify the stored approval (decision,
   reviewer, expiry, payload hash); re-run eligibility; create the refund
   operation with a stable idempotency key and reserve the amount. COMMIT.
2. Mark ``submitted`` (COMMIT), then call the provider with the same key.
   No transaction is open during the network call.
3. Persist the provider response: ``succeeded`` (settle), ``processing``,
   ``failed`` (release reservation) or ``unknown`` (timeout / 5xx / crash).
4. ``unknown``/``submitted`` operations are reconciled by looking the key up at
   the provider before anything else happens. Only when the provider has no
   record of the key is the request re-sent, with the same key, within budget.

The operation row and the order balance columns are the ledger. Workflow
checkpoints are never consulted here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ActionProposal, Approval, Order, Payment, RefundOperation, StaffUser, WorkflowRun
from app.db.session import session_scope
from app.schemas.errors import AppError, ExternalServiceError
from app.services import audit, records
from app.services.approvals import check_reviewer_may_approve
from app.services.decisions import decide_refund
from app.services.eligibility import DamageReport
from app.services.external import PaymentsClient
from app.services.faults import maybe_crash
from app.services.hashing import payload_hash

log = logging.getLogger(__name__)

TERMINAL = ("succeeded", "failed")


@dataclass
class RefundOutcome:
    status: str  # succeeded | processing | failed | unknown | invalidated | not_authorized
    operation_id: str | None = None
    provider_reference: str | None = None
    amount_minor: int = 0
    reason_code: str | None = None
    message: str = ""
    attempts: int = 0
    details: dict[str, Any] = field(default_factory=dict)


def idempotency_key_for(proposal_id: str) -> str:
    return f"refund-{proposal_id}"


def _outcome(op: RefundOperation, **kw: Any) -> RefundOutcome:
    return RefundOutcome(
        status=op.status,
        operation_id=op.id,
        provider_reference=op.provider_reference,
        amount_minor=op.amount_minor,
        attempts=op.attempts,
        **kw,
    )


def _ticket_id(session: Session, run_id: str) -> str | None:
    run = session.get(WorkflowRun, run_id)
    return run.ticket_id if run else None


# --------------------------------------------------------------------------- authorization


def _latest_approval(session: Session, proposal_id: str) -> Approval | None:
    return session.scalar(
        select(Approval).where(Approval.proposal_id == proposal_id).order_by(Approval.created_at.desc()).limit(1)
    )


def _verify_authorization(
    session: Session, proposal: ActionProposal, approval: Approval | None, now: datetime
) -> str | None:
    """Return a failure code, or None if the stored approval authorizes execution."""
    if approval is None or approval.decision != "approved":
        return "APPROVAL_NOT_GRANTED"
    if approval.reviewer_id is None or approval.decided_at is None:
        return "APPROVAL_INCOMPLETE"
    if approval.decided_at > approval.expires_at or now >= approval.expires_at:
        return "APPROVAL_EXPIRED"
    if not (payload_hash(proposal.payload) == proposal.payload_hash == approval.proposal_payload_hash):
        return "PROPOSAL_HASH_MISMATCH"
    reviewer = session.get(StaffUser, approval.reviewer_id)
    if reviewer is None or not reviewer.active:
        return "REVIEWER_INACTIVE"
    try:
        check_reviewer_may_approve(reviewer, proposal.payload)
    except AppError as exc:
        return exc.code
    if proposal.status not in ("approved",):
        return f"PROPOSAL_{proposal.status.upper()}"
    return None


# --------------------------------------------------------------------------- ledger transitions


def _settle(session: Session, op: RefundOperation, provider: dict[str, Any]) -> None:
    order = session.get(Order, op.order_id, with_for_update=True)
    assert order is not None
    if op.status == "succeeded":
        return
    previously_reserved = op.status in ("pending", "submitted", "processing", "unknown")
    op.status = "succeeded"
    op.provider_reference = provider.get("id") or op.provider_reference
    op.provider_response = provider
    op.completed_at = datetime.now(UTC)
    op.last_error = None
    if previously_reserved:
        order.reserved_refund_minor -= op.amount_minor
    order.refunded_amount_minor += op.amount_minor
    proposal = session.get(ActionProposal, op.proposal_id)
    if proposal is not None:
        proposal.status = "executed"
    audit.record(
        session,
        run_id=proposal.run_id if proposal else None,
        ticket_id=_ticket_id(session, proposal.run_id) if proposal else None,
        actor="system",
        event_type="refund_succeeded",
        payload={"operation_id": op.id, "provider_reference": op.provider_reference, "amount_minor": op.amount_minor},
    )


def _fail(session: Session, op: RefundOperation, reason: str, provider: dict[str, Any] | None = None) -> None:
    order = session.get(Order, op.order_id, with_for_update=True)
    assert order is not None
    if op.status in TERMINAL:
        return
    op.status = "failed"
    op.last_error = reason
    op.provider_response = provider
    op.completed_at = datetime.now(UTC)
    order.reserved_refund_minor -= op.amount_minor  # release the reservation: definitive failure
    proposal = session.get(ActionProposal, op.proposal_id)
    if proposal is not None:
        proposal.status = "execution_failed"
    audit.record(
        session,
        run_id=proposal.run_id if proposal else None,
        ticket_id=_ticket_id(session, proposal.run_id) if proposal else None,
        actor="system",
        event_type="refund_failed",
        payload={"operation_id": op.id, "reason": reason},
    )


def _mark(
    session: Session,
    op: RefundOperation,
    status: str,
    *,
    error: str | None = None,
    provider: dict[str, Any] | None = None,
) -> None:
    op.status = status
    if error is not None:
        op.last_error = error
    if provider is not None:
        op.provider_response = provider
        op.provider_reference = provider.get("id") or op.provider_reference


def _apply_provider_state(op_id: str, provider: dict[str, Any]) -> RefundOutcome:
    with session_scope() as session:
        op = session.get(RefundOperation, op_id, with_for_update=True)
        assert op is not None
        status = provider.get("status")
        if status == "succeeded":
            _settle(session, op, provider)
        elif status == "processing":
            _mark(session, op, "processing", provider=provider)
        elif status == "failed":
            _fail(session, op, "provider reported failure", provider)
        else:
            _mark(session, op, "unknown", error=f"unexpected provider status {status!r}", provider=provider)
        session.flush()
        return _outcome(op)


# --------------------------------------------------------------------------- provider calls


def _payment_reference(session: Session, order_id: str) -> str:
    ref = session.scalar(select(Payment.provider_payment_reference).where(Payment.order_id == order_id).limit(1))
    assert ref is not None
    return ref


def _submit(op_id: str) -> RefundOutcome:
    """Send (or re-send, same key) the refund request. Never holds a transaction during the call."""
    settings = get_settings()
    with session_scope() as session:
        op = session.get(RefundOperation, op_id, with_for_update=True)
        assert op is not None
        if op.status in TERMINAL or op.status == "processing":
            return _outcome(op)
        if op.attempts >= settings.max_refund_attempts:
            _mark(session, op, "unknown", error="refund attempt budget exhausted")
            return _outcome(op, reason_code="ATTEMPTS_EXHAUSTED")
        op.status = "submitted"
        op.attempts += 1
        request: dict[str, Any] = {
            "idempotency_key": op.idempotency_key,
            "payment_reference": _payment_reference(session, op.order_id),
            "amount_minor": op.amount_minor,
            "reason": "Approved refund",
            "metadata": {"order_id": op.order_id, "proposal_id": op.proposal_id, "operation_id": op.id},
        }
    # COMMITTED: from here on a crash leaves `submitted`, which recovery treats as uncertain.
    maybe_crash("refund.before_provider_call")
    try:
        _status, body = PaymentsClient().create_refund(**request)
    except ExternalServiceError as exc:
        maybe_crash("refund.after_provider_error")
        with session_scope() as session:
            op = session.get(RefundOperation, op_id, with_for_update=True)
            assert op is not None
            if exc.kind in ("timeout", "unavailable"):
                _mark(session, op, "unknown", error=f"{exc.code}: outcome uncertain")
                return _outcome(op, reason_code=exc.code)
            if exc.kind == "conflict":
                # Same key, different payload at the provider: never guess.
                _mark(
                    session,
                    op,
                    "unknown",
                    error="provider reports idempotency key reused with another payload",
                    provider=exc.body_json if isinstance(exc.body_json, dict) else None,
                )
                return _outcome(op, reason_code="IDEMPOTENCY_CONFLICT")
            _fail(
                session, op, f"{exc.code}: {exc.body_json}", exc.body_json if isinstance(exc.body_json, dict) else None
            )
            return _outcome(op, reason_code=exc.code)
    maybe_crash("refund.after_provider_call")
    return _apply_provider_state(op_id, body)


def reconcile(op_id: str) -> RefundOutcome:
    """Ask the provider what happened to this key before deciding anything else."""
    settings = get_settings()
    with session_scope() as session:
        op = session.get(RefundOperation, op_id, with_for_update=True)
        assert op is not None
        if op.status in TERMINAL:
            return _outcome(op)
        if op.status == "pending":
            # Reserved but never sent: safe to send now.
            key = None
        else:
            key = op.idempotency_key
            op.reconcile_attempts += 1
        reconcile_attempts = op.reconcile_attempts
        audit.record(
            session,
            run_id=None,
            ticket_id=None,
            actor="reconciler",
            event_type="refund_reconcile_started",
            payload={"operation_id": op.id, "status": op.status, "attempt": reconcile_attempts},
        )
    if key is None:
        return _submit(op_id)
    if reconcile_attempts > settings.max_reconcile_attempts:
        return RefundOutcome(status="unknown", operation_id=op_id, reason_code="RECONCILE_BUDGET_EXHAUSTED")
    try:
        found = PaymentsClient().lookup_refund(key)
    except ExternalServiceError as exc:
        with session_scope() as session:
            op = session.get(RefundOperation, op_id, with_for_update=True)
            assert op is not None
            if op.status == "submitted":
                _mark(session, op, "unknown", error=f"reconcile lookup failed: {exc.code}")
            return _outcome(op, reason_code=exc.code)
    if found is not None:
        return _apply_provider_state(op_id, found)
    # The provider never recorded this key: the request did not take effect. Re-send with the SAME key.
    return _submit(op_id)


# --------------------------------------------------------------------------- entry points


def execute_approved_refund(proposal_id: str, *, now: datetime | None = None) -> RefundOutcome:
    """Execute exactly the approved proposal. Safe to call repeatedly (idempotent per proposal)."""
    now = now or datetime.now(UTC)

    with session_scope() as session:
        proposal = session.get(ActionProposal, proposal_id)
        if proposal is None:
            return RefundOutcome(status="not_authorized", reason_code="PROPOSAL_NOT_FOUND")
        existing = session.scalar(select(RefundOperation).where(RefundOperation.proposal_id == proposal_id))
        customer_id = proposal.payload["customer_id"]
        order_id = proposal.order_id
    if existing is not None:
        # Re-entry (retry, resume after crash): continue the existing operation, never create another.
        return continue_operation(existing.id)

    provider_view = records.get_payment_status(customer_id, order_id)  # network, outside any transaction

    with session_scope() as session:
        proposal = session.get(ActionProposal, proposal_id, with_for_update=True)
        assert proposal is not None
        ticket_id = _ticket_id(session, proposal.run_id)
        existing = session.scalar(select(RefundOperation).where(RefundOperation.proposal_id == proposal_id))
        if existing is not None:
            op_id = existing.id
        else:
            approval = _latest_approval(session, proposal_id)
            failure = _verify_authorization(session, proposal, approval, now)
            if failure is not None:
                audit.record(
                    session,
                    run_id=proposal.run_id,
                    ticket_id=ticket_id,
                    actor="system",
                    event_type="refund_blocked",
                    payload={"proposal_id": proposal_id, "reason": failure},
                )
                return RefundOutcome(status="not_authorized", reason_code=failure)
            assert approval is not None
            payload = proposal.payload
            damage = DamageReport(**payload["damage_report"]) if payload.get("damage_report") else None
            reported_at = datetime.fromisoformat(payload["reported_at"]) if payload.get("reported_at") else None
            decision, _facts = decide_refund(
                session,
                customer_id,
                order_id,
                basis=payload.get("basis", "return"),
                provider_view=provider_view,
                now=now,
                damage_report=damage,
                reported_at=reported_at,
                lock=True,
            )
            if decision.disposition != "eligible" or decision.amount_minor != payload["amount_minor"]:
                # Material change since approval: invalidate; a new proposal + approval is required.
                proposal.status = "superseded"
                audit.record(
                    session,
                    run_id=proposal.run_id,
                    ticket_id=ticket_id,
                    actor="system",
                    event_type="approval_invalidated",
                    payload={
                        "proposal_id": proposal_id,
                        "approval_id": approval.id,
                        "reason_code": decision.reason_code,
                        "disposition": decision.disposition,
                        "approved_minor": payload["amount_minor"],
                        "now_permitted_minor": decision.amount_minor,
                    },
                )
                return RefundOutcome(
                    status="invalidated",
                    reason_code=decision.reason_code,
                    message=decision.explanation,
                    details={"disposition": decision.disposition, "permitted_minor": decision.amount_minor},
                )
            order = session.get(Order, order_id, with_for_update=True)
            assert order is not None
            op = RefundOperation(
                order_id=order_id,
                proposal_id=proposal_id,
                approval_id=approval.id,
                amount_minor=payload["amount_minor"],
                currency="INR",
                idempotency_key=idempotency_key_for(proposal_id),
                status="pending",
            )
            order.reserved_refund_minor += op.amount_minor  # CHECK constraint enforces the balance
            session.add(op)
            session.flush()
            op_id = op.id
            audit.record(
                session,
                run_id=proposal.run_id,
                ticket_id=ticket_id,
                actor="system",
                event_type="refund_reserved",
                payload={
                    "operation_id": op.id,
                    "amount_minor": op.amount_minor,
                    "idempotency_key": op.idempotency_key,
                    "approval_id": approval.id,
                },
            )
    # COMMITTED (reservation durable) before any external call.
    maybe_crash("refund.after_reserve")
    return _submit(op_id)


def continue_operation(op_id: str) -> RefundOutcome:
    with session_scope() as session:
        op = session.get(RefundOperation, op_id)
        assert op is not None
        status = op.status
    if status in TERMINAL:
        with session_scope() as session:
            op = session.get(RefundOperation, op_id)
            assert op is not None
            return _outcome(op)
    if status == "pending":
        return _submit(op_id)
    if status == "processing":
        return refresh_processing(op_id)
    return reconcile(op_id)  # submitted / unknown: never re-send before looking up the key


def refresh_processing(op_id: str) -> RefundOutcome:
    with session_scope() as session:
        op = session.get(RefundOperation, op_id)
        assert op is not None
        if op.status != "processing":
            return _outcome(op)
        ref = op.provider_reference
        key = op.idempotency_key
    try:
        client = PaymentsClient()
        found = client.get_refund(ref) if ref else client.lookup_refund(key)
    except ExternalServiceError as exc:
        with session_scope() as session:
            op = session.get(RefundOperation, op_id)
            assert op is not None
            return _outcome(op, reason_code=exc.code)
    if found is None:
        return reconcile(op_id)
    return _apply_provider_state(op_id, found)


def reconcile_pending(limit: int = 20, stale_seconds: float = 0) -> list[RefundOutcome]:
    """Sweeper for operations left uncertain (worker crash, timeouts, slow settlement).

    ``stale_seconds`` skips operations touched very recently (likely still owned by a running workflow).
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=stale_seconds)
    with session_scope() as session:
        ids = session.scalars(
            select(RefundOperation.id)
            .where(
                RefundOperation.status.in_(("submitted", "unknown", "processing", "pending")),
                RefundOperation.updated_at <= cutoff,
            )
            .order_by(RefundOperation.updated_at)
            .limit(limit)
        ).all()
    results = []
    for op_id in ids:
        try:
            results.append(continue_operation(op_id))
        except Exception:  # keep sweeping; the failure is logged and the op stays uncertain
            log.exception("reconciliation failed for %s", op_id)
    return results


def operation_for_proposal(proposal_id: str) -> RefundOperation | None:
    with session_scope() as session:
        op = session.scalar(select(RefundOperation).where(RefundOperation.proposal_id == proposal_id))
        if op is not None:
            session.expunge(op)
        return op
