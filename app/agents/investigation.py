"""Investigation agent: read-only tools, structured evidence summary. Has no refund or write tool."""

from __future__ import annotations

from app.agents import prompts
from app.agents.runtime import AgentTrace, run_agent
from app.schemas.agents import EvidenceSummary
from app.tools.registry import ToolContext


def investigate(order_id: str, category: str, complaint: str, ctx: ToolContext) -> tuple[EvidenceSummary, AgentTrace]:
    return run_agent(
        "investigation",
        prompts.INVESTIGATION,
        {"order_id": order_id, "category": category, "complaint": complaint},
        EvidenceSummary,
        ctx,
    )
