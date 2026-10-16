"""Policy agent: search_policy only; returns citations, versions and interpretation."""

from __future__ import annotations

from typing import Any

from app.agents import prompts
from app.agents.runtime import AgentTrace, run_agent
from app.schemas.agents import PolicyFinding
from app.tools.registry import ToolContext


def find_policy(
    category: str, complaint: str, order: dict[str, Any], ctx: ToolContext
) -> tuple[PolicyFinding, AgentTrace]:
    payload = {
        "category": category,
        "complaint": complaint,
        "order": {k: order.get(k) for k in ("order_id", "status", "placed_at", "delivered_at")}
        | {"categories": sorted({i["category"] for i in order.get("items", [])})},
    }
    return run_agent("policy", prompts.POLICY, payload, PolicyFinding, ctx)
