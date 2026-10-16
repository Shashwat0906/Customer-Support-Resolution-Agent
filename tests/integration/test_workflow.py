"""End-to-end workflows through the API, the worker and LangGraph (mock LLM)."""

from __future__ import annotations

import pytest

from tests.conftest import provider_refunds, reviewer_headers
from tests.helpers import decide, events, run_state, start_and_drain

HEADLINE = "I returned my headphones last week, but I still haven't received my refund."


@pytest.mark.parametrize("mode", ["specialist", "single"])
def test_order_status_answered(client, mode: str) -> None:  # type: ignore[no-untyped-def]
    run, h = start_and_drain(client, "C-1001", "Where is my cable order ORD-5002?", agent_mode=mode)
    assert run["status"] == "completed" and run["outcome"] == "answered", run
    assert "in transit" in run["final_response"] and "Delhivery" in run["final_response"]
    assert run["state"]["decision"]["reason_code"] == "in_transit"
    ticket = client.get(f"/tickets/{run['ticket_id']}", headers=h).json()
    assert ticket["status"] == "resolved" and ticket["category"] == "order_status"
    assert ticket["messages"][-1]["author_type"] == "agent"


def test_refund_produces_validated_proposal_with_citations(client) -> None:  # type: ignore[no-untyped-def]
    """Week 2 acceptance: evidence gathered, validated proposal with policy citations."""
    run, h = start_and_drain(client, "C-1001", HEADLINE)
    assert run["status"] == "awaiting_approval", run
    state = run["state"]
    assert state["order_id"] == "ORD-5001"  # inferred from "headphones" among the customer's own orders
    assert state["complaint_category"] == "refund_request"
    assert {f["ref"] for f in state["evidence"]["facts"]} >= {"order:ORD-5001", "return:RET-ORD-5001"}
    assert state["resolution_draft"]["action"] == "refund"
    assert state["resolution_draft"]["amount_minor"] == 899_900
    assert any(c["document_id"] == "refund-eligibility-v2" for c in state["policy_references"])
    assert all(c["document_id"] != "refund-eligibility-v1" for c in state["policy_references"])
    assert state["decision"]["disposition"] == "eligible"
    approval = client.get(f"/approvals/{run['pending_approval_id']}", headers=reviewer_headers(client)).json()
    payload = approval["proposal"]["payload"]
    assert (payload["order_id"], payload["amount_minor"], payload["currency"]) == ("ORD-5001", 899_900, "INR")
    assert payload["policy_references"] and payload["evidence_references"]
    assert provider_refunds() == [r for r in provider_refunds() if r["order_id"] != "ORD-5001"]  # nothing moved
    evts = events(client, run["run_id"], reviewer_headers(client))
    assert "tool_called" in evts and "proposal_created" in evts and "approval_requested" in evts


@pytest.mark.parametrize("mode", ["specialist", "single"])
def test_full_refund_flow(client, mode: str) -> None:  # type: ignore[no-untyped-def]
    from app.worker import drain

    run, h = start_and_drain(client, "C-1001", HEADLINE, agent_mode=mode)
    assert run["status"] == "awaiting_approval"
    r = decide(client, run["pending_approval_id"], staff_id="S-01")
    assert r.status_code == 200, r.text
    assert run_state(client, run["run_id"], h)["status"] == "queued"
    drain()
    done = run_state(client, run["run_id"], h)
    assert done["status"] == "completed" and done["outcome"] == "refund_succeeded", done
    ref = done["state"]["external_result_reference"]
    assert ref and ref in done["final_response"] and "₹8,999.00" in done["final_response"]
    remote = [x for x in provider_refunds() if x["order_id"] == "ORD-5001"]
    assert len(remote) == 1 and remote[0]["id"] == ref
    ticket = client.get(f"/tickets/{done['ticket_id']}", headers=h).json()
    assert ticket["status"] == "resolved"
    metrics = client.get(f"/runs/{run['run_id']}", headers=reviewer_headers(client)).json()["metrics"]
    assert metrics["model_calls"] > 0 and metrics["waiting_approval_ms"] > 0 and metrics["active_ms"] > 0


def test_customer_view_hides_internal_details(client) -> None:  # type: ignore[no-untyped-def]
    run, h = start_and_drain(client, "C-1001", HEADLINE)
    run = run_state(client, run["run_id"], h)  # as the customer sees it
    assert run["metrics"] is None and "error_details" not in run["state"]
    for e in client.get(f"/runs/{run['run_id']}/events", headers=h).json():
        assert not e["actor"].startswith("reviewer:")
