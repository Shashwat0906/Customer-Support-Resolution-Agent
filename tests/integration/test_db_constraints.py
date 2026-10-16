"""Database-enforced financial and integrity constraints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

from app.db.models import ActionProposal, Approval, AuditEvent, Order, RefundOperation, WorkflowRun
from app.db.session import session_scope
from app.services.hashing import payload_hash

DBError = (IntegrityError, InternalError, ProgrammingError)


def _proposal(session, order_id: str = "ORD-5001", amount: int = 899_900) -> ActionProposal:  # type: ignore[no-untyped-def]
    run = session.scalar(select(WorkflowRun).where(WorkflowRun.id == "run_hist_ORD-5008"))
    payload = {"action": "refund", "order_id": order_id, "amount_minor": amount}
    p = ActionProposal(
        run_id=run.id,
        order_id=order_id,
        action_type="refund",
        revision=99,
        payload=payload,
        payload_hash=payload_hash(payload),
    )
    session.add(p)
    session.flush()
    return p


def test_refunds_cannot_exceed_paid_amount(anchor: datetime) -> None:
    with pytest.raises(DBError), session_scope() as s:
        order = s.get(Order, "ORD-5001")
        order.reserved_refund_minor = order.paid_amount_minor
        order.refunded_amount_minor = 1
        s.flush()


def test_negative_reservation_rejected(anchor: datetime) -> None:
    with pytest.raises(DBError), session_scope() as s:
        s.get(Order, "ORD-5001").reserved_refund_minor = -1
        s.flush()


def test_currency_is_inr_only(anchor: datetime) -> None:
    with pytest.raises(DBError), session_scope() as s:
        s.get(Order, "ORD-5001").currency = "USD"
        s.flush()


def test_money_columns_are_integers(anchor: datetime) -> None:
    with session_scope() as s:
        types = dict(
            s.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns WHERE table_name IN "
                    "('orders', 'refund_operations', 'order_items') AND column_name LIKE '%minor'"
                )
            ).all()
        )
    assert types and set(types.values()) == {"bigint"}


def test_idempotency_key_unique(anchor: datetime) -> None:
    with pytest.raises(DBError), session_scope() as s:
        p = _proposal(s)
        a = Approval(
            proposal_id=p.id,
            proposal_payload_hash=p.payload_hash,
            decision="approved",
            reviewer_id="S-02",
            decided_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        s.add(a)
        s.flush()
        s.add(
            RefundOperation(
                order_id="ORD-5001",
                proposal_id=p.id,
                approval_id=a.id,
                amount_minor=1,
                idempotency_key="historic-refund-ORD-5008",
                status="pending",
            )
        )
        s.flush()


def test_proposal_payload_is_immutable(anchor: datetime) -> None:
    with session_scope() as s:
        pid = _proposal(s).id
    with pytest.raises(DBError), session_scope() as s:
        p = s.get(ActionProposal, pid)
        p.payload = {**p.payload, "amount_minor": 1}
        s.flush()
    with session_scope() as s:  # status changes are allowed
        s.get(ActionProposal, pid).status = "rejected"


def test_approval_must_match_proposal_hash(anchor: datetime) -> None:
    with pytest.raises(DBError), session_scope() as s:
        p = _proposal(s)
        s.add(
            Approval(
                proposal_id=p.id,
                proposal_payload_hash="0" * 64,
                decision="pending",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        s.flush()


def test_only_one_pending_approval_per_proposal(anchor: datetime) -> None:
    with pytest.raises(DBError), session_scope() as s:
        p = _proposal(s)
        for _ in range(2):
            s.add(
                Approval(
                    proposal_id=p.id,
                    proposal_payload_hash=p.payload_hash,
                    decision="pending",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            s.flush()


def test_approved_requires_reviewer(anchor: datetime) -> None:
    with pytest.raises(DBError), session_scope() as s:
        p = _proposal(s)
        s.add(
            Approval(
                proposal_id=p.id,
                proposal_payload_hash=p.payload_hash,
                decision="approved",
                decided_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        s.flush()


def test_audit_events_append_only(anchor: datetime) -> None:
    with session_scope() as s:
        s.add(AuditEvent(actor="test", event_type="x", redacted_payload={}))
    with pytest.raises(DBError), session_scope() as s:
        s.execute(text("UPDATE audit_events SET event_type = 'y'"))
    with pytest.raises(DBError), session_scope() as s:
        s.execute(text("DELETE FROM audit_events"))


def test_checkpoint_tables_are_in_separate_schema(anchor: datetime) -> None:
    with session_scope() as s:
        rows = s.execute(
            text("SELECT table_schema FROM information_schema.tables WHERE table_name = 'checkpoints'")
        ).all()
    assert [r[0] for r in rows] == ["langgraph"]


def test_seed_ledger_is_consistent(anchor: datetime) -> None:
    with session_scope() as s:
        for order in s.scalars(select(Order)).all():
            ops = s.scalars(select(RefundOperation).where(RefundOperation.order_id == order.id)).all()
            assert order.refunded_amount_minor == sum(o.amount_minor for o in ops if o.status == "succeeded")
            assert order.reserved_refund_minor == sum(o.amount_minor for o in ops if o.status == "processing")
