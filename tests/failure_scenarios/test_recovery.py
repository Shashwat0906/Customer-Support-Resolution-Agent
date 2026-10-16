"""Week 3 acceptance: provider failures, uncertain outcomes and worker crashes never duplicate refunds."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select, text

from app.db.models import Order, RefundOperation
from app.db.session import session_scope
from app.services import faults, refunds, tickets
from app.services.faults import SimulatedCrash
from app.worker import drain, sweep
from tests.conftest import ADMIN, MOCK_ROOT, add_fault, provider_refunds, provider_tickets
from tests.helpers import decide, review_state, run_state, start_and_drain

HEADLINE = "I returned my headphones last week, but I still haven't received my refund."


def _approved_run(client):  # type: ignore[no-untyped-def]
    run, h = start_and_drain(client, "C-1001", HEADLINE)
    assert run["status"] == "awaiting_approval", run
    assert decide(client, run["pending_approval_id"]).status_code == 200
    return run, h


def _ledger(order_id: str = "ORD-5001") -> tuple[int, int, list[RefundOperation]]:
    with session_scope() as s:
        o = s.get(Order, order_id)
        ops = s.scalars(select(RefundOperation).where(RefundOperation.order_id == order_id)).all()
        for op in ops:
            s.expunge(op)
        return o.refunded_amount_minor, o.reserved_refund_minor, list(ops)


def _remote(order_id: str = "ORD-5001") -> list[dict]:
    return [r for r in provider_refunds() if r["order_id"] == order_id]


def _create_requests(key_prefix: str = "refund-prp_") -> list[dict]:
    reqs = httpx.get(f"{MOCK_ROOT}/admin/refunds", headers=ADMIN).json()["create_requests"]
    return [r for r in reqs if (r["idempotency_key"] or "").startswith(key_prefix)]


def _expire_leases() -> None:
    with session_scope() as s:
        s.execute(
            text("UPDATE workflow_jobs SET lease_expires_at = now() - interval '1 second' WHERE status = 'running'")
        )


def test_timeout_after_provider_acceptance_reconciles_without_duplicate(client) -> None:  # type: ignore[no-untyped-def]
    run, h = _approved_run(client)
    add_fault(target="payments.create_refund", mode="commit_then_timeout", delay_ms=1500)
    drain()
    done = review_state(client, run["run_id"])
    assert done["outcome"] == "refund_succeeded", done
    assert done["state"]["retry_counts"].get("refund_reconcile", 0) >= 1
    assert len(_remote()) == 1
    assert [r["outcome"] for r in _create_requests()] == ["created"]  # never re-sent: lookup found it
    assert _ledger()[:2] == (899_900, 0)


def test_error_before_acceptance_retries_with_same_key(client) -> None:  # type: ignore[no-untyped-def]
    run, h = _approved_run(client)
    add_fault(target="payments.create_refund", mode="error", status_code=503, count=1)
    drain()
    done = review_state(client, run["run_id"])
    assert done["outcome"] == "refund_succeeded", done
    reqs = _create_requests()
    assert [r["outcome"] for r in reqs] == ["fault:error", "created"]
    assert len({r["idempotency_key"] for r in reqs}) == 1  # same key both times
    assert len(_remote()) == 1


def test_processing_refund_is_verified_before_claiming_success(client) -> None:  # type: ignore[no-untyped-def]
    run, h = _approved_run(client)
    add_fault(target="payments.create_refund", mode="processing", settle_after=2)
    drain()
    done = review_state(client, run["run_id"])
    assert done["outcome"] == "refund_succeeded" and done["state"]["retry_counts"]["refund_reconcile"] >= 1


def test_slow_settlement_reports_processing_honestly(client) -> None:  # type: ignore[no-untyped-def]
    run, h = _approved_run(client)
    add_fault(target="payments.create_refund", mode="processing", settle_after=50)
    drain()
    done = review_state(client, run["run_id"])
    assert done["outcome"] == "refund_processing" and done["status"] == "completed"
    msg = done["final_response"].lower()
    assert "submitted" in msg and "not yet received final confirmation" in msg
    assert "confirmed by our payment provider" not in msg
    _, reserved, ops = _ledger()
    assert reserved == 899_900 and ops[0].status == "processing"  # money stays reserved, not counted as refunded


def test_ticket_update_failure_retries_only_the_ticket(client) -> None:  # type: ignore[no-untyped-def]
    run, h = _approved_run(client)
    add_fault(target="ticketing.add_note", mode="error", status_code=503, count=2)
    drain()
    done = review_state(client, run["run_id"])
    assert done["outcome"] == "refund_succeeded"
    assert done["state"]["ticket_sync"]["synced"] and done["state"]["ticket_sync"]["attempts"] == 3
    assert len(_remote()) == 1 and len(_create_requests()) == 1


def test_ticket_sync_down_then_swept_later(client) -> None:  # type: ignore[no-untyped-def]
    run, h = _approved_run(client)
    add_fault(target="ticketing.upsert_ticket", mode="error", status_code=503, count=-1)
    drain()
    done = review_state(client, run["run_id"])
    assert done["outcome"] == "refund_succeeded"
    assert not done["state"]["ticket_sync"]["synced"]
    assert any(e["code"] == "TICKET_SYNC_FAILED" for e in done["state"]["error_details"])
    httpx.delete(f"{MOCK_ROOT}/admin/faults", headers=ADMIN)
    assert tickets.retry_unsynced() == 1
    notes = [n for n in provider_tickets()["notes"] if n["ticket_id"] == run["ticket_id"]]
    assert len(notes) == 1
    assert len(_remote()) == 1  # the refund was never repeated


@pytest.mark.parametrize(
    "point",
    [
        "refund.after_reserve",
        "refund.before_provider_call",
        "refund.after_provider_call",
        "refund.after_provider_error",
    ],
)
def test_worker_crash_recovers_without_duplicate_refund(client, point: str) -> None:  # type: ignore[no-untyped-def]
    run, h = _approved_run(client)
    if point == "refund.after_provider_error":
        add_fault(target="payments.create_refund", mode="commit_then_error", status_code=500)
    faults.arm(point)
    with pytest.raises(SimulatedCrash):
        drain()
    assert run_state(client, run["run_id"], h)["status"] in ("executing", "running")
    _expire_leases()
    assert sweep()["requeued"] == 1
    drain()
    done = review_state(client, run["run_id"])
    assert done["outcome"] == "refund_succeeded", done
    refunded, reserved, ops = _ledger()
    assert (refunded, reserved, len(ops)) == (899_900, 0, 1)
    assert len(_remote()) == 1
    created = [r for r in _create_requests() if r["outcome"] == "created"]
    assert len(created) == 1


def test_unknown_outcome_after_budget_escalates_and_keeps_reservation(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_reconcile_attempts", 2)
    run, h = _approved_run(client)
    add_fault(target="payments.create_refund", mode="error", status_code=503, count=-1)
    add_fault(target="payments.lookup_refund", mode="error", status_code=503, count=-1)
    drain()
    done = review_state(client, run["run_id"])
    assert done["status"] == "escalated" and done["state"]["escalation_reason"].startswith("refund_outcome_unknown")
    assert "not issued a second refund" in done["final_response"]
    _, reserved, ops = _ledger()
    assert reserved == 899_900 and ops[0].status == "unknown"
    # Provider recovers: the sweeper settles it with the same key, still exactly one refund.
    httpx.delete(f"{MOCK_ROOT}/admin/faults", headers=ADMIN)
    monkeypatch.setattr(get_settings(), "max_reconcile_attempts", 10)
    refunds.reconcile_pending()
    refunded, reserved, ops = _ledger()
    assert (refunded, reserved, ops[0].status) == (899_900, 0, "succeeded")
    assert len(_remote()) == 1
