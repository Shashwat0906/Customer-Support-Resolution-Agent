"""Single tool-using agent baseline: all read tools + search_policy, one structured output."""

from __future__ import annotations

from typing import Any

from app.agents import prompts
from app.agents.runtime import AgentTrace, run_agent
from app.schemas.agents import SingleAgentOutput
from app.tools.registry import ToolContext


def resolve(
    order_id: str,
    category: str,
    complaint: str,
    order: dict[str, Any],
    damage_report: dict[str, Any] | None,
    ctx: ToolContext,
) -> tuple[SingleAgentOutput, AgentTrace]:
    payload = {
        "order_id": order_id,
        "category": category,
        "complaint": complaint,
        "damage_report": damage_report,
        "order": {k: order.get(k) for k in ("order_id", "status", "placed_at", "delivered_at")},
    }
    return run_agent("single", prompts.SINGLE, payload, SingleAgentOutput, ctx)
