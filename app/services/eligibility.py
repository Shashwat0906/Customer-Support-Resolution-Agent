"""Deterministic eligibility engine.

These functions are the *only* place refund / damage / shipping decisions are
made. They take verified facts and the current time and return a typed
decision. Retrieval similarity and model output never feed into them.

Dispositions:
    eligible    an action (refund) may be proposed for human approval
    processing  something is already in progress; inform the customer
    blocked     not eligible now (with a reason the customer can act on)
    needs_info  specific information is missing from the customer
    escalate    a human must handle it (exception, missing/conflicting policy)
    answer      informational answer (order status)
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models import RESERVING_REFUND_STATUSES
from app.policies import rules as R
from app.schemas.domain import OrderFacts, PaymentStatusFacts, ReturnStatusFacts, ShipmentFacts

Disposition = Literal["eligible", "processing", "blocked", "needs_info", "escalate", "answer"]


class Decision(BaseModel):
    kind: Literal["refund", "damage", "shipping"]
    disposition: Disposition
    reason_code: str
    explanation: str
    amount_minor: int = 0
    refundable_balance_minor: int = 0
    policy_document_id: str | None = None
    policy_version: str | None = None
    requires_senior_review: bool = False
    missing_information: list[str] = Field(default_factory=list)
    facts_used: list[str] = Field(default_factory=list)


def _policy_date(order: OrderFacts) -> date:
    return (order.delivered_at or order.placed_at).date()


def _select[T: (R.RefundRules, R.DamageRules, R.ShippingRules)](
    kind: Literal["refund", "damage", "shipping"], candidates: list[T], order: OrderFacts
) -> T | Decision:
    if not candidates:
        return Decision(
            kind=kind,
            disposition="escalate",
            reason_code="no_applicable_policy",
            explanation=f"No {kind} policy version was in effect on {_policy_date(order).isoformat()}.",
            refundable_balance_minor=order.refundable_balance_minor,
        )
    if len(candidates) > 1:
        return Decision(
            kind=kind,
            disposition="escalate",
            reason_code="conflicting_policy",
            explanation=f"Multiple {kind} policy versions apply on {_policy_date(order).isoformat()}: "
            + ", ".join(c.document_id for c in candidates),
            refundable_balance_minor=order.refundable_balance_minor,
        )
    return candidates[0]


def _in_progress(order: OrderFacts, payment: PaymentStatusFacts | None) -> bool:
    if order.reserved_refund_minor > 0:
        return True
    if payment is None:
        return False
    if any(r.status in RESERVING_REFUND_STATUSES for r in payment.ledger_refunds):
        return True
    return bool(payment.provider_refund_pending_minor)


def _ledger_mismatch(order: OrderFacts, payment: PaymentStatusFacts | None) -> bool:
    """The provider reports more refunded money than our ledger knows about."""
    if payment is None or payment.provider_refunded_minor is None:
        return False
    # Money we have in flight (reserved) may already have settled at the provider.
    return payment.provider_refunded_minor > order.refunded_amount_minor + order.reserved_refund_minor


def evaluate_refund(
    order: OrderFacts, returns: ReturnStatusFacts, payment: PaymentStatusFacts | None, now: datetime
) -> Decision:
    selected = _select("refund", R.refund_rules_for(_policy_date(order)), order)
    if isinstance(selected, Decision):
        return selected
    rules = selected
    base = {
        "kind": "refund",
        "policy_document_id": rules.document_id,
        "policy_version": rules.version,
        "refundable_balance_minor": order.refundable_balance_minor,
    }

    def d(disposition: Disposition, code: str, text: str, **kw: object) -> Decision:
        return Decision(disposition=disposition, reason_code=code, explanation=text, **base, **kw)  # type: ignore[arg-type]

    if _ledger_mismatch(order, payment):
        return d(
            "escalate",
            "ledger_provider_mismatch",
            "The payment provider reports refunds the ledger does not; a human must reconcile.",
        )
    if _in_progress(order, payment):
        return d("processing", "refund_in_progress", "A refund for this order is already being processed.")
    if order.refunded_amount_minor >= order.paid_amount_minor:
        return d("blocked", "already_refunded", "This order has already been refunded in full.")
    if order.status == "cancelled":
        return d("escalate", "cancelled_without_refund", "The order is cancelled but no refund is recorded.")
    if order.status != "delivered" or order.delivered_at is None:
        return d("blocked", "order_not_delivered", "The order has not been delivered, so no return can be refunded.")
    ret = returns.latest
    if ret is None or ret.status == "cancelled":
        return d("blocked", "return_not_started", "No return has been started for this order.")
    if ret.status in ("requested", "in_transit"):
        return d(
            "processing",
            "return_not_received",
            "The return has not reached our warehouse yet; refunds start after it is received and inspected.",
        )
    if ret.status == "rejected":
        return d("blocked", "return_rejected", "The return was rejected at the warehouse.")
    days_to_request = (ret.requested_at - order.delivered_at).days
    if days_to_request > rules.return_window_days:
        return d(
            "blocked",
            "outside_return_window",
            f"The return was requested {days_to_request} days after delivery; the window is "
            f"{rules.return_window_days} days.",
        )
    if ret.inspection_status in ("not_started", "pending"):
        return d("processing", "inspection_pending", "The returned item is awaiting inspection.")
    if ret.inspection_status == "failed":
        return d("blocked", "inspection_failed", "The returned item did not pass inspection.")
    if ret.inspection_status == "needs_review":
        return d("escalate", "inspection_needs_review", "The inspection result requires human review.")
    amount = order.refundable_balance_minor
    if amount <= 0:
        return d("blocked", "nothing_refundable", "There is no refundable balance left on this order.")
    return d(
        "eligible",
        "return_received_inspection_passed",
        "The return was received and passed inspection within the return window.",
        amount_minor=amount,
        requires_senior_review=amount > rules.senior_review_threshold_minor,
        facts_used=[f"order:{order.order_id}", f"return:{ret.return_id}"],
    )


class DamageReport(BaseModel):
    description: str | None = None
    photos_available: bool | None = None
    preference: Literal["refund", "replacement"] | None = None


def evaluate_damage(
    order: OrderFacts, report: DamageReport, payment: PaymentStatusFacts | None, reported_at: datetime
) -> Decision:
    selected = _select("damage", R.damage_rules_for(_policy_date(order)), order)
    if isinstance(selected, Decision):
        return selected
    rules = selected
    base = {
        "kind": "damage",
        "policy_document_id": rules.document_id,
        "policy_version": rules.version,
        "refundable_balance_minor": order.refundable_balance_minor,
    }

    def d(disposition: Disposition, code: str, text: str, **kw: object) -> Decision:
        return Decision(disposition=disposition, reason_code=code, explanation=text, **base, **kw)  # type: ignore[arg-type]

    if order.status != "delivered" or order.delivered_at is None:
        return d("blocked", "order_not_delivered", "The order has not been delivered yet.")
    if _in_progress(order, payment):
        return d("processing", "refund_in_progress", "A refund for this order is already being processed.")
    if order.refunded_amount_minor >= order.paid_amount_minor:
        return d("blocked", "already_refunded", "This order has already been refunded in full.")
    days = (reported_at - order.delivered_at).days
    if days > rules.report_window_days:
        return d(
            "escalate",
            "damage_reported_late",
            f"Damage was reported {days} days after delivery; the window is {rules.report_window_days} days. "
            "A specialist will review it.",
        )
    missing = []
    if not report.description:
        missing.append("damage_description")
    if report.photos_available is None:
        missing.append("photo_confirmation")
    if missing:
        return d(
            "needs_info", "damage_details_missing", "Details about the damage are needed.", missing_information=missing
        )
    if report.photos_available is False:
        return d("escalate", "no_photo_evidence", "Without photos, a specialist must assess the damage claim.")
    if report.preference == "replacement":
        return d(
            "escalate",
            "replacement_requested",
            "Replacements are arranged by the fulfilment team; the case has been prepared for them.",
        )
    amount = order.refundable_balance_minor
    if amount <= 0:
        return d("blocked", "nothing_refundable", "There is no refundable balance left on this order.")
    refund_rules = R.refund_rules_for(_policy_date(order))
    threshold = refund_rules[0].senior_review_threshold_minor if refund_rules else 0
    return d(
        "eligible",
        "damage_reported_in_window",
        "Damage was reported within the window with a description and photos.",
        amount_minor=amount,
        requires_senior_review=amount > threshold,
        facts_used=[f"order:{order.order_id}"],
    )


def evaluate_shipping(order: OrderFacts, shipment: ShipmentFacts | None, now: datetime) -> Decision:
    selected = _select("shipping", R.shipping_rules_for(_policy_date(order)), order)
    if isinstance(selected, Decision):
        return selected
    rules = selected
    base = {
        "kind": "shipping",
        "policy_document_id": rules.document_id,
        "policy_version": rules.version,
        "refundable_balance_minor": order.refundable_balance_minor,
    }

    def d(disposition: Disposition, code: str, text: str) -> Decision:
        return Decision(disposition=disposition, reason_code=code, explanation=text, **base)  # type: ignore[arg-type]

    if order.status == "cancelled":
        return d("answer", "order_cancelled", "The order was cancelled.")
    if shipment is None:
        return d("answer", "not_shipped", "The order is being prepared and has not been handed to a carrier yet.")
    if shipment.status == "delivered" or order.status == "delivered":
        return d("answer", "delivered", "The carrier reports the order as delivered.")
    if shipment.status == "exception":
        return d("escalate", "shipment_exception", "The carrier reported a problem with this shipment.")
    if shipment.estimated_delivery_at is not None:
        late_days = (now - shipment.estimated_delivery_at).days
        if late_days > rules.escalate_after_days_late:
            return d(
                "escalate", "shipment_delayed", f"The shipment is {late_days} days past its estimated delivery date."
            )
        if now > shipment.estimated_delivery_at:
            return d("answer", "running_late", "The shipment is slightly past its estimated delivery date.")
    if shipment.status == "label_created":
        return d("answer", "label_created", "A shipping label is created; the carrier has not collected it yet.")
    if shipment.status == "out_for_delivery":
        return d("answer", "out_for_delivery", "The order is out for delivery today.")
    return d("answer", "in_transit", "The order is in transit.")
