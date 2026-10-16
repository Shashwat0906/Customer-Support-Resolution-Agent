"""Workflow nodes.

Each node returns a partial state update. ``route`` selects the next node.
Side effects that must not repeat live in their own nodes and are idempotent;
the two ``await_*`` nodes contain *only* the ``interrupt()`` call before their
resume handling, because LangGraph re-runs an interrupted node from the start
when it resumes.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from langgraph.types import interrupt
from sqlalchemy import select

from app.agents import investigation, policy, resolution, single, support
from app.agents.runtime import AgentError, AgentTrace
from app.config import get_settings
from app.db.models import Approval, Ticket, TicketMessage, WorkflowRun
from app.db.session import session_scope
from app.policies.retrieval import document_sections
from app.schemas.errors import AppError, Conflict, ExternalServiceError
from app.services import audit, eligibility, records, refunds
from app.services.decisions import decide_refund
from app.services.eligibility import DamageReport
from app.tools import write_tools
from app.tools.registry import ToolContext
from app.workflows import messages
from app.workflows.state import PUBLIC_FIELDS, ResolutionState
from app.workflows.transitions import transition

log = logging.getLogger(__name__)
Update = dict[str, Any]


# --------------------------------------------------------------------------- helpers


def ctx_for(state: ResolutionState) -> ToolContext:
    return ToolContext(customer_id=state.authenticated_customer_id, run_id=state.run_id, ticket_id=state.ticket_id)


def _counters(trace: AgentTrace, ctx: ToolContext) -> Update:
    return {
        **trace.counters(),
        "unauthorized_tool_attempts": ctx.denied,
        "retry_counts": {"read_retries": ctx.retries} if ctx.retries else {},
    }


def _event(
    state: ResolutionState, event_type: str, payload: dict[str, Any] | None = None, actor: str = "workflow"
) -> None:
    audit.record_now(
        run_id=state.run_id, ticket_id=state.ticket_id, actor=actor, event_type=event_type, payload=payload
    )


def _error(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "at": datetime.now(UTC).isoformat(), **details}


def _set_run_status(run_id: str, status: str) -> None:
    with session_scope() as s:
        run = s.get(WorkflowRun, run_id, with_for_update=True)
        if run is not None:
            transition(s, run, status)


def _set_ticket(ticket_id: str, **fields: Any) -> None:
    with session_scope() as s:
        t = s.get(Ticket, ticket_id)
        for k, v in fields.items():
            if v is not None:
                setattr(t, k, v)


def _post_message(state: ResolutionState, body: str) -> None:
    """Idempotent: a node re-run (resume/crash) does not duplicate the message."""
    with session_scope() as s:
        exists = s.scalar(
            select(TicketMessage.id)
            .where(
                TicketMessage.run_id == state.run_id, TicketMessage.author_type == "agent", TicketMessage.body == body
            )
            .limit(1)
        )
        if not exists:
            s.add(TicketMessage(ticket_id=state.ticket_id, run_id=state.run_id, author_type="agent", body=body))


def write_snapshot(state: dict[str, Any]) -> None:
    snap = {k: state.get(k) for k in PUBLIC_FIELDS}
    with session_scope() as s:
        run = s.get(WorkflowRun, state["run_id"])
        if run is not None:
            run.state_snapshot = _jsonable(snap)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def node(
    name: str, *, guarded: bool = True, parallel: bool = False
) -> Callable[..., Callable[[ResolutionState], Update]]:
    """Wrap a node: step/cost limits, error capture, progress snapshot."""

    def deco(fn: Callable[[ResolutionState], Update]) -> Callable[[ResolutionState], Update]:
        @wraps(fn)
        def wrapper(state: ResolutionState) -> Update:
            settings = get_settings()
            if guarded and (
                state.step_count >= settings.max_workflow_steps
                or state.estimated_cost_usd >= settings.max_cost_usd_per_run
            ):
                err = _error(
                    "STEP_OR_COST_LIMIT",
                    f"Stopped at step {state.step_count} "
                    f"(limit {settings.max_workflow_steps}) / cost ${state.estimated_cost_usd:.4f}",
                )
                _event(state, "limit_reached", err)
                if parallel:
                    return {"step_count": 1, "error_details": [err], "agent_failures": ["STEP_OR_COST_LIMIT"]}
                return {
                    "step_count": 1,
                    "error_details": [err],
                    "route": "escalate",
                    "escalation_reason": "step_or_cost_limit",
                }
            update = fn(state)
            update["step_count"] = update.get("step_count", 0) + 1
            try:
                merged = state.model_dump()
                for k, v in update.items():
                    merged[k] = v if k not in ("step_count", "model_calls", "tool_calls", "tokens") else merged[k] + v
                write_snapshot(merged)
            except Exception:  # snapshots are for display; never fail the workflow on them
                log.exception("snapshot failed in %s", name)
            return update

        wrapper.__name__ = name
        return wrapper

    return deco


# --------------------------------------------------------------------------- intake & order resolution


@node("intake")
def intake(state: ResolutionState) -> Update:
    ctx = ctx_for(state)
    try:
        result, trace = support.classify(state.customer_messages, ctx)
    except AgentError as exc:
        return {
            **_counters(exc.trace, ctx),
            "route": "escalate",
            "escalation_reason": f"classifier_failed:{exc.code}",
            "error_details": [_error(exc.code, str(exc), agent="classifier")],
        }
    cls = result.model_dump()
    category = cls["category"]
    if category == "other" and state.complaint_category not in (None, "other"):
        category = state.complaint_category  # a follow-up answer keeps the original topic
    previous = state.damage_report or {}
    damage = {
        "description": cls["damage_description"] or previous.get("description"),
        "photos_available": cls["photos_available"]
        if cls["photos_available"] is not None
        else previous.get("photos_available"),
        "preference": cls["preference"] or previous.get("preference"),
    }
    _event(
        state,
        "classified",
        {"category": category, "order_reference": cls["order_reference"], "product_mentions": cls["product_mentions"]},
        actor="agent:classifier",
    )
    _set_ticket(state.ticket_id, category=category)
    return {
        **_counters(trace, ctx),
        "classification": cls,
        "complaint_category": category,
        "damage_report": damage if category == "damaged_item" else None,
        "route": "locate_order",
    }


_MENTION_MATCH = {
    "headphones": "headphones",
    "earbuds": "earbuds",
    "speaker": "speaker",
    "smartwatch": "smartwatch",
    "smartphone": "smartphone",
    "laptop": "laptop",
    "tablet": "tablet",
    "charger": "charger",
    "cable": "cable",
    "keyboard": "keyboard",
    "mouse": "mouse",
    "monitor": "monitor",
}


@node("locate_order")
def locate_order(state: ResolutionState) -> Update:
    cls = state.classification or {}
    reference = state.order_id or cls.get("order_reference")
    with session_scope() as s:
        if reference:
            try:
                records.load_owned_order(s, state.authenticated_customer_id, reference)
            except AppError:
                # Same response whether the order is someone else's or does not exist.
                _event(state, "order_access_denied", {"reference": reference})
                q = messages.clarification_question(["order_id"], reference=reference)
                return {
                    "order_id": None,
                    "missing_information": ["order_id"],
                    "clarification_question": q,
                    "route": "ask_customer",
                }
            _event(state, "order_located", {"order_id": reference, "source": "customer_reference"})
            _set_ticket(state.ticket_id, order_id=reference)
            return {"order_id": reference, "route": "load_order_facts"}
        orders = records.list_customer_orders(s, state.authenticated_customer_id)
    if state.complaint_category == "other":
        q = (
            "Could you tell me a bit more about what you need help with (an order's delivery, a refund, or a damaged "
            "item) and which order it is about?"
        )
        return {
            "missing_information": ["issue_details", "order_id"],
            "clarification_question": q,
            "route": "ask_customer",
        }
    mentions = cls.get("product_mentions") or []
    matches = [
        o for o in orders if any(_MENTION_MATCH.get(m, m) in " ".join(o.product_names).lower() for m in mentions)
    ]
    chosen = matches[0] if len(matches) == 1 else (orders[0] if len(orders) == 1 else None)
    if chosen is None:
        listing = [{"order_id": o.order_id, "items": o.product_names} for o in (matches or orders)]
        q = messages.clarification_question(["order_id"], orders=listing)
        return {"missing_information": ["order_id"], "clarification_question": q, "route": "ask_customer"}
    _event(
        state,
        "order_located",
        {
            "order_id": chosen.order_id,
            "source": "inferred_from_product_mention" if matches else "only_order_on_account",
        },
    )
    _set_ticket(state.ticket_id, order_id=chosen.order_id)
    return {"order_id": chosen.order_id, "route": "load_order_facts"}


@node("ask_customer")
def ask_customer(state: ResolutionState) -> Update:
    if state.clarifications_asked >= get_settings().max_clarifications:
        return {"route": "escalate", "escalation_reason": "clarification_limit"}
    question = state.clarification_question or messages.clarification_question(state.missing_information)
    _post_message(state, question)
    _set_ticket(state.ticket_id, status="awaiting_customer")
    _event(state, "clarification_requested", {"question": question, "missing": state.missing_information})
    return {"clarification_question": question, "route": "await_customer"}


def await_customer(state: ResolutionState) -> Update:
    # Only the interrupt precedes resume handling (this node re-runs on resume).
    value = interrupt({"type": "awaiting_customer", "question": state.clarification_question})
    text = str((value or {}).get("customer_message", "")).strip()
    return {
        "customer_messages": [*state.customer_messages, text],
        "customer_message": text,
        "clarifications_asked": state.clarifications_asked + 1,
        "missing_information": [],
        "clarification_question": None,
        "route": "intake",
        "step_count": 1,
    }


# --------------------------------------------------------------------------- facts, agents


@node("load_order_facts")
def load_order_facts(state: ResolutionState) -> Update:
    assert state.order_id
    with session_scope() as s:
        order = records.get_order(s, state.authenticated_customer_id, state.order_id).model_dump(mode="json")
    route = (
        "decide"
        if state.complaint_category == "other"
        else ("single_agent" if state.agent_mode == "single" else "fanout")
    )
    return {"verified_facts": {"order": order}, "route": route}


def _complaint(state: ResolutionState) -> str:
    return "\n".join(state.customer_messages)


@node("investigate", parallel=True)
def investigate(state: ResolutionState) -> Update:
    ctx = ctx_for(state)
    try:
        evidence, trace = investigation.investigate(
            state.order_id or "", state.complaint_category or "other", _complaint(state), ctx
        )
    except AgentError as exc:
        return {
            **_counters(exc.trace, ctx),
            "agent_failures": [f"investigation:{exc.code}"],
            "error_details": [_error(exc.code, str(exc), agent="investigation")],
        }
    results = {r["tool"]: r["result"].get("data") for r in ctx.results if r["result"]["ok"]}
    return {
        **_counters(trace, ctx),
        "evidence": evidence.model_dump(),
        "investigation_summary": evidence.summary,
        "investigation_results": results,
    }


@node("retrieve_policy", parallel=True)
def retrieve_policy(state: ResolutionState) -> Update:
    ctx = ctx_for(state)
    try:
        finding, trace = policy.find_policy(
            state.complaint_category or "other", _complaint(state), state.verified_facts.get("order", {}), ctx
        )
    except AgentError as exc:
        return {
            **_counters(exc.trace, ctx),
            "agent_failures": [f"policy:{exc.code}"],
            "error_details": [_error(exc.code, str(exc), agent="policy")],
        }
    return {
        **_counters(trace, ctx),
        "policy_finding": finding.model_dump(),
        "policy_references": [c.model_dump() for c in finding.citations],
    }


@node("resolve")
def resolve(state: ResolutionState) -> Update:
    if state.agent_failures or state.evidence is None or state.policy_finding is None:
        return {"route": "decide"}
    ctx = ctx_for(state)
    from app.schemas.agents import EvidenceSummary, PolicyFinding

    facts = {
        "order": state.investigation_results.get("get_order") or state.verified_facts.get("order"),
        "return": state.investigation_results.get("get_return_status"),
        "payment": state.investigation_results.get("get_payment_status"),
        "shipment": state.investigation_results.get("get_shipment_status"),
    }
    try:
        draft, trace = resolution.propose(
            state.complaint_category or "other",
            _complaint(state),
            facts,
            EvidenceSummary.model_validate(state.evidence),
            PolicyFinding.model_validate(state.policy_finding),
            state.damage_report,
            ctx,
        )
    except AgentError as exc:
        return {
            **_counters(exc.trace, ctx),
            "agent_failures": [f"resolution:{exc.code}"],
            "error_details": [_error(exc.code, str(exc), agent="resolution")],
            "route": "decide",
        }
    return {**_counters(trace, ctx), "resolution_draft": draft.model_dump(), "route": "decide"}


@node("single_agent")
def single_agent(state: ResolutionState) -> Update:
    ctx = ctx_for(state)
    try:
        out, trace = single.resolve(
            state.order_id or "",
            state.complaint_category or "other",
            _complaint(state),
            state.verified_facts.get("order", {}),
            state.damage_report,
            ctx,
        )
    except AgentError as exc:
        return {
            **_counters(exc.trace, ctx),
            "agent_failures": [f"single:{exc.code}"],
            "error_details": [_error(exc.code, str(exc), agent="single")],
            "route": "decide",
        }
    return {
        **_counters(trace, ctx),
        "evidence": out.evidence.model_dump(),
        "investigation_summary": out.evidence.summary,
        "policy_finding": out.policy.model_dump(),
        "policy_references": [c.model_dump() for c in out.policy.citations],
        "resolution_draft": out.proposal.model_dump(),
        "route": "decide",
    }


# --------------------------------------------------------------------------- deterministic decision

EXPECTED_ACTION = {
    "eligible": "refund",
    "processing": "answer_only",
    "blocked": "answer_only",
    "answer": "answer_only",
    "needs_info": "request_information",
    "escalate": "escalate",
}


def _authoritative_facts(
    state: ResolutionState, ctx: ToolContext, include_payment: bool = True
) -> tuple[dict[str, Any], Any, Any]:
    """Re-read the facts the decision depends on directly from the systems of record."""
    assert state.order_id
    with session_scope() as s:
        order = records.get_order(s, state.authenticated_customer_id, state.order_id)
        ret = records.get_return_status(s, state.authenticated_customer_id, state.order_id)
    facts: dict[str, Any] = {"order": order.model_dump(mode="json"), "return": ret.model_dump(mode="json")}
    payment = None
    if include_payment:
        payment = records.get_payment_status(state.authenticated_customer_id, state.order_id, on_retry=ctx.on_retry)
        facts["payment"] = payment.model_dump(mode="json")
    return facts, order, payment


def _governing_citations(state: ResolutionState, document_id: str | None) -> tuple[list[dict[str, Any]], bool]:
    """Keep model citations only from applicable documents; ensure the governing document is cited."""
    cited = [c for c in state.policy_references if isinstance(c, dict)]
    if document_id is None:
        return cited, False
    key = document_id.rsplit("-v", 1)[0]
    kept = [c for c in cited if not (c["document_id"].startswith(key) and c["document_id"] != document_id)]
    if len(kept) != len(cited):
        _event(
            state,
            "citation_rejected",
            {
                "reason": "cites a policy version not in effect",
                "dropped": [c["document_id"] for c in cited if c not in kept],
            },
        )
    if any(c["document_id"] == document_id for c in kept):
        return kept, False
    with session_scope() as s:
        sections = document_sections(s, document_id)
    added = [
        {
            "document_id": p.document_id,
            "version": p.version,
            "section_ref": p.section_ref,
            "quote": p.content[:300],
            "added_by": "system",
        }
        for p in sections[1:4]
    ]
    _event(state, "citation_added", {"document_id": document_id, "sections": [a["section_ref"] for a in added]})
    return kept + added, True


@node("decide")
def decide(state: ResolutionState) -> Update:
    settings = get_settings()
    ctx = ctx_for(state)
    if state.agent_failures:
        return {"route": "escalate", "escalation_reason": "agent_failure:" + ",".join(state.agent_failures)}
    if state.complaint_category == "other":
        return {
            "missing_information": ["issue_details"],
            "route": "ask_customer",
            "clarification_question": messages.clarification_question(["issue_details"]),
        }
    now = datetime.now(UTC)
    try:
        facts, order, payment = _authoritative_facts(
            state, ctx, include_payment=state.complaint_category != "order_status"
        )
        if state.complaint_category == "order_status":
            shipment = records.get_shipment(
                state.authenticated_customer_id, state.order_id or "", on_retry=ctx.on_retry
            )
            facts["shipment"] = shipment.model_dump(mode="json") if shipment else None
            decision = eligibility.evaluate_shipping(order, shipment, now)
        else:
            if payment is None or not payment.provider_reachable:
                _event(state, "provider_unavailable", {"service": "payments"})
                return {
                    "verified_facts": facts,
                    "route": "escalate",
                    "escalation_reason": "payment_provider_unavailable",
                    "retry_counts": {"read_retries": ctx.retries} if ctx.retries else {},
                    "error_details": [_error("PAYMENTS_UNAVAILABLE", "Payment provider unreachable after retries")],
                }
            with session_scope() as s:
                if state.complaint_category == "damaged_item":
                    dr = state.damage_report or {}
                    decision, _ = decide_refund(
                        s,
                        state.authenticated_customer_id,
                        state.order_id or "",
                        basis="damage",
                        provider_view=payment,
                        now=now,
                        reported_at=state.reported_at or now,
                        damage_report=DamageReport(**dr),
                    )
                else:
                    decision, _ = decide_refund(
                        s,
                        state.authenticated_customer_id,
                        state.order_id or "",
                        basis="return",
                        provider_view=payment,
                        now=now,
                    )
    except ExternalServiceError as exc:
        _event(state, "provider_unavailable", {"service": exc.service, "kind": exc.kind})
        return {
            "route": "escalate",
            "escalation_reason": f"{exc.service}_unavailable",
            "retry_counts": {"read_retries": ctx.retries} if ctx.retries else {},
            "error_details": [_error(exc.code, exc.message)],
        }

    d = decision.model_dump()
    update: Update = {
        "verified_facts": facts,
        "decision": d,
        "retry_counts": {"read_retries": ctx.retries} if ctx.retries else {},
    }
    finding = state.policy_finding or {}
    citations, added = _governing_citations(state, decision.policy_document_id)
    update["policy_references"] = citations
    _event(
        state,
        "decision_made",
        {
            "kind": d["kind"],
            "disposition": d["disposition"],
            "reason_code": d["reason_code"],
            "amount_minor": d["amount_minor"],
            "policy": d["policy_document_id"],
            "citation_added_by_system": added,
        },
    )

    draft = state.resolution_draft or {}
    expected = EXPECTED_ACTION[decision.disposition]
    if draft and (
        draft.get("action") != expected
        or (
            expected == "refund"
            and (draft.get("amount_minor") != decision.amount_minor or draft.get("order_id") != state.order_id)
        )
    ):
        update["proposal_corrections"] = 1
        _event(
            state,
            "proposal_corrected",
            {
                "draft_action": draft.get("action"),
                "draft_amount": draft.get("amount_minor"),
                "draft_order": draft.get("order_id"),
                "permitted_action": expected,
                "permitted_amount": decision.amount_minor,
            },
        )

    if (
        decision.reason_code in ("no_applicable_policy", "conflicting_policy")
        or finding.get("missing_policy")
        or finding.get("conflict")
    ):
        reason = (
            "conflicting_policy"
            if (finding.get("conflict") or decision.reason_code == "conflicting_policy")
            else "no_applicable_policy"
        )
        return {**update, "route": "escalate", "escalation_reason": reason}
    if decision.disposition == "eligible":
        if state.proposal_revisions > settings.max_proposal_revisions:
            return {**update, "route": "escalate", "escalation_reason": "proposal_revision_limit"}
        return {**update, "route": "propose"}
    if decision.disposition == "needs_info":
        return {
            **update,
            "missing_information": decision.missing_information,
            "route": "ask_customer",
            "clarification_question": messages.clarification_question(decision.missing_information),
        }
    if decision.disposition == "escalate":
        return {**update, "route": "escalate", "escalation_reason": decision.reason_code}
    return {**update, "outcome": "answered", "route": "respond"}


# --------------------------------------------------------------------------- approval & execution


@node("propose")
def propose(state: ResolutionState) -> Update:
    ctx = ctx_for(state)
    d = state.decision or {}
    draft = state.resolution_draft or {}
    damage = (
        DamageReport(**state.damage_report)
        if state.complaint_category == "damaged_item" and state.damage_report
        else None
    )
    try:
        result = write_tools.create_refund_proposal(
            ctx,
            state.order_id or "",
            int(d["amount_minor"]),
            draft.get("reason") or d.get("explanation", ""),
            basis="damage" if damage else "return",
            damage_report=damage,
            reported_at=state.reported_at if damage else None,
            policy_references=state.policy_references,
            evidence_references=[f["ref"] for f in (state.evidence or {}).get("facts", [])],
        )
        proposal_id, approval_id = result["proposal_id"], result["approval_id"]
    except Conflict as exc:  # re-run of this node: reuse the active proposal
        proposal_id = exc.details["proposal_id"]
        with session_scope() as s:
            approval_id = s.scalar(
                select(Approval.id)
                .where(Approval.proposal_id == proposal_id)
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
    except AppError as exc:
        return {
            "route": "escalate",
            "escalation_reason": f"proposal_rejected:{exc.code}",
            "error_details": [_error(exc.code, exc.message)],
        }
    _set_ticket(state.ticket_id, status="awaiting_approval")
    _event(
        state,
        "approval_requested",
        {"proposal_id": proposal_id, "approval_id": approval_id, "amount_minor": d["amount_minor"]},
    )
    return {
        "proposed_action_id": proposal_id,
        "approval_id": approval_id,
        "execution_status": "awaiting_approval",
        "proposal_revisions": state.proposal_revisions + 1,
        "route": "await_approval",
    }


def await_approval(state: ResolutionState) -> Update:
    # Only the interrupt precedes resume handling (this node re-runs on resume).
    interrupt({"type": "awaiting_approval", "approval_id": state.approval_id})
    # The decision is read from the database; the resume payload carries no authority.
    with session_scope() as s:
        approval = s.get(Approval, state.approval_id)
        decision = approval.decision if approval else "missing"
    update: Update = {"step_count": 1}
    if decision == "approved":
        return {**update, "execution_status": "approved", "route": "execute_refund"}
    if decision == "rejected":
        return {**update, "execution_status": "rejected", "outcome": "refund_rejected", "route": "respond"}
    if decision == "expired":
        return {**update, "execution_status": "approval_expired", "outcome": "approval_expired", "route": "respond"}
    if decision == "pending":
        return {**update, "route": "await_approval"}
    return {**update, "route": "escalate", "escalation_reason": f"approval_{decision}"}


@node("execute_refund")
def execute_refund(state: ResolutionState) -> Update:
    ctx = ctx_for(state)
    _set_run_status(state.run_id, "executing")
    outcome = write_tools.execute_approved_refund(ctx, state.proposed_action_id or "")
    _event(
        state,
        "refund_execution_result",
        {"status": outcome.status, "operation_id": outcome.operation_id, "reason_code": outcome.reason_code},
    )
    update: Update = {"execution_status": outcome.status, "external_result_reference": outcome.provider_reference}
    if outcome.status == "invalidated":
        _set_run_status(state.run_id, "running")
        if state.proposal_revisions > get_settings().max_proposal_revisions:
            return {**update, "route": "escalate", "escalation_reason": "approval_invalidated_repeatedly"}
        return {**update, "route": "decide", "proposed_action_id": None, "approval_id": None}
    if outcome.status == "not_authorized":
        return {**update, "route": "escalate", "escalation_reason": f"execution_blocked:{outcome.reason_code}"}
    if outcome.status == "failed":
        return {**update, "route": "escalate", "escalation_reason": f"refund_failed:{outcome.reason_code}"}
    return {**update, "route": "verify_refund"}


@node("verify_refund", guarded=False)
def verify_refund(state: ResolutionState) -> Update:
    settings = get_settings()
    op = refunds.operation_for_proposal(state.proposed_action_id or "")
    if op is None:
        return {"route": "escalate", "escalation_reason": "refund_operation_missing"}
    status, ref, attempts = op.status, op.provider_reference, 0
    if status not in ("succeeded", "failed"):
        _set_run_status(state.run_id, "reconciling")
    while status not in ("succeeded", "failed") and attempts < settings.max_reconcile_attempts:
        attempts += 1
        result = refunds.continue_operation(op.id)
        status, ref = result.status, result.provider_reference or ref
        if status not in ("succeeded", "failed"):
            time.sleep(settings.retry_backoff_seconds * (2 ** (attempts - 1)))
    with session_scope() as s:  # evidence shown to people must reflect the ledger after execution
        order_now = records.get_order(s, state.authenticated_customer_id, state.order_id or "").model_dump(mode="json")
    update: Update = {
        "verified_facts": {**state.verified_facts, "order": order_now},
        "execution_status": status,
        "external_result_reference": ref,
        "retry_counts": {"refund_reconcile": attempts} if attempts else {},
    }
    _event(state, "refund_verified", {"status": status, "provider_reference": ref, "reconcile_attempts": attempts})
    if status == "succeeded":
        return {**update, "outcome": "refund_succeeded", "route": "respond"}
    if status == "processing":
        return {**update, "outcome": "refund_processing", "route": "respond"}
    if status == "failed":
        return {**update, "route": "escalate", "escalation_reason": "refund_failed"}
    return {
        **update,
        "route": "escalate",
        "escalation_reason": f"refund_outcome_unknown:{status}",
        "error_details": [
            _error(
                "REFUND_OUTCOME_UNKNOWN",
                "Provider outcome could not be confirmed; the reserved amount stays held pending reconciliation.",
                operation_id=op.id,
            )
        ],
    }


# --------------------------------------------------------------------------- terminal path


@node("escalate", guarded=False)
def escalate(state: ResolutionState) -> Update:
    _event(state, "escalated", {"reason": state.escalation_reason})
    return {"outcome": "escalated", "route": "respond"}


@node("respond", guarded=False)
def respond(state: ResolutionState) -> Update:
    draft = messages.draft_response(state.model_dump())
    facts = state.verified_facts or {}
    order = facts.get("order") or {}
    allowed = {
        int(v)
        for v in (
            (state.decision or {}).get("amount_minor"),
            order.get("paid_amount_minor"),
            order.get("refunded_amount_minor"),
        )
        if v
    }
    refs = [state.external_result_reference] if state.outcome == "refund_succeeded" else []
    message, update = draft, {}
    ctx = ctx_for(state)
    try:
        result, trace = support.respond(
            {"outcome": state.outcome, "template_message": draft, "order_id": state.order_id}, ctx
        )
        update.update(_counters(trace, ctx))
        problems = messages.check_grounding(
            result.message, outcome=state.outcome, allowed_amounts=allowed, required_refs=[r for r in refs if r]
        )
        if problems:
            _event(state, "response_guard_corrected", {"problems": problems})
        else:
            message = result.message
    except AgentError as exc:
        update.update(_counters(exc.trace, ctx))
        _event(state, "responder_failed", {"code": exc.code})
    _post_message(state, message)
    _event(state, "response_sent", {"outcome": state.outcome, "message": message})
    return {**update, "final_response": message, "route": "update_ticket"}


TICKET_STATUS = {
    "answered": "resolved",
    "refund_succeeded": "resolved",
    "refund_processing": "in_progress",
    "refund_rejected": "resolved",
    "approval_expired": "escalated",
    "escalated": "escalated",
}


@node("update_ticket", guarded=False)
def update_ticket(state: ResolutionState) -> Update:
    status = TICKET_STATUS.get(state.outcome or "", "escalated")
    note = f"[{state.outcome}] {state.final_response}" + (
        f" Escalation reason: {state.escalation_reason}." if state.escalation_reason else ""
    )
    sync = write_tools.update_ticket(
        ctx_for(state),
        state.ticket_id,
        status,
        note,
        idempotency_key=f"{state.run_id}:final:{state.outcome}",
        category=state.complaint_category,
        order_id=state.order_id,
    )
    update: Update = {
        "ticket_sync": sync,
        "route": "end",
        "retry_counts": {"ticket_update": sync["attempts"] - 1} if sync["attempts"] > 1 else {},
    }
    if not sync["synced"]:
        update["error_details"] = [
            _error(
                "TICKET_SYNC_FAILED",
                "External ticket update failed after retries; the "
                "outcome is recorded locally and the sync will be retried.",
                attempts=sync["attempts"],
            )
        ]
    return update
