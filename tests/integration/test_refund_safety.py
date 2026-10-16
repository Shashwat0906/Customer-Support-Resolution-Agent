"""Refund execution safety: authorization, revalidation, idempotency, concurrency."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.auth.deps import Principal
from app.db.models import Approval, Order, RefundOperation, Return, Ticket, WorkflowRun
from app.db.session import session_scope
from app.schemas.errors import AppError
from app.services import approvals, proposals, refunds
from tests.conftest import provider_refunds

SENIOR = Principal("staff", "S-02", "demo|staff|S-02", "Kabir", "senior_reviewer", 20_000_000)
JUNIOR = Principal("staff", "S-01", "demo|staff|S-01", "Riya", "reviewer", 3_000_000)


def _run(customer_id: str, order_id: str) -> str:
    with session_scope() as s:
        t = Ticket(customer_id=customer_id, order_id=order_id, summary="test", category="refund_request")
        s.add(t)
        s.flush()
        r = WorkflowRun(ticket_id=t.id, thread_id=f"thread-{t.id}", status="awaiting_approval", agent_mode="specialist")
        s.add(r)
        s.flush()
        return r.id


def _proposal(customer_id: str = "C-1001", order_id: str = "ORD-5001", amount: int = 899_900) -> dict:
    return proposals.create_refund_proposal(
        run_id=_run(customer_id, order_id),
        customer_id=customer_id,
        order_id=order_id,
        amount_minor=amount,
        reason="test",
    )


def _remote(order_id: str) -> list[dict]:
    return [r for r in provider_refunds() if r["order_id"] == order_id]


def test_proposal_never_moves_money(anchor: datetime) -> None:
    _proposal()
    assert _remote("ORD-5001") == []
    with session_scope() as s:
        assert s.scalars(select(RefundOperation).where(RefundOperation.order_id == "ORD-5001")).all() == []


def test_proposal_rejects_wrong_amount_and_ineligible_orders(anchor: datetime) -> None:
    with pytest.raises(AppError) as exc:
        _proposal(amount=9_999_999)
    assert exc.value.code == "AMOUNT_MISMATCH"
    with pytest.raises(AppError) as exc:
        _proposal("C-1002", "ORD-5003", 1_299_900)  # inspection failed
    assert exc.value.code == "NOT_ELIGIBLE"


def test_proposal_rejects_foreign_order(anchor: datetime) -> None:
    with pytest.raises(AppError) as exc:
        proposals.create_refund_proposal(
            run_id=_run("C-1001", "ORD-5001"),
            customer_id="C-1001",
            order_id="ORD-5003",
            amount_minor=1_299_900,
            reason="x",
        )
    assert exc.value.code == "ORDER_NOT_FOUND"


def test_execution_requires_stored_approval(anchor: datetime) -> None:
    p = _proposal()
    out = refunds.execute_approved_refund(p["proposal_id"])
    assert out.status == "not_authorized" and out.reason_code == "APPROVAL_NOT_GRANTED"
    approvals.decide(p["approval_id"], SENIOR, "reject", "no")
    assert refunds.execute_approved_refund(p["proposal_id"]).status == "not_authorized"
    assert _remote("ORD-5001") == []


def test_expired_approval_cannot_execute(anchor: datetime) -> None:
    p = _proposal()
    approvals.decide(p["approval_id"], SENIOR, "approve", None)
    out = refunds.execute_approved_refund(p["proposal_id"], now=datetime.now(UTC) + timedelta(days=3))
    assert out.status == "not_authorized" and out.reason_code == "APPROVAL_EXPIRED"


def test_decision_after_expiry_is_refused(anchor: datetime) -> None:
    p = _proposal()
    with pytest.raises(AppError) as exc:
        approvals.decide(p["approval_id"], SENIOR, "approve", None, now=datetime.now(UTC) + timedelta(days=3))
    assert exc.value.code == "APPROVAL_EXPIRED" and exc.value.status_code == 410
    with session_scope() as s:
        assert s.get(Approval, p["approval_id"]).decision == "expired"


def test_reviewer_limits(anchor: datetime) -> None:
    p = _proposal("C-1003", "ORD-5005", 5_499_900)  # requires senior review
    with pytest.raises(AppError) as exc:
        approvals.decide(p["approval_id"], JUNIOR, "approve", None)
    assert exc.value.code == "SENIOR_REVIEW_REQUIRED"
    assert approvals.decide(p["approval_id"], SENIOR, "approve", None)["decision"] == "approved"


def test_double_decision_rejected(anchor: datetime) -> None:
    p = _proposal()
    approvals.decide(p["approval_id"], SENIOR, "approve", None)
    with pytest.raises(AppError) as exc:
        approvals.decide(p["approval_id"], SENIOR, "reject", None)
    assert exc.value.code == "APPROVAL_ALREADY_DECIDED"


def test_material_change_invalidates_approval(anchor: datetime) -> None:
    p = _proposal()
    approvals.decide(p["approval_id"], SENIOR, "approve", None)
    with session_scope() as s:  # warehouse re-inspects and marks the item for review
        s.scalar(select(Return).where(Return.order_id == "ORD-5001")).inspection_status = "needs_review"
    out = refunds.execute_approved_refund(p["proposal_id"])
    assert out.status == "invalidated" and out.reason_code == "inspection_needs_review"
    assert _remote("ORD-5001") == []


def test_repeated_execution_is_idempotent(anchor: datetime) -> None:
    p = _proposal()
    approvals.decide(p["approval_id"], SENIOR, "approve", None)
    outs = [refunds.execute_approved_refund(p["proposal_id"]) for _ in range(3)]
    assert {o.operation_id for o in outs} == {outs[0].operation_id}
    assert len(_remote("ORD-5001")) == 1


def test_concurrent_execution_creates_one_refund(anchor: datetime) -> None:
    p = _proposal()
    approvals.decide(p["approval_id"], SENIOR, "approve", None)
    results: list[refunds.RefundOutcome] = []
    threads = [
        threading.Thread(target=lambda: results.append(refunds.execute_approved_refund(p["proposal_id"])))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_remote("ORD-5001")) == 1
    with session_scope() as s:
        assert len(s.scalars(select(RefundOperation).where(RefundOperation.order_id == "ORD-5001")).all()) == 1
        o = s.get(Order, "ORD-5001")
        assert (o.refunded_amount_minor, o.reserved_refund_minor) == (899_900, 0)


def test_two_proposals_for_same_order_refund_once(anchor: datetime) -> None:
    a, b = _proposal(), _proposal()
    for p in (a, b):
        approvals.decide(p["approval_id"], SENIOR, "approve", None)
    results: list[refunds.RefundOutcome] = []
    threads = [
        threading.Thread(target=lambda p=p: results.append(refunds.execute_approved_refund(p["proposal_id"])))
        for p in (a, b)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.status for r in results) == ["invalidated", "succeeded"]
    assert len(_remote("ORD-5001")) == 1
