"""Deterministic, versioned policy parameters.

Policy *text* is retrieved (RAG) and cited; policy *decisions* use these typed
parameters, keyed by the same document IDs and versions as the policy
documents in ``app/policies/documents``. A consistency test asserts every rule
set has a matching document and that the numbers quoted in the text agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RefundRules:
    document_id: str
    version: str
    effective_from: date
    effective_to: date | None
    return_window_days: int
    requires_inspection_pass: bool
    senior_review_threshold_minor: int


@dataclass(frozen=True)
class DamageRules:
    document_id: str
    version: str
    effective_from: date
    effective_to: date | None
    report_window_days: int


@dataclass(frozen=True)
class ShippingRules:
    document_id: str
    version: str
    effective_from: date
    effective_to: date | None
    escalate_after_days_late: int


REFUND_RULES: tuple[RefundRules, ...] = (
    RefundRules(
        "refund-eligibility-v1",
        "1.0",
        date(2024, 1, 1),
        date(2026, 6, 30),
        return_window_days=30,
        requires_inspection_pass=True,
        senior_review_threshold_minor=3_000_000,
    ),
    RefundRules(
        "refund-eligibility-v2",
        "2.0",
        date(2026, 7, 1),
        None,
        return_window_days=21,
        requires_inspection_pass=True,
        senior_review_threshold_minor=3_000_000,
    ),
)

DAMAGE_RULES: tuple[DamageRules, ...] = (
    DamageRules("damaged-products-v1", "1.0", date(2024, 1, 1), None, report_window_days=7),
)

SHIPPING_RULES: tuple[ShippingRules, ...] = (
    ShippingRules("shipping-delays-v1", "1.0", date(2024, 1, 1), None, escalate_after_days_late=3),
)


def _applicable[T: (RefundRules, DamageRules, ShippingRules)](rules: tuple[T, ...], on: date) -> list[T]:
    return [r for r in rules if r.effective_from <= on and (r.effective_to is None or on <= r.effective_to)]


def refund_rules_for(on: date) -> list[RefundRules]:
    return _applicable(REFUND_RULES, on)


def damage_rules_for(on: date) -> list[DamageRules]:
    return _applicable(DAMAGE_RULES, on)


def shipping_rules_for(on: date) -> list[ShippingRules]:
    return _applicable(SHIPPING_RULES, on)
