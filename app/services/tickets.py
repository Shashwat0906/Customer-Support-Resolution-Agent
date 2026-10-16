"""Support tickets: local record (authoritative) + sync to the external ticketing system."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.auth.deps import Principal
from app.config import get_settings
from app.db.models import Ticket, TicketMessage
from app.db.session import session_scope
from app.schemas.errors import ExternalServiceError, NotFound
from app.services import audit, records
from app.services.external import TicketingClient

log = logging.getLogger(__name__)

TICKET_STATUSES = ("open", "in_progress", "awaiting_customer", "awaiting_approval", "resolved", "escalated", "closed")


def create_ticket(session: Session, principal: Principal, message: str, order_id: str | None) -> Ticket:
    if order_id:
        # Ownership check; a foreign or unknown order gets the same generic 404.
        records.load_owned_order(session, principal.id, order_id)
    ticket = Ticket(
        customer_id=principal.id,
        order_id=order_id,
        category="unclassified",
        status="open",
        summary=message.strip()[:300],
    )
    session.add(ticket)
    session.flush()
    session.add(
        TicketMessage(ticket_id=ticket.id, author_type="customer", author_id=principal.id, body=message.strip())
    )
    audit.record(
        session,
        run_id=None,
        ticket_id=ticket.id,
        actor=principal.actor,
        event_type="ticket_created",
        payload={"order_id": order_id, "message": message[:500]},
    )
    return ticket


def get_ticket_for(session: Session, principal: Principal, ticket_id: str) -> Ticket:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None or (principal.kind == "customer" and ticket.customer_id != principal.id):
        raise NotFound("TICKET_NOT_FOUND", "Ticket not found.")
    return ticket


def add_message(
    session: Session, ticket_id: str, author_type: str, author_id: str | None, body: str, run_id: str | None = None
) -> None:
    session.add(
        TicketMessage(ticket_id=ticket_id, run_id=run_id, author_type=author_type, author_id=author_id, body=body)
    )


def update_ticket(
    ticket_id: str,
    *,
    status: str,
    note: str,
    idempotency_key: str,
    run_id: str | None,
    category: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    """Update the local ticket (always), then sync externally with bounded retries.

    Only this ticket update is retried on failure; whatever happened before it
    (e.g. a verified refund) is not repeated.
    """
    if status not in TICKET_STATUSES:
        raise ValueError(f"invalid ticket status {status!r}")
    settings = get_settings()
    with session_scope() as session:
        ticket = session.get(Ticket, ticket_id, with_for_update=True)
        if ticket is None:
            raise NotFound("TICKET_NOT_FOUND", "Ticket not found.")
        ticket.status = status
        if category:
            ticket.category = category
        if order_id and ticket.order_id is None:
            ticket.order_id = order_id
        customer_ref, cat, summary = ticket.customer_id, ticket.category, ticket.summary

    client = TicketingClient()
    attempts = 0
    last_error: str | None = None
    while attempts < settings.max_ticket_update_attempts:
        attempts += 1
        try:
            client.upsert_ticket(ticket_id, customer_ref=customer_ref, status=status, category=cat, summary=summary)
            client.add_note(ticket_id, idempotency_key=idempotency_key, body=note, status=status)
            last_error = None
            break
        except ExternalServiceError as exc:
            last_error = exc.code
            log.info("ticket sync failed (%s), attempt %s/%s", exc.code, attempts, settings.max_ticket_update_attempts)
            if not exc.retryable:
                break
            time.sleep(settings.retry_backoff_seconds * (2 ** (attempts - 1)))

    synced = last_error is None
    with session_scope() as session:
        ticket = session.get(Ticket, ticket_id)
        assert ticket is not None
        if synced:
            ticket.external_synced_at = datetime.now(UTC)
        audit.record(
            session,
            run_id=run_id,
            ticket_id=ticket_id,
            actor="system",
            event_type="ticket_updated" if synced else "ticket_sync_failed",
            payload={"status": status, "attempts": attempts, "error": last_error},
        )
    return {"status": status, "synced": synced, "attempts": attempts, "error": last_error}
