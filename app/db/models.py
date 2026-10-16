"""SQLAlchemy models for application data and the financial ledger.

All money is stored as integer minor units (paise) in INR. Financial invariants
are enforced by database constraints, not only by application code:

* ``orders.refunded_amount_minor + orders.reserved_refund_minor <= paid_amount_minor``
* ``refund_operations.idempotency_key`` and ``refund_operations.proposal_id`` are unique
* at most one pending approval per proposal (partial unique index)
* ``action_proposals.payload``/``payload_hash`` are immutable (trigger, see migration 0001)

LangGraph checkpoints live in the separate ``langgraph`` schema and are never
used as the ledger.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- people


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    external_auth_subject: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30))

    orders: Mapped[list[Order]] = relationship(back_populates="customer")


class StaffUser(TimestampMixin, Base):
    """Support staff (reviewers). Supporting table."""

    __tablename__ = "staff_users"
    __table_args__ = (
        CheckConstraint("role IN ('reviewer', 'senior_reviewer')", name="ck_staff_role"),
        CheckConstraint("approval_limit_minor >= 0", name="ck_staff_limit_nonneg"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    external_auth_subject: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    approval_limit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


# --------------------------------------------------------------------------- commerce


class Product(TimestampMixin, Base):
    """Product catalogue. Supporting table."""

    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, CheckConstraint("price_minor > 0"), nullable=False)


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("currency = 'INR'", name="ck_orders_currency_inr"),
        CheckConstraint("paid_amount_minor > 0", name="ck_orders_paid_positive"),
        CheckConstraint("refunded_amount_minor >= 0", name="ck_orders_refunded_nonneg"),
        CheckConstraint("reserved_refund_minor >= 0", name="ck_orders_reserved_nonneg"),
        CheckConstraint(
            "refunded_amount_minor + reserved_refund_minor <= paid_amount_minor",
            name="ck_orders_refunds_within_paid",
        ),
        CheckConstraint("status IN ('placed', 'shipped', 'delivered', 'cancelled')", name="ck_orders_status"),
        Index("ix_orders_customer_id", "customer_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    paid_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    refunded_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    reserved_refund_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(back_populates="order", order_by="OrderItem.id")
    returns: Mapped[list[Return]] = relationship(back_populates="order", order_by="Return.id")
    payments: Mapped[list[Payment]] = relationship(back_populates="order")
    shipments: Mapped[list[Shipment]] = relationship(back_populates="order")


class OrderItem(TimestampMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_qty_positive"),
        CheckConstraint("paid_amount_minor >= 0", name="ck_order_items_paid_nonneg"),
        Index("ix_order_items_order_id", "order_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class Return(TimestampMixin, Base):
    __tablename__ = "returns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'in_transit', 'received', 'rejected', 'cancelled')", name="ck_returns_status"
        ),
        CheckConstraint(
            "inspection_status IN ('not_started', 'pending', 'passed', 'failed', 'needs_review')",
            name="ck_returns_inspection",
        ),
        CheckConstraint("status <> 'received' OR received_at IS NOT NULL", name="ck_returns_received_at"),
        Index("ix_returns_order_id", "order_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    inspection_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="not_started")
    reason: Mapped[str | None] = mapped_column(String(300))

    order: Mapped[Order] = relationship(back_populates="returns")


class Payment(TimestampMixin, Base):
    """Captured payment for an order. Supporting table."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_payments_amount_positive"),
        CheckConstraint("currency = 'INR'", name="ck_payments_currency_inr"),
        Index("ix_payments_order_id", "order_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    provider_payment_reference: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    order: Mapped[Order] = relationship(back_populates="payments")


class Shipment(TimestampMixin, Base):
    """Shipment reference; live tracking comes from the shipping API. Supporting table."""

    __tablename__ = "shipments"
    __table_args__ = (Index("ix_shipments_order_id", "order_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    carrier: Mapped[str] = mapped_column(String(60), nullable=False)
    tracking_number: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    estimated_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="shipments")


# --------------------------------------------------------------------------- support


class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint(
            "category IN ('unclassified', 'order_status', 'refund_request', 'damaged_item', 'other')",
            name="ck_tickets_category",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'awaiting_customer', 'awaiting_approval', 'resolved', "
            "'escalated', 'closed')",
            name="ck_tickets_status",
        ),
        Index("ix_tickets_customer_id", "customer_id"),
        Index("ix_tickets_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("tkt"))
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"))
    category: Mapped[str] = mapped_column(String(30), nullable=False, server_default="unclassified")
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="open")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    external_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list[TicketMessage]] = relationship(back_populates="ticket", order_by="TicketMessage.created_at")
    runs: Mapped[list[WorkflowRun]] = relationship(back_populates="ticket", order_by="WorkflowRun.created_at")


class TicketMessage(Base):
    """Customer-visible conversation on a ticket. Supporting table."""

    __tablename__ = "ticket_messages"
    __table_args__ = (
        CheckConstraint("author_type IN ('customer', 'agent', 'reviewer', 'system')", name="ck_msg_author"),
        Index("ix_ticket_messages_ticket_id", "ticket_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("msg"))
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_runs.id"))
    author_type: Mapped[str] = mapped_column(String(20), nullable=False)
    author_id: Mapped[str | None] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="messages")


RUN_STATUSES = (
    "queued",
    "running",
    "awaiting_customer",
    "awaiting_approval",
    "executing",
    "reconciling",
    "completed",
    "escalated",
    "rejected",
    "expired",
    "failed",
)


class WorkflowRun(TimestampMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in RUN_STATUSES) + ")", name="ck_runs_status"),
        Index("ix_workflow_runs_ticket_id", "ticket_id"),
        Index("ix_workflow_runs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("run"))
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="queued")
    outcome: Mapped[str | None] = mapped_column(String(40))
    agent_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    final_response: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(60))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    state_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    ticket: Mapped[Ticket] = relationship(back_populates="runs")


