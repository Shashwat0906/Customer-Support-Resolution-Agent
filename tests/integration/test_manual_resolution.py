"""Week 1 acceptance: resolve a refund ticket through the APIs without an LLM."""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Order, RefundOperation, Ticket, WorkflowRun
from app.db.session import session_scope
from app.services import proposals, refunds, tickets
from tests.conftest import customer_headers, provider_refunds, provider_tickets, reviewer_headers


def test_manual_refund_resolution_without_llm(client) -> None:  # type: ignore[no-untyped-def]
    cust = customer_headers(client, "C-1001")
    ticket = client.post(
        "/tickets",
        headers=cust,
        json={
            "message": "I returned my headphones last week, but I still haven't received my refund.",
            "order_id": "ORD-5001",
        },
    ).json()
    run_id = client.post(f"/tickets/{ticket['ticket_id']}/runs", headers=cust).json()["run_id"]

    # Deterministic proposal (what the workflow does after investigation).
    result = proposals.create_refund_proposal(
        run_id=run_id,
        customer_id="C-1001",
        order_id="ORD-5001",
        amount_minor=899_900,
        reason="Return received and inspection passed",
    )
    with session_scope() as s:
        s.get(WorkflowRun, run_id).status = "awaiting_approval"

    rev = reviewer_headers(client, "S-01")
    queue = client.get("/approvals", headers=rev).json()
    assert [a["approval_id"] for a in queue] == [result["approval_id"]]
    assert queue[0]["proposal"]["payload"]["amount_minor"] == 899_900
    decision = client.post(
        f"/approvals/{result['approval_id']}/decision",
        headers=rev,
        json={"decision": "approve", "note": "Inspection passed"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["decision"] == "approved"

    outcome = refunds.execute_approved_refund(result["proposal_id"])
    assert outcome.status == "succeeded" and outcome.provider_reference

    with session_scope() as s:
        order = s.get(Order, "ORD-5001")
        assert (order.refunded_amount_minor, order.reserved_refund_minor) == (899_900, 0)
        ops = s.scalars(select(RefundOperation).where(RefundOperation.order_id == "ORD-5001")).all()
        assert len(ops) == 1 and ops[0].idempotency_key == f"refund-{result['proposal_id']}"
    remote = [r for r in provider_refunds() if r["order_id"] == "ORD-5001"]
    assert len(remote) == 1 and remote[0]["status"] == "succeeded" and remote[0]["id"] == outcome.provider_reference

    sync = tickets.update_ticket(
        ticket["ticket_id"],
        status="resolved",
        run_id=run_id,
        note=f"Refund of INR 8,999.00 confirmed by provider ({outcome.provider_reference}).",
        idempotency_key=f"{run_id}:final",
    )
    assert sync["synced"]
    with session_scope() as s:
        assert s.get(Ticket, ticket["ticket_id"]).status == "resolved"
    external = provider_tickets()
    assert any(n["ticket_id"] == ticket["ticket_id"] for n in external["notes"])

    # Executing again (retry / duplicate request) is a no-op.
    again = refunds.execute_approved_refund(result["proposal_id"])
    assert again.operation_id == outcome.operation_id and again.status == "succeeded"
    assert len([r for r in provider_refunds() if r["order_id"] == "ORD-5001"]) == 1
