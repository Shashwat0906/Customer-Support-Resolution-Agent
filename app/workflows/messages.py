"""Deterministic customer-facing drafts and the grounding guard.

The draft is built only from verified state. The responder agent may rephrase
it; ``check_grounding`` rejects any rewrite that claims more than the verified
outcome supports (e.g. "refund issued" when it is only processing), mentions
an amount that is not in the verified facts, or drops a reference number.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def format_inr(minor: int) -> str:
    rupees, paise = divmod(int(minor), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"₹{s}.{paise:02d}"


def _date(value: Any) -> str:
    if not value:
        return "an unknown date"
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return dt.strftime("%d %b %Y")


def _items(order: dict[str, Any]) -> str:
    names = [i["name"] for i in order.get("items", [])]
    return " and ".join(names) if names else "your order"


QUESTIONS = {
    "damage_description": "a short description of the damage (for example, a cracked case or no sound)",
    "photo_confirmation": "whether you can share photos of the item and its packaging",
    "issue_details": "a little more detail about what you need help with (an order's delivery, a refund, or a "
    "damaged item)",
}


def clarification_question(
    missing: list[str], *, reference: str | None = None, orders: list[dict[str, Any]] | None = None
) -> str:
    if "order_id" in missing:
        if reference:
            return (
                f"I couldn't find order {reference} on your account. Could you double-check the order number? "
                "It looks like ORD-1234."
            )
        listing = "; ".join(f"{o['order_id']} ({', '.join(o['items'])})" for o in (orders or [])[:6])
        return (
            f"Which order is this about? Your recent orders: {listing}."
            if listing
            else "Could you tell me the order number this is about? It looks like ORD-1234."
        )
    parts = [QUESTIONS.get(m, m.replace("_", " ")) for m in missing]
    return "To help with this, could you please share " + " and ".join(parts) + "?"


def draft_response(state: dict[str, Any]) -> str:
    outcome = state.get("outcome")
    decision = state.get("decision") or {}
    facts = state.get("verified_facts") or {}
    order = facts.get("order") or {}
    order_id = state.get("order_id") or order.get("order_id") or "your order"
    code = decision.get("reason_code")
    amount = decision.get("amount_minor") or 0

    if outcome == "refund_succeeded":
        return (
            f"Good news: your refund of {format_inr(amount)} for order {order_id} has been confirmed by our "
            f"payment provider (reference {state.get('external_result_reference')}). It usually reaches your "
            "original payment method within 5 to 7 business days."
        )
    if outcome == "refund_processing":
        return (
            f"Your refund of {format_inr(amount)} for order {order_id} has been submitted to our payment provider "
            "and is being processed. We have not yet received final confirmation; we will update this ticket "
            "when it is confirmed."
        )
    if outcome == "refund_rejected":
        return (
            f"A support reviewer looked at the refund request for order {order_id} and could not approve it at "
            "this time. No refund has been issued. A member of our team can explain the decision if you reply "
            "to this ticket."
        )
    if outcome == "approval_expired":
        return (
            f"Your refund request for order {order_id} is still waiting for a review and has been passed to a "
            "specialist. No refund has been issued yet."
        )
    if outcome == "escalated":
        reason = state.get("escalation_reason") or ""
        if reason.startswith("refund_outcome_unknown"):
            return (
                f"We sent the refund for order {order_id} to our payment provider but could not yet confirm the "
                "result. We have not issued a second refund; a specialist is checking with the provider and will "
                "update you."
            )
        if reason == "shipping_unavailable":
            return (
                f"I couldn't get the latest tracking information for order {order_id} right now. I've passed your "
                "ticket to our logistics team, who will follow up with the current status."
            )
        if code == "shipment_delayed" or code == "shipment_exception":
            return (
                f"Order {order_id} is delayed according to the carrier. I've escalated it to our logistics team, "
                "who will contact the carrier and follow up with you."
            )
        if code == "replacement_requested":
            return (
                f"Thanks for the details about the damaged {_items(order)}. I've prepared your request for a "
                "replacement and passed it to our fulfilment team, who will contact you."
            )
        return (
            f"I've verified the details of order {order_id} and passed your request to a specialist who will "
            "follow up with you. No changes have been made to your order or payment yet."
        )
    # answered
    if code == "already_refunded":
        return (
            f"Order {order_id} has already been refunded in full ({format_inr(order.get('refunded_amount_minor', 0))})."
        )
    if code == "refund_in_progress":
        return (
            f"A refund for order {order_id} is already being processed with our payment provider. You don't need "
            "to do anything; we'll update this ticket once it is confirmed."
        )
    if code == "return_not_received":
        return (
            f"We haven't received the returned {_items(order)} from order {order_id} at our warehouse yet. The "
            "refund starts once it arrives and passes inspection."
        )
    if code == "inspection_pending":
        return (
            f"Your returned {_items(order)} (order {order_id}) has arrived and is awaiting inspection. Once it "
            "passes, the refund will be reviewed."
        )
    if code == "inspection_failed":
        return (
            f"The returned {_items(order)} from order {order_id} did not pass our inspection, so it is not "
            "eligible for a refund. Reply to this ticket if you'd like the inspection notes reviewed."
        )
    if code == "return_rejected":
        return (
            f"The return for order {order_id} was rejected at our warehouse, so a refund can't be issued. Reply "
            "to this ticket if you'd like a specialist to review it."
        )
    if code == "return_not_started":
        return (
            f"There's no return in progress for order {order_id} yet. Please start a return from your order page; "
            "once we receive and inspect the item, the refund can be reviewed."
        )
    if code == "outside_return_window":
        return (
            f"The return for order {order_id} was requested after the return window in our refund policy, so it "
            "is not eligible for a refund."
        )
    if code == "order_not_delivered":
        return f"Order {order_id} hasn't been delivered yet, so it can't be refunded as a return."
    if code == "nothing_refundable":
        return f"There is no refundable balance left on order {order_id}."
    shipment = facts.get("shipment") or {}
    if code == "delivered":
        when = order.get("delivered_at") or (shipment.get("events") or [{}])[-1].get("at")
        return f"Order {order_id} ({_items(order)}) was delivered on {_date(when)}, according to the carrier."
    if code in ("in_transit", "out_for_delivery", "label_created", "running_late", "not_shipped", "order_cancelled"):
        latest = (shipment.get("events") or [{}])[-1]
        eta = shipment.get("estimated_delivery_at")
        text = {
            "in_transit": f"Order {order_id} ({_items(order)}) is in transit with {shipment.get('carrier')}.",
            "out_for_delivery": f"Order {order_id} ({_items(order)}) is out for delivery today.",
            "label_created": f"Order {order_id} is packed and labelled; the carrier hasn't collected it yet.",
            "running_late": f"Order {order_id} is running slightly behind the carrier's estimate.",
            "not_shipped": f"Order {order_id} is being prepared and hasn't shipped yet.",
            "order_cancelled": f"Order {order_id} was cancelled.",
        }[code]
        if latest.get("location") and code not in ("not_shipped", "order_cancelled"):
            text += f" Latest update: {latest.get('status', '').replace('_', ' ')} at {latest['location']}"
            text += f" on {_date(latest.get('at'))}."
        if eta and code not in ("not_shipped", "order_cancelled"):
            text += f" Estimated delivery: {_date(eta)}."
        return text
    return f"I've reviewed order {order_id}. {decision.get('explanation', '')}".strip()


_CLAIM_SUCCESS = re.compile(
    r"\b(refund(ed)?\b.{0,40}\b(has been|was|is)\s+(issued|processed|completed|credited|sent|"
    r"confirmed)|we('ve| have) (refunded|issued (you )?a refund))",
    re.I,
)
_NEGATION = re.compile(r"\b(no|not|never|nor)\b\s*$", re.I)  # "No refund has been issued" is not a claim
_AMOUNT = re.compile(r"(?:₹|INR|Rs\.?)\s?([\d,]+(?:\.\d{1,2})?)", re.I)


def check_grounding(
    message: str, *, outcome: str | None, allowed_amounts: set[int], required_refs: list[str]
) -> list[str]:
    problems = []
    if outcome != "refund_succeeded" and any(
        not _NEGATION.search(message[max(0, m.start() - 12) : m.start()]) for m in _CLAIM_SUCCESS.finditer(message)
    ):
        problems.append("claims a completed refund that is not verified")
    for m in _AMOUNT.finditer(message):
        value = round(float(m.group(1).replace(",", "")) * 100)
        if value not in allowed_amounts:
            problems.append(f"mentions unverified amount {m.group(0)}")
    for ref in required_refs:
        if ref and ref not in message:
            problems.append(f"drops reference {ref}")
    return problems