class WorkflowJob(TimestampMixin, Base):
    """Durable job queue driving the worker. Supporting table."""

    __tablename__ = "workflow_jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('start', 'resume')", name="ck_jobs_kind"),
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name="ck_jobs_status"),
        Index("ix_workflow_jobs_claim", "status", "available_at"),
        Index("ix_workflow_jobs_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("job"))
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(80))
    last_error: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------- policies


class PolicyDocument(TimestampMixin, Base):
    __tablename__ = "policy_documents"
    __table_args__ = (
        UniqueConstraint("policy_key", "version", name="uq_policy_key_version"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_policy_dates"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    policy_key: Mapped[str] = mapped_column(String(60), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str] = mapped_column(String(60), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_uri: Mapped[str] = mapped_column(String(300), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    chunks: Mapped[list[PolicyChunk]] = relationship(back_populates="document", cascade="all, delete-orphan")


class PolicyChunk(Base):
    __tablename__ = "policy_chunks"
    __table_args__ = (
        Index("ix_policy_chunks_document_id", "document_id"),
        Index("ix_policy_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("policy_documents.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('english', content)", persisted=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    document: Mapped[PolicyDocument] = relationship(back_populates="chunks")


# --------------------------------------------------------------------------- actions & ledger


class ActionProposal(TimestampMixin, Base):
    __tablename__ = "action_proposals"
    __table_args__ = (
        CheckConstraint("action_type IN ('refund')", name="ck_proposals_action_type"),
        CheckConstraint(
            "status IN ('pending_approval', 'approved', 'rejected', 'expired', 'superseded', 'executed', "
            "'execution_failed')",
            name="ck_proposals_status",
        ),
        UniqueConstraint("run_id", "revision", name="uq_proposals_run_revision"),
        Index("ix_action_proposals_run_id", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("prp"))
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pending_approval")

    approvals: Mapped[list[Approval]] = relationship(back_populates="proposal", order_by="Approval.created_at")


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected', 'expired', 'invalidated')", name="ck_approvals_decision"
        ),
        CheckConstraint("(decision = 'pending') = (decided_at IS NULL)", name="ck_approvals_decided_at"),
        CheckConstraint(
            "decision NOT IN ('approved', 'rejected') OR reviewer_id IS NOT NULL", name="ck_approvals_reviewer"
        ),
        Index(
            "uq_approvals_one_pending_per_proposal",
            "proposal_id",
            unique=True,
            postgresql_where=text("decision = 'pending'"),
        ),
        Index("ix_approvals_decision", "decision"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("apr"))
    proposal_id: Mapped[str] = mapped_column(ForeignKey("action_proposals.id"), nullable=False)
    proposal_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("staff_users.id"))
    decision: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    note: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    proposal: Mapped[ActionProposal] = relationship(back_populates="approvals")


REFUND_STATUSES = ("pending", "submitted", "processing", "succeeded", "failed", "unknown")
RESERVING_REFUND_STATUSES = ("pending", "submitted", "processing", "unknown")


class RefundOperation(TimestampMixin, Base):
    """Financial ledger entry for one approved refund.

    pending     amount reserved, provider not yet called
    submitted   provider call in flight (a crash here means outcome unknown)
    processing  provider accepted, settlement not yet confirmed
    succeeded   provider confirmed; amount moved from reserved to refunded
    failed      definitive failure; reservation released
    unknown     outcome uncertain (timeout/5xx); reconcile before anything else
    """

    __tablename__ = "refund_operations"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_refunds_amount_positive"),
        CheckConstraint("currency = 'INR'", name="ck_refunds_currency_inr"),
        CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in REFUND_STATUSES) + ")", name="ck_refunds_status"),
        CheckConstraint("status <> 'succeeded' OR provider_reference IS NOT NULL", name="ck_refunds_success_ref"),
        Index("ix_refund_operations_order_id", "order_id"),
        Index("ix_refund_operations_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rfo"))
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("action_proposals.id"), unique=True, nullable=False)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    reconcile_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    provider_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Append-only audit trail (UPDATE/DELETE blocked by trigger)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_run_id", "run_id", "id"),
        Index("ix_audit_events_ticket_id", "ticket_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_runs.id"))
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("tickets.id"))
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    redacted_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
