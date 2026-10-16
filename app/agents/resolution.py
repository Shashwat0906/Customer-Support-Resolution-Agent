"""Resolution agent: no tools; turns verified facts + policy findings into a structured proposal."""

from __future__ import annotations

from typing import Any

from app.agents import prompts
from app.agents.runtime import AgentTrace, run_agent
from app.schemas.agents import EvidenceSummary, PolicyFinding, ResolutionProposal
from app.tools.registry import ToolContext


def propose(
    category: str,
    complaint: str,
    facts: dict[str, Any],
    evidence: EvidenceSummary,
    policy: PolicyFinding,
    damage_report: dict[str, Any] | None,
    ctx: ToolContext,
) -> tuple[ResolutionProposal, AgentTrace]:
    payload = {
        "category": category,
        "complaint": complaint,
        "order_id": evidence.order_id,
        "facts": facts,
        "evidence": evidence.model_dump(),
        "policy": policy.model_dump(),
        "damage_report": damage_report,
    }
    return run_agent("resolution", prompts.RESOLUTION, payload, ResolutionProposal, ctx)
