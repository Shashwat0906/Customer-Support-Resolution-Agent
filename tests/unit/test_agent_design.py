"""Agent design guarantees: tool allowlists, graph structure, state schema, adapters."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.llm.base import ToolCall
from app.schemas.agents import Classification, ResolutionProposal
from app.tools.registry import AGENT_TOOLS, TOOLS
from app.workflows.state import ResolutionState

WRITE_TOOLS = {n for n, t in TOOLS.items() if t.write}


def test_no_agent_has_a_write_tool() -> None:
    assert WRITE_TOOLS == {"create_refund_proposal", "execute_approved_refund", "update_ticket"}
    for agent, allowed in AGENT_TOOLS.items():
        assert not (allowed & WRITE_TOOLS), agent


def test_agents_have_only_the_tools_they_need() -> None:
    assert "execute_approved_refund" not in AGENT_TOOLS["investigation"]
    assert AGENT_TOOLS["policy"] == {"search_policy"}
    assert AGENT_TOOLS["resolution"] == frozenset()
    assert "search_policy" not in AGENT_TOOLS["investigation"]
    assert AGENT_TOOLS["single"] == AGENT_TOOLS["investigation"] | {"search_policy"}


def test_required_read_tools_exist() -> None:
    for name in (
        "get_order",
        "get_return_status",
        "get_payment_status",
        "get_refund_status",
        "search_policy",
        "get_ticket",
    ):
        assert name in TOOLS and not TOOLS[name].write


def test_graph_runs_investigation_and_policy_in_parallel() -> None:
    from app.workflows.graph import build_graph

    g = build_graph().compile().get_graph()
    into_resolve = {e.source for e in g.edges if e.target == "resolve"}
    assert into_resolve == {"investigate", "retrieve_policy"}
    assert {e.target for e in g.edges if e.source == "load_order_facts"} >= {
        "investigate",
        "retrieve_policy",
        "single_agent",
    }
    # Side effects live outside the interrupted nodes.
    assert {e.target for e in g.edges if e.source == "await_approval"} >= {"execute_refund"}


def test_state_contains_required_fields() -> None:
    required = {
        "run_id",
        "ticket_id",
        "authenticated_customer_id",
        "order_id",
        "complaint_category",
        "customer_message",
        "verified_facts",
        "policy_references",
        "missing_information",
        "proposed_action_id",
        "approval_id",
        "execution_status",
        "external_result_reference",
        "retry_counts",
        "error_details",
        "final_response",
    }
    assert required <= set(ResolutionState.model_fields)


def test_resolution_proposal_schema() -> None:
    fields = set(ResolutionProposal.model_fields)
    assert {
        "action",
        "order_id",
        "amount_minor",
        "currency",
        "reason",
        "evidence_references",
        "policy_references",
        "missing_information",
    } <= fields
    with pytest.raises(ValueError):
        ResolutionProposal(action="refund", order_id="ORD-1", amount_minor=-5, reason="x")
    with pytest.raises(ValueError):
        ResolutionProposal.model_validate(
            {"action": "refund", "order_id": "ORD-1", "amount_minor": 5, "currency": "USD", "reason": "x"}
        )


def test_classification_normalises_order_reference() -> None:
    assert Classification(category="other", order_reference="ord 5001", summary="x").order_reference == "ORD-5001"
    assert Classification(category="other", order_reference="5001", summary="x").order_reference == "ORD-5001"


def test_mock_classifier_categories() -> None:
    from app.llm.mock import classify

    assert classify("I returned my headphones but no refund yet")["category"] == "refund_request"
    assert classify("Where is ORD-5002?")["order_reference"] == "ORD-5002"
    assert classify("Where is my parcel, it is delayed")["category"] == "order_status"
    dmg = classify("My speaker arrived with a cracked case. I have photos, please replace it")
    assert (dmg["category"], dmg["photos_available"], dmg["preference"]) == ("damaged_item", True, "replacement")
    assert dmg["damage_description"]
    assert classify("My earbuds arrived damaged")["damage_description"] is None


def test_anthropic_adapter_converts_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Message shaping only; no network (there is no API key in CI)."""
    pytest.importorskip("anthropic")
    from app.llm import anthropic_provider

    captured: dict[str, Any] = {}

    class FakeMessages:
        def create(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace(
                stop_reason="tool_use",
                model="claude-opus-5-5",
                usage=SimpleNamespace(input_tokens=11, output_tokens=7),
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        id="tu_1",
                        name="get_order",
                        input={"order_id": "ORD-1"},
                        model_dump=lambda **_: {"type": "tool_use", "id": "tu_1"},
                    )
                ],
            )

    llm = anthropic_provider.AnthropicLLM.__new__(anthropic_provider.AnthropicLLM)
    llm._anthropic = __import__("anthropic")
    llm.model = "claude-opus-5-5"
    llm.client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    msgs = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [ToolCall("a", "get_order", {})],
            "provider_content": [{"type": "tool_use", "id": "a", "name": "get_order", "input": {}}],
        },
        {"role": "tool", "tool_call_id": "a", "name": "get_order", "content": "{}", "is_error": False},
        {"role": "tool", "tool_call_id": "b", "name": "get_order", "content": "{}", "is_error": True},
    ]
    resp = llm.chat(agent="investigation", system="sys", messages=msgs, tools=[TOOLS["get_order"].schema()])
    assert captured["model"] == "claude-opus-5-5" and captured["fallbacks"] == "default"
    assert "tool_choice" not in captured
    assert captured["messages"][1]["content"][0]["type"] == "tool_use"
    results = captured["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"] and results[1]["is_error"]  # one user turn
    assert resp.tool_calls[0].name == "get_order" and resp.input_tokens == 11


