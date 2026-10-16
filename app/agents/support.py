"""Classifier and responder agents (no tools)."""

from __future__ import annotations

from typing import Any

from app.agents import prompts
from app.agents.runtime import AgentTrace, run_agent
from app.schemas.agents import Classification, CustomerResponse
from app.tools.registry import ToolContext


def classify(messages: list[str], ctx: ToolContext) -> tuple[Classification, AgentTrace]:
    return run_agent("classifier", prompts.CLASSIFIER, {"messages": messages}, Classification, ctx)


def respond(payload: dict[str, Any], ctx: ToolContext) -> tuple[CustomerResponse, AgentTrace]:
    return run_agent("responder", prompts.RESPONDER, payload, CustomerResponse, ctx)
