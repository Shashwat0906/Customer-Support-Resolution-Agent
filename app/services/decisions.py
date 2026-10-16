"""Assemble verified facts and run the eligibility engine.

Used both when a proposal is created and, again, immediately before execution
(revalidation under the order row lock).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from app.schemas.domain import OrderFacts, PaymentStatusFacts
from app.services import eligibility, records
from app.services.eligibility import DamageReport, Decision

Basis = Literal["return", "damage"]


def decide_refund(
    session: Session,
    customer_id: str,
    order_id: str,
    *,
    basis: Basis,
    provider_view: PaymentStatusFacts | None,
    now: datetime,
    damage_report: DamageReport | None = None,
    reported_at: datetime | None = None,
    lock: bool = False,
) -> tuple[Decision, OrderFacts]:
    order = records.load_owned_order(session, customer_id, order_id, for_update=lock)
    facts = records.order_facts(order)
    payment = None
    if provider_view is not None:
        # Always use the ledger as read inside *this* transaction.
        payment = provider_view.model_copy(update={"ledger_refunds": records.ledger_refunds(session, order_id)})
    if basis == "damage":
        decision = eligibility.evaluate_damage(facts, damage_report or DamageReport(), payment, reported_at or now)
    else:
        returns = records.get_return_status(session, customer_id, order_id)
        decision = eligibility.evaluate_refund(facts, returns, payment, now)
    return decision, facts
