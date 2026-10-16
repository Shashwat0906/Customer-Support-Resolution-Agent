"""Authorized access to customer, order, return, payment and shipment records.

Every lookup is scoped to the authenticated customer. An order that does not
exist and an order that belongs to someone else produce the *same* error, so
the response never reveals whether another customer's order exists.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Order, OrderItem, RefundOperation, Return, Ticket
from app.db.session import session_scope
from app.schemas.domain import (
    ItemFacts,
    OrderFacts,
    PaymentStatusFacts,
    RefundOperationFacts,
    RefundRecord,
    ReturnFacts,
    ReturnStatusFacts,
    ShipmentEvent,
    ShipmentFacts,
    TicketFacts,
)
from app.schemas.errors import ExternalServiceError, NotFound
from app.services.external import PaymentsClient, ShippingClient, with_read_retry

ORDER_NOT_FOUND = "ORDER_NOT_FOUND"


def _order_not_found(order_id: str) -> NotFound:
    return NotFound(ORDER_NOT_FOUND, f"No order {order_id!r} was found on this account.")


def load_owned_order(session: Session, customer_id: str, order_id: str, *, for_update: bool = False) -> Order:
    stmt = (
        select(Order)
        .where(Order.id == order_id, Order.customer_id == customer_id)
        .options(
            selectinload(Order.items).selectinload(OrderItem.product),
            selectinload(Order.returns),
            selectinload(Order.payments),
            selectinload(Order.shipments),
        )
    )
    if for_update:
        stmt = stmt.with_for_update(of=Order)
    order = session.scalar(stmt)
    if order is None:
        raise _order_not_found(order_id)
    return order


def order_facts(order: Order) -> OrderFacts:
    shipment = order.shipments[0] if order.shipments else None
    return OrderFacts(
        order_id=order.id,
        status=order.status,
        currency=order.currency,
        paid_amount_minor=order.paid_amount_minor,
        refunded_amount_minor=order.refunded_amount_minor,
        reserved_refund_minor=order.reserved_refund_minor,
        refundable_balance_minor=order.paid_amount_minor - order.refunded_amount_minor - order.reserved_refund_minor,
        placed_at=order.placed_at,
        delivered_at=order.delivered_at,
        items=[
            ItemFacts(
                product_id=i.product_id,
                name=i.product.name,
                category=i.product.category,
                quantity=i.quantity,
                paid_amount_minor=i.paid_amount_minor,
            )
            for i in order.items
        ],
        tracking_number=shipment.tracking_number if shipment else None,
        carrier=shipment.carrier if shipment else None,
        estimated_delivery_at=shipment.estimated_delivery_at if shipment else None,
    )


def get_order(session: Session, customer_id: str, order_id: str) -> OrderFacts:
    return order_facts(load_owned_order(session, customer_id, order_id))


def list_customer_orders(session: Session, customer_id: str) -> list[OrderFacts]:
    orders = session.scalars(
        select(Order)
        .where(Order.customer_id == customer_id)
        .order_by(Order.placed_at.desc())
        .options(selectinload(Order.items).selectinload(OrderItem.product), selectinload(Order.shipments))
    ).all()
    return [order_facts(o) for o in orders]


def get_return_status(session: Session, customer_id: str, order_id: str) -> ReturnStatusFacts:
    load_owned_order(session, customer_id, order_id)
    ret = session.scalar(select(Return).where(Return.order_id == order_id).order_by(Return.requested_at.desc()))
    if ret is None:
        return ReturnStatusFacts(order_id=order_id, has_return=False)
    return ReturnStatusFacts(
        order_id=order_id,
        has_return=True,
        latest=ReturnFacts(
            return_id=ret.id,
            status=ret.status,
            requested_at=ret.requested_at,
            received_at=ret.received_at,
            inspection_status=ret.inspection_status,
            reason=ret.reason,
        ),
    )


def ledger_refunds(session: Session, order_id: str) -> list[RefundRecord]:
    ops = session.scalars(
        select(RefundOperation).where(RefundOperation.order_id == order_id).order_by(RefundOperation.created_at)
    ).all()
    return [
        RefundRecord(
            operation_id=o.id,
            provider_reference=o.provider_reference,
            amount_minor=o.amount_minor,
            status=o.status,
            created_at=o.created_at,
        )
        for o in ops
    ]


def get_payment_status(
    customer_id: str, order_id: str, on_retry: Callable[[int, ExternalServiceError], None] | None = None
) -> PaymentStatusFacts:
    """Ledger view (DB, short session) + provider view (network, no open transaction)."""
    with session_scope() as session:
        order = load_owned_order(session, customer_id, order_id)
        payment = order.payments[0]
        facts = PaymentStatusFacts(
            order_id=order_id,
            payment_reference=payment.provider_payment_reference,
            method=payment.method,
            amount_minor=payment.amount_minor,
            status=payment.status,
            ledger_refunds=ledger_refunds(session, order_id),
        )
    return attach_provider_view(facts, on_retry=on_retry)


def attach_provider_view(
    facts: PaymentStatusFacts, on_retry: Callable[[int, ExternalServiceError], None] | None = None
) -> PaymentStatusFacts:
    client = PaymentsClient()
    try:
        remote = with_read_retry(lambda: client.get_payment(facts.payment_reference), on_retry=on_retry)
    except ExternalServiceError:
        # The ledger is still authoritative for our own operations; flag the gap.
        facts.provider_reachable = False
        return facts
    facts.provider_refunded_minor = remote["refunded_minor"]
    facts.provider_refund_pending_minor = remote["refund_pending_minor"]
    facts.provider_refunds = [
        RefundRecord(
            provider_reference=r["id"], amount_minor=r["amount_minor"], status=r["status"], created_at=r["created_at"]
        )
        for r in remote["refunds"]
    ]
    return facts


def get_shipment(
    customer_id: str, order_id: str, on_retry: Callable[[int, ExternalServiceError], None] | None = None
) -> ShipmentFacts | None:
    with session_scope() as session:
        order = load_owned_order(session, customer_id, order_id)
        tracking = order.shipments[0].tracking_number if order.shipments else None
    if tracking is None:
        return None
    client = ShippingClient()
    remote = with_read_retry(lambda: client.get_shipment(tracking), on_retry=on_retry)
    return ShipmentFacts(
        order_id=order_id,
        tracking_number=remote["tracking_number"],
        carrier=remote["carrier"],
        status=remote["status"],
        estimated_delivery_at=remote["estimated_delivery_at"],
        events=[ShipmentEvent(**e) for e in remote["events"]],
    )


def get_refund_operation(session: Session, customer_id: str, operation_id: str) -> RefundOperationFacts:
    op = session.get(RefundOperation, operation_id)
    if op is None or session.scalar(select(Order.customer_id).where(Order.id == op.order_id)) != customer_id:
        raise NotFound("REFUND_NOT_FOUND", f"No refund operation {operation_id!r} was found on this account.")
    return RefundOperationFacts(
        operation_id=op.id,
        order_id=op.order_id,
        proposal_id=op.proposal_id,
        amount_minor=op.amount_minor,
        status=op.status,
        provider_reference=op.provider_reference,
        attempts=op.attempts,
        reconcile_attempts=op.reconcile_attempts,
        last_error=op.last_error,
    )


def get_ticket(session: Session, customer_id: str, ticket_id: str) -> TicketFacts:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None or ticket.customer_id != customer_id:
        raise NotFound("TICKET_NOT_FOUND", f"No ticket {ticket_id!r} was found on this account.")
    return TicketFacts(
        ticket_id=ticket.id,
        order_id=ticket.order_id,
        category=ticket.category,
        status=ticket.status,
        summary=ticket.summary,
        created_at=ticket.created_at,
    )
