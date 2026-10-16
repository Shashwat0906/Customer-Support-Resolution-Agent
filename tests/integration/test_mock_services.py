"""Mock payment / shipping / ticketing contract, including fault injection."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from tests.conftest import MOCK_ROOT, add_fault

PAY = {"Authorization": "Bearer dev-payments-key"}
BODY = {"payment_reference": "pay_ord5001", "amount_minor": 899_900, "currency": "INR", "reason": "test"}


def create(key: str, body: dict | None = None, timeout: float = 5) -> httpx.Response:
    return httpx.post(
        f"{MOCK_ROOT}/payments/refunds", json=body or BODY, timeout=timeout, headers={**PAY, "Idempotency-Key": key}
    )


def test_requires_provider_credentials(anchor: datetime) -> None:
    r = httpx.get(f"{MOCK_ROOT}/payments/charges/pay_ord5001")
    assert r.status_code == 401


def test_same_key_same_payload_returns_same_refund(anchor: datetime) -> None:
    first = create("key-aaaa-1")
    second = create("key-aaaa-1")
    assert first.status_code == 201 and second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true"
    assert first.json()["id"] == second.json()["id"]
    charge = httpx.get(f"{MOCK_ROOT}/payments/charges/pay_ord5001", headers=PAY).json()
    assert charge["refunded_minor"] == 899_900 and len(charge["refunds"]) == 1


def test_same_key_different_payload_rejected(anchor: datetime) -> None:
    assert create("key-bbbb-1", {**BODY, "amount_minor": 100}).status_code == 201
    r = create("key-bbbb-1", {**BODY, "amount_minor": 200})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "idempotency_key_reused"


def test_provider_enforces_refundable_balance(anchor: datetime) -> None:
    assert create("key-cccc-1").status_code == 201
    r = create("key-cccc-2")
    assert r.status_code == 422 and r.json()["detail"]["error"] == "insufficient_refundable_amount"


def test_lookup_by_key(anchor: datetime) -> None:
    assert (
        httpx.get(f"{MOCK_ROOT}/payments/refunds", params={"idempotency_key": "key-dddd-1"}, headers=PAY).status_code
        == 404
    )
    created = create("key-dddd-1").json()
    found = httpx.get(f"{MOCK_ROOT}/payments/refunds", params={"idempotency_key": "key-dddd-1"}, headers=PAY).json()
    assert found["id"] == created["id"]


def test_commit_then_timeout_fault(anchor: datetime) -> None:
    add_fault(target="payments.create_refund", mode="commit_then_timeout", delay_ms=1500)
    with pytest.raises(httpx.TimeoutException):
        create("key-eeee-1", timeout=0.5)
    # The provider accepted it despite the caller's timeout.
    found = httpx.get(f"{MOCK_ROOT}/payments/refunds", params={"idempotency_key": "key-eeee-1"}, headers=PAY)
    assert found.status_code == 200 and found.json()["status"] == "succeeded"


def test_error_fault_does_no_work_and_is_consumed(anchor: datetime) -> None:
    add_fault(target="payments.create_refund", mode="error", status_code=503, count=1)
    assert create("key-ffff-1").status_code == 503
    assert (
        httpx.get(f"{MOCK_ROOT}/payments/refunds", params={"idempotency_key": "key-ffff-1"}, headers=PAY).status_code
        == 404
    )
    assert create("key-ffff-1").status_code == 201


def test_processing_settles_after_polls(anchor: datetime) -> None:
    add_fault(target="payments.create_refund", mode="processing", settle_after=2)
    rid = create("key-gggg-1").json()
    assert rid["status"] == "processing"
    statuses = [httpx.get(f"{MOCK_ROOT}/payments/refunds/{rid['id']}", headers=PAY).json()["status"] for _ in range(2)]
    assert statuses == ["processing", "succeeded"]


def test_shipping_tracking_and_fault(anchor: datetime) -> None:
    r = httpx.get(f"{MOCK_ROOT}/shipping/shipments/TRK5002IN")
    assert r.status_code == 200 and r.json()["status"] == "in_transit"
    add_fault(target="shipping.get_shipment", mode="error", status_code=503)
    assert httpx.get(f"{MOCK_ROOT}/shipping/shipments/TRK5002IN").status_code == 503
    assert httpx.get(f"{MOCK_ROOT}/shipping/shipments/TRK5002IN").status_code == 200


def test_ticket_notes_idempotent(anchor: datetime) -> None:
    httpx.put(
        f"{MOCK_ROOT}/ticketing/tickets/t1",
        json={"customer_ref": "C", "status": "open", "category": "x", "summary": "s"},
    )
    for _ in range(2):
        httpx.post(
            f"{MOCK_ROOT}/ticketing/tickets/t1/notes",
            json={"body": "hello", "status": "resolved"},
            headers={"Idempotency-Key": "note-key-1"},
        )
    t = httpx.get(f"{MOCK_ROOT}/ticketing/tickets/t1").json()
    assert len(t["notes"]) == 1 and t["status"] == "resolved"


def test_seeded_processing_refund(anchor: datetime) -> None:
    charge = httpx.get(f"{MOCK_ROOT}/payments/charges/pay_ord5020", headers=PAY).json()
    assert charge["refund_pending_minor"] == 899_900
