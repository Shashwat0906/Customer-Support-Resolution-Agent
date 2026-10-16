"""Workflow run endpoints: start (202 + durable job), status, events, customer replies."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, get_principal, require_customer
from app.config import get_settings
from app.db.models import ActionProposal, Approval, AuditEvent, Ticket, WorkflowRun
from app.db.session import get_db
from app.schemas.api import CustomerMessage, EventOut, RunCreate, RunCreated, RunOut
from app.schemas.errors import Conflict, NotFound
from app.services import audit, jobs, tickets
from app.workflows.transitions import TERMINAL, transition

router = APIRouter(tags=["runs"])

# Event types whose payload is shown to customers (others show type + time only).
CUSTOMER_VISIBLE_PAYLOADS = {
    "classified",
    "order_located",
    "clarification_requested",
    "customer_replied",
    "refund_succeeded",
    "response_sent",
    "escalated",
}


def _run_for(db: Session, principal: Principal, run_id: str) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise NotFound("RUN_NOT_FOUND", "Workflow run not found.")
    if principal.kind == "customer":
        ticket = db.get(Ticket, run.ticket_id)
        if ticket is None or ticket.customer_id != principal.id:
            raise NotFound("RUN_NOT_FOUND", "Workflow run not found.")
    return run


def run_out(db: Session, run: WorkflowRun, principal: Principal) -> RunOut:
    pending = db.scalar(
        select(Approval.id)
        .join(ActionProposal, ActionProposal.id == Approval.proposal_id)
        .where(ActionProposal.run_id == run.id, Approval.decision == "pending")
        .limit(1)
    )
    state = dict(run.state_snapshot or {})
    if principal.kind == "customer":
        state.pop("error_details", None)
        state.pop("retry_counts", None)
    return RunOut(
        run_id=run.id,
        ticket_id=run.ticket_id,
        status=run.status,
        outcome=run.outcome,
        agent_mode=run.agent_mode,
        started_at=run.started_at,
        completed_at=run.completed_at,
        final_response=run.final_response,
        error_code=run.error_code,
        state=state,
        metrics=run.metrics if principal.is_reviewer else None,
        pending_approval_id=pending,
    )


@router.post(
    "/tickets/{ticket_id}/runs",
    status_code=202,
    response_model=RunCreated,
    summary="Start a resolution workflow (asynchronous; poll the status URL)",
)
def start_run(
    ticket_id: str,
    response: Response,
    body: RunCreate | None = Body(default=None),
    principal: Principal = Depends(require_customer),
    db: Session = Depends(get_db),
) -> RunCreated:
    ticket = tickets.get_ticket_for(db, principal, ticket_id)
    db.refresh(ticket, with_for_update=True)
    active = db.scalar(
        select(WorkflowRun).where(WorkflowRun.ticket_id == ticket.id, WorkflowRun.status.not_in(TERMINAL)).limit(1)
    )
    if active is not None:
        raise Conflict(
            "RUN_ALREADY_ACTIVE", "A workflow is already running for this ticket.", details={"run_id": active.id}
        )
    mode = (body.agent_mode if body and body.agent_mode else None) or get_settings().agent_mode
    run = WorkflowRun(ticket_id=ticket.id, status="queued", agent_mode=mode, thread_id="pending")
    db.add(run)
    db.flush()
    run.thread_id = f"thread_{run.id}"
    ticket.status = "in_progress"
    jobs.enqueue(db, run.id, "start")
    audit.record(
        db,
        run_id=run.id,
        ticket_id=ticket.id,
        actor=principal.actor,
        event_type="run_queued",
        payload={"agent_mode": mode},
    )
    db.commit()
    response.headers["Location"] = f"/runs/{run.id}"
    return RunCreated(
        run_id=run.id,
        ticket_id=ticket.id,
        status=run.status,
        agent_mode=mode,
        status_url=f"/runs/{run.id}",
        events_url=f"/runs/{run.id}/events",
    )


@router.get("/runs/{run_id}", response_model=RunOut, summary="Run status, verified state and output")
def get_run(run_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> RunOut:
    return run_out(db, _run_for(db, principal, run_id), principal)


@router.get("/runs/{run_id}/events", response_model=list[EventOut], summary="Redacted event timeline")
def get_events(
    run_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db), after_id: int = 0
) -> list[EventOut]:
    run = _run_for(db, principal, run_id)
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.run_id == run.id, AuditEvent.id > after_id).order_by(AuditEvent.id)
    ).all()
    out = []
    for e in events:
        payload: dict[str, Any] | None = e.redacted_payload
        actor = e.actor
        if principal.kind == "customer":
            payload = payload if e.event_type in CUSTOMER_VISIBLE_PAYLOADS else None
            actor = "reviewer" if actor.startswith("reviewer:") else actor
        out.append(EventOut(id=e.id, created_at=e.created_at, actor=actor, event_type=e.event_type, payload=payload))
    return out


@router.post(
    "/runs/{run_id}/messages",
    status_code=202,
    response_model=RunOut,
    summary="Provide additional information requested by the workflow",
)
def post_message(
    run_id: str, body: CustomerMessage, principal: Principal = Depends(require_customer), db: Session = Depends(get_db)
) -> RunOut:
    run = _run_for(db, principal, run_id)
    db.refresh(run, with_for_update=True)
    if run.status != "awaiting_customer":
        raise Conflict(
            "NOT_AWAITING_CUSTOMER",
            "This workflow is not waiting for more information.",
            details={"status": run.status},
        )
    tickets.add_message(db, run.ticket_id, "customer", principal.id, body.message.strip(), run_id=run.id)
    transition(db, run, "queued")
    jobs.enqueue(db, run.id, "resume", {"customer_message": body.message.strip()})
    audit.record(
        db,
        run_id=run.id,
        ticket_id=run.ticket_id,
        actor=principal.actor,
        event_type="customer_replied",
        payload={"message": body.message[:500]},
    )
    db.commit()
    return run_out(db, run, principal)
