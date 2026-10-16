"""Ticket endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, get_principal, require_customer
from app.db.models import Ticket, WorkflowRun
from app.db.session import get_db
from app.schemas.api import TicketCreate, TicketMessageOut, TicketOut
from app.services import tickets

router = APIRouter(prefix="/tickets", tags=["tickets"])


def ticket_out(ticket: Ticket, with_messages: bool = False) -> TicketOut:
    latest: WorkflowRun | None = ticket.runs[-1] if ticket.runs else None
    return TicketOut(
        ticket_id=ticket.id,
        customer_id=ticket.customer_id,
        order_id=ticket.order_id,
        category=ticket.category,
        status=ticket.status,
        summary=ticket.summary,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        latest_run_id=latest.id if latest else None,
        latest_run_status=latest.status if latest else None,
        messages=[
            TicketMessageOut(author_type=m.author_type, body=m.body, created_at=m.created_at) for m in ticket.messages
        ]
        if with_messages
        else [],
    )


@router.post("", status_code=201, response_model=TicketOut, summary="Create a customer support ticket")
def create_ticket(
    body: TicketCreate, principal: Principal = Depends(require_customer), db: Session = Depends(get_db)
) -> TicketOut:
    ticket = tickets.create_ticket(db, principal, body.message, body.order_id)
    db.commit()
    db.refresh(ticket)
    return ticket_out(ticket, with_messages=True)


@router.get("", response_model=list[TicketOut], summary="List tickets (own tickets for customers, all for reviewers)")
def list_tickets(
    principal: Principal = Depends(get_principal),
    db: Session = Depends(get_db),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[TicketOut]:
    stmt = select(Ticket).where(~Ticket.id.startswith("tkt_hist_")).order_by(Ticket.created_at.desc()).limit(limit)
    if principal.kind == "customer":
        stmt = stmt.where(Ticket.customer_id == principal.id)
    if status:
        stmt = stmt.where(Ticket.status == status)
    return [ticket_out(t) for t in db.scalars(stmt).all()]


@router.get("/{ticket_id}", response_model=TicketOut, summary="Ticket details and conversation")
def get_ticket(
    ticket_id: str, principal: Principal = Depends(get_principal), db: Session = Depends(get_db)
) -> TicketOut:
    return ticket_out(tickets.get_ticket_for(db, principal, ticket_id), with_messages=True)
