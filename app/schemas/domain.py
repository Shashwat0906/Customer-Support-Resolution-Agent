"""Typed facts returned by read tools. These are the 'verified facts' of a run."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ItemFacts(BaseModel):
    product_id: str
    name: str
    category: str
    quantity: int
    paid_amount_minor: int


class OrderFacts(BaseModel):
    order_id: str
    status: str
    currency: str
    paid_amount_minor: int
    refunded_amount_minor: int
    reserved_refund_minor: int
    refundable_balance_minor: int
    placed_at: datetime
    delivered_at: datetime | None
    items: list[ItemFacts]
    tracking_number: str | None = None
    carrier: str | None = None
    estimated_delivery_at: datetime | None = None

    @property
    def categories(self) -> list[str]:
        return sorted({i.category for i in self.items})

    @property
    def product_names(self) -> list[str]:
        return [i.name for i in self.items]


class ReturnFacts(BaseModel):
    return_id: str
    status: str
    requested_at: datetime
    received_at: datetime | None
    inspection_status: str
    reason: str | None = None


class ReturnStatusFacts(BaseModel):
    order_id: str
    has_return: bool
    latest: ReturnFacts | None = None


class RefundRecord(BaseModel):
    operation_id: str | None = None
    provider_reference: str | None = None
    amount_minor: int
    status: str
    created_at: datetime | None = None


class PaymentStatusFacts(BaseModel):
    order_id: str
    payment_reference: str
    method: str
    amount_minor: int
    status: str
    provider_refunded_minor: int | None = None
    provider_refund_pending_minor: int | None = None
    ledger_refunds: list[RefundRecord] = Field(default_factory=list)
    provider_refunds: list[RefundRecord] = Field(default_factory=list)
    provider_reachable: bool = True


class ShipmentEvent(BaseModel):
    status: str
    at: datetime
    location: str


class ShipmentFacts(BaseModel):
    order_id: str
    tracking_number: str
    carrier: str
    status: str
    estimated_delivery_at: datetime | None
    events: list[ShipmentEvent]

    @property
    def latest_event(self) -> ShipmentEvent | None:
        return self.events[-1] if self.events else None


class RefundOperationFacts(BaseModel):
    operation_id: str
    order_id: str
    proposal_id: str
    amount_minor: int
    status: str
    provider_reference: str | None
    attempts: int
    reconcile_attempts: int
    last_error: str | None


class TicketFacts(BaseModel):
    ticket_id: str
    order_id: str | None
    category: str
    status: str
    summary: str
    created_at: datetime
