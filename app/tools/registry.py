"""Typed tools, per-agent allowlists and dispatch.

* Inputs are Pydantic models; models only ever supply these arguments.
* ``ToolContext`` (customer identity, run) is built by the server from the
  authenticated ticket — the model cannot choose which customer it represents.
* Every agent has an allowlist. A call outside it is refused, audited and
  counted as an unauthorized action attempt. No agent has a write tool: write
  tools are invoked only by deterministic workflow nodes (``actor="workflow"``).
* Failures are structured: ``{"ok": false, "error": {code, message, retryable}}``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.db.session import session_scope
from app.policies.retrieval import PolicyFilters, search_policy
from app.schemas.errors import AppError, ExternalServiceError
from app.services import audit, records
from app.tools import write_tools

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    customer_id: str
    run_id: str
    ticket_id: str
    tool_calls: int = 0
    retries: int = 0
    denied: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def on_retry(self, attempt: int, exc: ExternalServiceError) -> None:
        with self._lock:
            self.retries += 1
        audit.record_now(
            run_id=self.run_id,
            ticket_id=self.ticket_id,
            actor="system",
            event_type="read_retry",
            payload={"service": exc.service, "kind": exc.kind, "attempt": attempt},
        )


# --------------------------------------------------------------------------- inputs


class OrderArgs(BaseModel):
    order_id: str = Field(max_length=40, description="Order ID, e.g. ORD-5001")


class RefundStatusArgs(BaseModel):
    operation_id: str = Field(max_length=40)


class TicketArgs(BaseModel):
    ticket_id: str = Field(max_length=40)


class SearchPolicyArgs(BaseModel):
    query: str = Field(max_length=500, description="What the policy question is about")
    policy_filters: PolicyFilters = Field(default_factory=PolicyFilters)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[[ToolContext, Any], Any]
    write: bool = False

    def schema(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.args.model_json_schema()}


# --------------------------------------------------------------------------- read tools


def _get_order(ctx: ToolContext, a: OrderArgs) -> Any:
    with session_scope() as s:
        return records.get_order(s, ctx.customer_id, a.order_id).model_dump(mode="json")


def _get_return_status(ctx: ToolContext, a: OrderArgs) -> Any:
    with session_scope() as s:
        return records.get_return_status(s, ctx.customer_id, a.order_id).model_dump(mode="json")


def _get_payment_status(ctx: ToolContext, a: OrderArgs) -> Any:
    return records.get_payment_status(ctx.customer_id, a.order_id, on_retry=ctx.on_retry).model_dump(mode="json")


def _get_shipment_status(ctx: ToolContext, a: OrderArgs) -> Any:
    facts = records.get_shipment(ctx.customer_id, a.order_id, on_retry=ctx.on_retry)
    return facts.model_dump(mode="json") if facts else {"order_id": a.order_id, "shipment": None}


def _get_refund_status(ctx: ToolContext, a: RefundStatusArgs) -> Any:
    with session_scope() as s:
        return records.get_refund_operation(s, ctx.customer_id, a.operation_id).model_dump(mode="json")


def _search_policy(ctx: ToolContext, a: SearchPolicyArgs) -> Any:
    with session_scope() as s:
        return search_policy(s, a.query, a.policy_filters).model_dump(mode="json")


def _get_ticket(ctx: ToolContext, a: TicketArgs) -> Any:
    with session_scope() as s:
        return records.get_ticket(s, ctx.customer_id, a.ticket_id).model_dump(mode="json")


# --------------------------------------------------------------------------- write tools (workflow only)


class CreateProposalArgs(BaseModel):
    order_id: str
    amount_minor: int = Field(gt=0)
    reason: str = Field(max_length=1000)


class ExecuteRefundArgs(BaseModel):
    proposal_id: str


class UpdateTicketArgs(BaseModel):
    ticket_id: str
    status: str
    note: str = Field(max_length=8000)


def _create_proposal(ctx: ToolContext, a: CreateProposalArgs) -> Any:
    return write_tools.create_refund_proposal(ctx, a.order_id, a.amount_minor, a.reason)


def _execute_refund(ctx: ToolContext, a: ExecuteRefundArgs) -> Any:
    return write_tools.execute_approved_refund(ctx, a.proposal_id).__dict__


def _update_ticket(ctx: ToolContext, a: UpdateTicketArgs) -> Any:
    return write_tools.update_ticket(ctx, a.ticket_id, a.status, a.note, idempotency_key=f"{ctx.run_id}:{a.status}")


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "get_order",
            "Get an order on the authenticated customer's account: status, items, amounts, delivery.",
            OrderArgs,
            _get_order,
        ),
        Tool(
            "get_return_status",
            "Get the latest return for an order: status, received date, inspection result.",
            OrderArgs,
            _get_return_status,
        ),
        Tool(
            "get_payment_status",
            "Get payment and refund status for an order from the ledger and the payment provider.",
            OrderArgs,
            _get_payment_status,
        ),
        Tool("get_shipment_status", "Get the latest carrier tracking for an order.", OrderArgs, _get_shipment_status),
        Tool(
            "get_refund_status", "Get the status of a refund operation by its ID.", RefundStatusArgs, _get_refund_status
        ),
        Tool(
            "search_policy",
            "Search VoltCart policy documents. Returns cited passages from versions in effect on "
            "`policy_filters.effective_on`, plus missing topics and conflicts.",
            SearchPolicyArgs,
            _search_policy,
        ),
        Tool("get_ticket", "Get a support ticket on the authenticated customer's account.", TicketArgs, _get_ticket),
        Tool(
            "create_refund_proposal",
            "Create a refund proposal for human approval (never issues a refund).",
            CreateProposalArgs,
            _create_proposal,
            write=True,
        ),
        Tool(
            "execute_approved_refund",
            "Execute a refund that a reviewer approved.",
            ExecuteRefundArgs,
            _execute_refund,
            write=True,
        ),
        Tool(
            "update_ticket",
            "Update the support ticket status and add a note.",
            UpdateTicketArgs,
            _update_ticket,
            write=True,
        ),
    )
}

READ_TOOLS = (
    "get_order",
    "get_return_status",
    "get_payment_status",
    "get_shipment_status",
    "get_refund_status",
    "get_ticket",
)

AGENT_TOOLS: dict[str, frozenset[str]] = {
    "classifier": frozenset(),
    "investigation": frozenset(READ_TOOLS),
    "policy": frozenset({"search_policy"}),
    "resolution": frozenset(),
    "responder": frozenset(),
    "single": frozenset((*READ_TOOLS, "search_policy")),
}


def tools_for(agent: str) -> list[Tool]:
    return [TOOLS[n] for n in sorted(AGENT_TOOLS.get(agent, frozenset()))]


def _failure(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, "retryable": retryable}}


def dispatch(agent: str, name: str, arguments: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Run a tool call made by an agent. Never raises for expected failures."""
    with ctx._lock:
        ctx.tool_calls += 1
    allowed = AGENT_TOOLS.get(agent, frozenset())
    if name not in allowed:
        with ctx._lock:
            ctx.denied += 1
        audit.record_now(
            run_id=ctx.run_id,
            ticket_id=ctx.ticket_id,
            actor=f"agent:{agent}",
            event_type="tool_denied",
            payload={"tool": name, "reason": "not in agent allowlist"},
        )
        return _failure("TOOL_NOT_PERMITTED", f"Tool {name!r} is not available to the {agent} agent.")
    tool = TOOLS[name]
    try:
        args = tool.args.model_validate(arguments or {})
    except ValidationError as exc:
        return _failure("INVALID_TOOL_ARGUMENTS", str(exc.errors(include_url=False))[:500])
    try:
        data = tool.fn(ctx, args)
        result = {"ok": True, "data": data}
    except AppError as exc:
        result = _failure(exc.code, exc.message, exc.retryable)
    except Exception:
        log.exception("tool %s failed", name)
        result = _failure("TOOL_ERROR", f"{name} failed unexpectedly.", retryable=False)
    with ctx._lock:
        ctx.results.append({"agent": agent, "tool": name, "arguments": arguments, "result": result})
    audit.record_now(
        run_id=ctx.run_id,
        ticket_id=ctx.ticket_id,
        actor=f"agent:{agent}",
        event_type="tool_called",
        payload={
            "tool": name,
            "arguments": arguments,
            "ok": result["ok"],
            "error": result.get("error", {}).get("code") if not result["ok"] else None,
        },
    )
    return result
