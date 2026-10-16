"""Explicit workflow run states and validated transitions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import WorkflowRun
from app.schemas.errors import Conflict

TERMINAL = frozenset({"completed", "escalated", "rejected", "expired", "failed"})

ALLOWED: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "failed"}),
    "running": frozenset(
        {
            "awaiting_customer",
            "awaiting_approval",
            "executing",
            "completed",
            "escalated",
            "rejected",
            "expired",
            "failed",
            "queued",
        }
    ),
    "awaiting_customer": frozenset({"queued", "expired", "failed"}),
    "awaiting_approval": frozenset({"queued", "failed"}),
    "executing": frozenset({"reconciling", "running", "completed", "escalated", "failed", "queued"}),
    "reconciling": frozenset({"running", "executing", "completed", "escalated", "failed", "queued"}),
}
for _t in TERMINAL:
    ALLOWED[_t] = frozenset()


class InvalidTransition(Conflict):
    pass


def can_transition(current: str, new: str) -> bool:
    return current == new or new in ALLOWED.get(current, frozenset())


def transition(session: Session, run: WorkflowRun, new: str) -> None:
    if run.status == new:
        return
    if not can_transition(run.status, new):
        raise InvalidTransition(
            "INVALID_TRANSITION",
            f"Run {run.id} cannot move from {run.status} to {new}.",
            details={"from": run.status, "to": new},
        )
    run.status = new
