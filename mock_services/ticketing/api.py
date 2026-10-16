"""Mock external ticketing system (a helpdesk the support team uses)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field

from mock_services.deps import get_faults, get_store
from mock_services.faults import FaultRegistry, apply_post, apply_pre
from mock_services.store import Store, now_iso

router = APIRouter(prefix="/ticketing", tags=["mock-ticketing"])


class TicketUpsert(BaseModel):
    customer_ref: str
    status: str
    category: str
    summary: str = Field(max_length=4000)


class NoteCreate(BaseModel):
    body: str = Field(max_length=8000)
    status: str | None = None


@router.put("/tickets/{ticket_id}")
def upsert_ticket(
    ticket_id: str, body: TicketUpsert, store: Store = Depends(get_store), faults: FaultRegistry = Depends(get_faults)
) -> dict[str, Any]:
    rule = faults.take("ticketing.upsert_ticket", ticket_id)
    apply_pre(rule)
    with store.tx() as c:
        c.execute(
            "INSERT INTO tickets (id, customer_ref, status, category, summary, updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, category=excluded.category, "
            "summary=excluded.summary, updated_at=excluded.updated_at",
            (ticket_id, body.customer_ref, body.status, body.category, body.summary, now_iso()),
        )
    apply_post(rule)
    return get_ticket(ticket_id, store, faults, record=False)


@router.post("/tickets/{ticket_id}/notes", status_code=201)
def add_note(
    ticket_id: str,
    body: NoteCreate,
    response: Response,
    idempotency_key: str = Header(min_length=8, max_length=160),
    store: Store = Depends(get_store),
    faults: FaultRegistry = Depends(get_faults),
) -> dict[str, Any]:
    rule = faults.take("ticketing.add_note", ticket_id)
    apply_pre(rule)
    if not store.query("SELECT id FROM tickets WHERE id = ?", (ticket_id,)):
        raise HTTPException(status_code=404, detail={"error": "ticket_not_found"})
    existing = store.query("SELECT * FROM ticket_notes WHERE idempotency_key = ?", (idempotency_key,))
    if existing:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
        return existing[0]
    with store.tx() as c:
        c.execute(
            "INSERT INTO ticket_notes (ticket_id, idempotency_key, body, status, created_at) VALUES (?,?,?,?,?)",
            (ticket_id, idempotency_key, body.body, body.status, now_iso()),
        )
        if body.status:
            c.execute("UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?", (body.status, now_iso(), ticket_id))
    apply_post(rule)
    return store.query("SELECT * FROM ticket_notes WHERE idempotency_key = ?", (idempotency_key,))[0]


@router.get("/tickets/{ticket_id}")
def get_ticket(
    ticket_id: str, store: Store = Depends(get_store), faults: FaultRegistry = Depends(get_faults), record: bool = True
) -> dict[str, Any]:
    if record:
        apply_pre(faults.take("ticketing.get_ticket", ticket_id))
    rows = store.query("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    if not rows:
        raise HTTPException(status_code=404, detail={"error": "ticket_not_found"})
    notes = store.query("SELECT * FROM ticket_notes WHERE ticket_id = ? ORDER BY id", (ticket_id,))
    return dict(rows[0], notes=notes)
