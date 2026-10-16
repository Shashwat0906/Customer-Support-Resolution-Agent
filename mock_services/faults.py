"""Configurable fault injection for the mock services.

A fault rule targets one operation (e.g. ``payments.create_refund``) and applies
to the next ``count`` calls (``-1`` = until cleared). Modes:

``error``                respond with ``status_code`` without doing any work
``timeout``              sleep ``delay_ms`` then respond 504 without doing any work
``delay``                sleep ``delay_ms`` then behave normally
``commit_then_timeout``  do the work (commit), then sleep ``delay_ms`` so the caller times out
``commit_then_error``    do the work (commit), then respond with ``status_code``
``processing``           (payments.create_refund) accept the refund as ``processing``; it
                         settles to ``settle_to`` after ``settle_after`` status polls
"""

from __future__ import annotations

import threading
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

FaultMode = Literal["error", "timeout", "delay", "commit_then_timeout", "commit_then_error", "processing"]

TARGETS = (
    "payments.get_payment",
    "payments.create_refund",
    "payments.get_refund",
    "payments.lookup_refund",
    "shipping.get_shipment",
    "ticketing.upsert_ticket",
    "ticketing.add_note",
    "ticketing.get_ticket",
)


class FaultRule(BaseModel):
    target: str
    mode: FaultMode
    count: int = Field(default=1, ge=-1)
    status_code: int = Field(default=503, ge=400, le=599)
    delay_ms: int = Field(default=0, ge=0, le=120_000)
    settle_after: int = Field(default=2, ge=0, le=100)
    settle_to: Literal["succeeded", "failed"] = "succeeded"
    match_key: str | None = Field(default=None, description="only apply when the path/key contains this text")


class FaultRegistry:
    def __init__(self) -> None:
        self._rules: list[FaultRule] = []
        self._lock = threading.Lock()
        self.hits: dict[str, int] = {}

    def add(self, rule: FaultRule) -> None:
        if rule.target not in TARGETS:
            raise ValueError(f"unknown fault target {rule.target!r}; expected one of {TARGETS}")
        with self._lock:
            self._rules.append(rule)

    def clear(self) -> None:
        with self._lock:
            self._rules.clear()
            self.hits.clear()

    def list(self) -> list[FaultRule]:
        with self._lock:
            return list(self._rules)

    def take(self, target: str, key: str = "") -> FaultRule | None:
        """Consume and return the first matching rule for this call, if any."""
        with self._lock:
            self.hits[target] = self.hits.get(target, 0) + 1
            for rule in self._rules:
                if rule.target != target or (rule.match_key and rule.match_key not in key):
                    continue
                if rule.count == 0:
                    continue
                if rule.count > 0:
                    rule.count -= 1
                return rule
        return None


def apply_pre(rule: FaultRule | None) -> None:
    """Apply the part of a fault that happens before any work is done."""
    if rule is None:
        return
    if rule.mode == "error":
        raise HTTPException(status_code=rule.status_code, detail={"error": "injected_fault", "mode": "error"})
    if rule.mode == "timeout":
        time.sleep(rule.delay_ms / 1000)
        raise HTTPException(status_code=504, detail={"error": "injected_fault", "mode": "timeout"})
    if rule.mode == "delay":
        time.sleep(rule.delay_ms / 1000)


def apply_post(rule: FaultRule | None) -> None:
    """Apply the part of a fault that happens after the work was committed."""
    if rule is None:
        return
    if rule.mode == "commit_then_timeout":
        time.sleep(rule.delay_ms / 1000)
        raise HTTPException(status_code=504, detail={"error": "injected_fault", "mode": "commit_then_timeout"})
    if rule.mode == "commit_then_error":
        raise HTTPException(
            status_code=rule.status_code, detail={"error": "injected_fault", "mode": "commit_then_error"}
        )
