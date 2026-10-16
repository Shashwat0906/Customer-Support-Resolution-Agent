"""Append-only audit trail with redaction of personal data and secrets."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditEvent
from app.db.session import session_scope

log = logging.getLogger(__name__)

_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_PHONE = re.compile(r"(?:\+\d{1,3}[\s-]?)?\b\d{5}[\s-]?\d{5}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]+")
_SECRET_KEYS = {"authorization", "token", "access_token", "api_key", "password", "secret", "jwt", "email", "phone"}


def redact_text(text: str) -> str:
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _CARD.sub("[REDACTED_NUMBER]", text)
    text = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    text = _PHONE.sub("[REDACTED_PHONE]", text)
    return text


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if k.lower() in _SECRET_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def record(
    session: Session,
    *,
    run_id: str | None,
    ticket_id: str | None,
    actor: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Add an audit event to the caller's transaction."""
    session.add(
        AuditEvent(
            run_id=run_id,
            ticket_id=ticket_id,
            actor=actor,
            event_type=event_type,
            redacted_payload=redact(payload or {}),
        )
    )


def record_now(
    *, run_id: str | None, ticket_id: str | None, actor: str, event_type: str, payload: dict[str, Any] | None = None
) -> None:
    """Write an audit event in its own short transaction."""
    try:
        with session_scope() as session:
            record(session, run_id=run_id, ticket_id=ticket_id, actor=actor, event_type=event_type, payload=payload)
    except Exception:  # audit must never take down the workflow, but must be visible
        log.exception("failed to write audit event %s for run %s", event_type, run_id)
