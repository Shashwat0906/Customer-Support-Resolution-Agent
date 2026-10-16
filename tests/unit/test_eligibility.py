"""Deterministic eligibility rules (no database, no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.policies import rules
from app.schemas.domain import (
    ItemFacts,
    OrderFacts,
    PaymentStatusFacts,
    RefundRecord,
    ReturnFacts,
    ReturnStatusFacts,
    ShipmentFacts,
)
from app.services.eligibility import DamageReport, evaluate_damage, evaluate_refund, evaluate_shipping

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def order(**kw: object) -> OrderFacts:
    base: dict[str, object] = dict(
        order_id="ORD-1",
        status="delivered",
        currency="INR",
        paid_amount_minor=899_900,
        refunded_amount_minor=0,
        reserved_refund_minor=0,
        refundable_balance_minor=899_900,
        placed_at=NOW - timedelta(days=16),
        delivered_at=NOW - timedelta(days=12),
        items=[ItemFacts(product_id="P", name="Headphones", category="audio", quantity=1, paid_amount_minor=899_900)],
    )
    base.update(kw)
    if "refundable_balance_minor" not in kw:
        base["refundable_balance_minor"] = (
            int(base["paid_amount_minor"])
            - int(base["refunded_amount_minor"])  # type: ignore[call-overload]
            - int(base["reserved_refund_minor"])
        )  # type: ignore[call-overload]
    return OrderFacts(**base)  # type: ignore[arg-type]


def ret(
    status: str = "received", inspection: str = "passed", requested_days_after_delivery: int = 4
) -> ReturnStatusFacts:
    delivered = NOW - timedelta(days=12)
    return ReturnStatusFacts(
        order_id="ORD-1",
        has_return=True,
        latest=ReturnFacts(
            return_id="RET-1",
            status=status,
            requested_at=delivered + timedelta(days=requested_days_after_delivery),
            received_at=NOW - timedelta(days=3) if status == "received" else None,
            inspection_status=inspection,
        ),
    )


NO_RETURN = ReturnStatusFacts(order_id="ORD-1", has_return=False)


def payment(**kw: object) -> PaymentStatusFacts:
    base: dict[str, object] = dict(
        order_id="ORD-1",
        payment_reference="pay",
        method="upi",
        amount_minor=899_900,
        status="captured",
        provider_refunded_minor=0,
        provider_refund_pending_minor=0,
    )
    base.update(kw)
    return PaymentStatusFacts(**base)  # type: ignore[arg-type]


def test_eligible_when_received_and_passed() -> None:
    d = evaluate_refund(order(), ret(), payment(), NOW)
    assert d.disposition == "eligible"
    assert d.amount_minor == 899_900
    assert d.policy_document_id == "refund-eligibility-v2"
    assert not d.requires_senior_review


@pytest.mark.parametrize(
    ("returns", "expected_disposition", "code"),
    [
        (NO_RETURN, "blocked", "return_not_started"),
        (ret(status="in_transit", inspection="not_started"), "processing", "return_not_received"),
        (ret(inspection="pending"), "processing", "inspection_pending"),
        (ret(inspection="failed"), "blocked", "inspection_failed"),
        (ret(status="rejected", inspection="failed"), "blocked", "return_rejected"),
        (ret(inspection="needs_review"), "escalate", "inspection_needs_review"),
        (ret(requested_days_after_delivery=25), "blocked", "outside_return_window"),
    ],
)
def test_return_states(returns: ReturnStatusFacts, expected_disposition: str, code: str) -> None:
    d = evaluate_refund(order(), returns, payment(), NOW)
    assert (d.disposition, d.reason_code) == (expected_disposition, code)
    assert d.amount_minor == 0


def test_v1_window_is_30_days_for_older_orders() -> None:
    delivered = datetime(2026, 3, 6, tzinfo=UTC)
    o = order(delivered_at=delivered, placed_at=delivered - timedelta(days=4))
    r = ReturnStatusFacts(
        order_id="ORD-1",
        has_return=True,
        latest=ReturnFacts(
            return_id="R",
            status="received",
            requested_at=delivered + timedelta(days=25),
            received_at=delivered + timedelta(days=28),
            inspection_status="passed",
        ),
    )
    d = evaluate_refund(o, r, payment(), NOW)
    assert d.disposition == "eligible" and d.policy_version == "1.0"


def test_no_policy_for_orders_before_first_version() -> None:
    delivered = datetime(2023, 11, 14, tzinfo=UTC)
    d = evaluate_refund(order(delivered_at=delivered, placed_at=delivered), ret(), payment(), NOW)
    assert (d.disposition, d.reason_code) == ("escalate", "no_applicable_policy")


def test_conflicting_policy_versions_escalate(monkeypatch: pytest.MonkeyPatch) -> None:
    overlapping = rules.RefundRules(
        "refund-eligibility-v2b", "2.1", rules.REFUND_RULES[1].effective_from, None, 10, True, 1
    )
    monkeypatch.setattr(rules, "REFUND_RULES", (*rules.REFUND_RULES, overlapping))
    d = evaluate_refund(order(), ret(), payment(), NOW)
    assert (d.disposition, d.reason_code) == ("escalate", "conflicting_policy")


def test_already_refunded_and_in_progress() -> None:
    assert (
        evaluate_refund(
            order(refunded_amount_minor=899_900), ret(), payment(provider_refunded_minor=899_900), NOW
        ).reason_code
        == "already_refunded"
    )
    assert (
        evaluate_refund(order(reserved_refund_minor=899_900), ret(), payment(), NOW).reason_code == "refund_in_progress"
    )
    in_ledger = payment(ledger_refunds=[RefundRecord(operation_id="op", amount_minor=1, status="unknown")])
    assert evaluate_refund(order(), ret(), in_ledger, NOW).disposition == "processing"


def test_provider_ledger_mismatch_escalates() -> None:
    d = evaluate_refund(order(), ret(), payment(provider_refunded_minor=100), NOW)
    assert (d.disposition, d.reason_code) == ("escalate", "ledger_provider_mismatch")


def test_not_delivered_is_blocked() -> None:
    d = evaluate_refund(order(status="shipped", delivered_at=None), NO_RETURN, payment(), NOW)
    assert d.reason_code == "order_not_delivered"


def test_senior_review_threshold() -> None:
    d = evaluate_refund(order(paid_amount_minor=5_499_900), ret(), payment(amount_minor=5_499_900), NOW)
    assert d.disposition == "eligible" and d.requires_senior_review


def test_damage_needs_details_then_eligible() -> None:
    o = order(delivered_at=NOW - timedelta(days=3))
    missing = evaluate_damage(o, DamageReport(), payment(), NOW)
    assert missing.disposition == "needs_info"
    assert missing.missing_information == ["damage_description", "photo_confirmation"]
    ok = evaluate_damage(o, DamageReport(description="cracked case", photos_available=True), payment(), NOW)
    assert ok.disposition == "eligible" and ok.amount_minor == 899_900
    repl = evaluate_damage(
        o, DamageReport(description="x", photos_available=True, preference="replacement"), payment(), NOW
    )
    assert repl.reason_code == "replacement_requested" and repl.disposition == "escalate"
    no_photo = evaluate_damage(o, DamageReport(description="x", photos_available=False), payment(), NOW)
    assert no_photo.disposition == "escalate"


def test_damage_reported_late_escalates() -> None:
    d = evaluate_damage(
        order(delivered_at=NOW - timedelta(days=25)),
        DamageReport(description="x", photos_available=True),
        payment(),
        NOW,
    )
    assert (d.disposition, d.reason_code) == ("escalate", "damage_reported_late")


def shipment(status: str, eta_days: float) -> ShipmentFacts:
    return ShipmentFacts(
        order_id="ORD-1",
        tracking_number="T",
        carrier="C",
        status=status,
        estimated_delivery_at=NOW + timedelta(days=eta_days),
        events=[],
    )


@pytest.mark.parametrize(
    ("status", "eta", "disposition", "code"),
    [
        ("in_transit", 2, "answer", "in_transit"),
        ("out_for_delivery", 0.3, "answer", "out_for_delivery"),
        ("label_created", 4, "answer", "label_created"),
        ("in_transit", -1.5, "answer", "running_late"),
        ("in_transit", -4.5, "escalate", "shipment_delayed"),
        ("exception", -3, "escalate", "shipment_exception"),
    ],
)
def test_shipping(status: str, eta: float, disposition: str, code: str) -> None:
    o = order(status="shipped", delivered_at=None)
    d = evaluate_shipping(o, shipment(status, eta), NOW)
    assert (d.disposition, d.reason_code) == (disposition, code)


def test_rules_agree_with_policy_text() -> None:
    """The numbers in the deterministic rules match the published policy documents."""
    from pathlib import Path

    docs = Path("app/policies/documents")
    for r in rules.REFUND_RULES:
        text = (docs / f"{r.document_id}.md").read_text()
        assert f"within {r.return_window_days} days" in text
        assert f"INR {r.senior_review_threshold_minor // 100:,}" in text
    for d in rules.DAMAGE_RULES:
        assert f"within {d.report_window_days} days" in (docs / f"{d.document_id}.md").read_text()
    for s in rules.SHIPPING_RULES:
        assert f"more than {s.escalate_after_days_late} days" in (docs / f"{s.document_id}.md").read_text()
