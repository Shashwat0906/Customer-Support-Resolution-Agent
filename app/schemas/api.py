"""Request and response models for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class DemoLoginRequest(BaseModel):
    subject: str = Field(description="external_auth_subject of a synthetic persona (see GET /auth/demo-users)")


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    principal: dict[str, Any]


class TicketCreate(BaseModel):
    message: str = Field(min_length=3, max_length=4000, description="The customer's complaint in their own words")
    order_id: str | None = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9-]+$")

    @field_validator("message")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("message is too short")
        return v


class TicketMessageOut(BaseModel):
    author_type: str
    body: str
    created_at: datetime


class TicketOut(BaseModel):
    ticket_id: str
    customer_id: str
    order_id: str | None
    category: str
    status: str
    summary: str
    created_at: datetime
    updated_at: datetime
    latest_run_id: str | None = None
    latest_run_status: str | None = None
    messages: list[TicketMessageOut] = Field(default_factory=list)


class RunCreated(BaseModel):
    run_id: str
    ticket_id: str
    status: str
    agent_mode: str
    status_url: str
    events_url: str


class RunCreate(BaseModel):
    agent_mode: Literal["specialist", "single"] | None = Field(
        default=None, description="Override the configured agent design (evaluation/demo only)"
    )


class RunOut(BaseModel):
    run_id: str
    ticket_id: str
    status: str
    outcome: str | None
    agent_mode: str
    started_at: datetime | None
    completed_at: datetime | None
    final_response: str | None
    error_code: str | None
    state: dict[str, Any]
    metrics: dict[str, Any] | None = None
    pending_approval_id: str | None = None


class EventOut(BaseModel):
    id: int
    created_at: datetime
    actor: str
    event_type: str
    payload: dict[str, Any] | None = None


class CustomerMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)


class HealthOut(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    checks: dict[str, Any]
    version: str
