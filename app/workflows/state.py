"""Validated LangGraph state for one resolution workflow.

The first block holds the fields the specification requires; the second holds
working data and counters. Counters and error lists use additive reducers so
the concurrently running investigation and policy branches can both report.
"""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


def _add(a: float | int | None, b: float | int | None) -> Any:
    return (a or 0) + (b or 0)


def _merge_counts(a: dict[str, int] | None, b: dict[str, int] | None) -> dict[str, int]:
    out = dict(a or {})
    for k, v in (b or {}).items():
        out[k] = out.get(k, 0) + v
    return out


class ResolutionState(BaseModel):
    # --- required by the specification
    run_id: str
    ticket_id: str
    authenticated_customer_id: str
    order_id: str | None = None
    complaint_category: str | None = None
    customer_message: str = ""
    verified_facts: dict[str, Any] = Field(default_factory=dict)
    policy_references: list[dict[str, Any]] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    proposed_action_id: str | None = None
    approval_id: str | None = None
    execution_status: str | None = None
    external_result_reference: str | None = None
    retry_counts: Annotated[dict[str, int], _merge_counts] = Field(default_factory=dict)
    error_details: Annotated[list[dict[str, Any]], operator.add] = Field(default_factory=list)
    final_response: str | None = None

    # --- working data
    agent_mode: Literal["specialist", "single"] = "specialist"
    reported_at: datetime | None = None
    customer_messages: list[str] = Field(default_factory=list)
    classification: dict[str, Any] | None = None
    damage_report: dict[str, Any] | None = None
    investigation_results: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] | None = None
    investigation_summary: str | None = None
    policy_finding: dict[str, Any] | None = None
    resolution_draft: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    clarification_question: str | None = None
    clarifications_asked: int = 0
    proposal_revisions: int = 0
    outcome: str | None = None
    escalation_reason: str | None = None
    route: str | None = None
    ticket_sync: dict[str, Any] | None = None
    agent_failures: Annotated[list[str], operator.add] = Field(default_factory=list)

    # --- counters (additive)
    step_count: Annotated[int, _add] = 0
    model_calls: Annotated[int, _add] = 0
    tool_calls: Annotated[int, _add] = 0
    tokens: Annotated[int, _add] = 0
    estimated_cost_usd: Annotated[float, _add] = 0.0
    unauthorized_tool_attempts: Annotated[int, _add] = 0
    proposal_corrections: Annotated[int, _add] = 0


PUBLIC_FIELDS = (
    "run_id",
    "ticket_id",
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
    "agent_mode",
    "classification",
    "damage_report",
    "evidence",
    "investigation_summary",
    "policy_finding",
    "resolution_draft",
    "decision",
    "clarification_question",
    "clarifications_asked",
    "proposal_revisions",
    "outcome",
    "escalation_reason",
    "ticket_sync",
    "step_count",
    "model_calls",
    "tool_calls",
    "tokens",
    "estimated_cost_usd",
    "unauthorized_tool_attempts",
    "proposal_corrections",
    "agent_failures",
)