def test_anthropic_refusal_is_an_error() -> None:
    pytest.importorskip("anthropic")
    from app.llm import anthropic_provider
    from app.llm.base import LLMError

    llm = anthropic_provider.AnthropicLLM.__new__(anthropic_provider.AnthropicLLM)
    llm._anthropic = __import__("anthropic")
    llm.model = "claude-opus-5-5"
    llm.client = SimpleNamespace(
        beta=SimpleNamespace(
            messages=SimpleNamespace(
                create=lambda **_: SimpleNamespace(stop_reason="refusal", content=[], usage=None, model="m")
            )
        )
    )
    with pytest.raises(LLMError) as exc:
        llm.chat(agent="classifier", system="s", messages=[{"role": "user", "content": "x"}])
    assert exc.value.code == "LLM_REFUSAL"


def test_grounding_guard() -> None:
    from app.workflows.messages import check_grounding, format_inr

    assert format_inr(899_900) == "₹8,999.00" and format_inr(7_499_900) == "₹74,999.00"
    assert format_inr(12_345_678_900) == "₹12,34,56,789.00"
    assert check_grounding(
        "Your refund has been issued.", outcome="refund_processing", allowed_amounts=set(), required_refs=[]
    )
    assert check_grounding(
        "We refunded ₹99,999.00", outcome="refund_succeeded", allowed_amounts={899_900}, required_refs=[]
    )
    assert not check_grounding(
        "Refund of ₹8,999.00 confirmed (ref rf_1).",
        outcome="refund_succeeded",
        allowed_amounts={899_900},
        required_refs=["rf_1"],
    )


def test_grounding_guard_respects_negation() -> None:
    from app.workflows.messages import check_grounding

    for text in ("No refund has been issued.", "A refund was not issued yet."):
        assert not check_grounding(text, outcome="refund_rejected", allowed_amounts=set(), required_refs=[]), text
    assert check_grounding(
        "Good news, your refund has been issued!", outcome="refund_rejected", allowed_amounts=set(), required_refs=[]
    )
