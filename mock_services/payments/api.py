"""Mock payment provider: captured payments and idempotent refunds.

Contract (mirrors common PSP behaviour):
* ``POST /refunds`` requires an ``Idempotency-Key`` header.
  - same key + same payload  -> returns the original refund (``200``, ``Idempotent-Replayed: true``)
  - same key + different payload -> ``409 idempotency_key_reused``
  - a new key creates a refund (``201``) if the refundable balance allows it
* ``GET /refunds?idempotency_key=...`` finds a refund by key (``404`` if the provider never saw it),
  so callers can reconcile uncertain outcomes before retrying.
* Retrying with the same key after a timeout is safe.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from mock_services.deps import get_faults, get_store, require_provider_key
from mock_services.faults import FaultRegistry, apply_post, apply_pre
from mock_services.store import Store, now_iso

router = APIRouter(prefix="/payments", tags=["mock-payments"], dependencies=[Depends(require_provider_key)])


class RefundRequest(BaseModel):
    payment_reference: str
    amount_minor: int = Field(gt=0)
    currency: Literal["INR"]
    reason: str = Field(max_length=500)
    metadata: dict[str, str] = Field(default_factory=dict)


def _request_hash(body: RefundRequest) -> str:
    material = {
        "payment_reference": body.payment_reference,
        "amount_minor": body.amount_minor,
        "currency": body.currency,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _public(refund: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": refund["id"],
        "idempotency_key": refund["idempotency_key"],
        "payment_reference": refund["payment_reference"],
        "order_id": refund["order_id"],
        "amount_minor": refund["amount_minor"],
        "currency": refund["currency"],
        "status": refund["status"],
        "created_at": refund["created_at"],
    }


def _advance_settlement(store: Store, refund: dict[str, Any]) -> dict[str, Any]:
    """A `processing` refund settles after `settle_after` status polls."""
    if refund["status"] != "processing":
        return refund
    with store.tx() as c:
        polls = refund["polls"] + 1
        status = refund["settle_to"] if polls >= refund["settle_after"] else "processing"
        c.execute("UPDATE refunds SET polls = ?, status = ? WHERE id = ?", (polls, status, refund["id"]))
    refund = dict(refund, polls=polls, status=status)
    return refund


@router.get("/charges/{payment_reference}")
def get_payment(
    payment_reference: str, store: Store = Depends(get_store), faults: FaultRegistry = Depends(get_faults)
) -> dict[str, Any]:
    apply_pre(faults.take("payments.get_payment", payment_reference))
    rows = store.query("SELECT * FROM payments WHERE reference = ?", (payment_reference,))
    if not rows:
        raise HTTPException(status_code=404, detail={"error": "payment_not_found"})
    payment = rows[0]
    refunds = [
        _public(r)
        for r in store.query(
            "SELECT * FROM refunds WHERE payment_reference = ? ORDER BY created_at", (payment_reference,)
        )
    ]
    refunded = sum(r["amount_minor"] for r in refunds if r["status"] == "succeeded")
    pending = sum(r["amount_minor"] for r in refunds if r["status"] == "processing")
    return {
        "reference": payment["reference"],
        "order_id": payment["order_id"],
        "status": "captured",
        "amount_minor": payment["amount_minor"],
        "currency": payment["currency"],
        "method": payment["method"],
        "captured_at": payment["captured_at"],
        "refunded_minor": refunded,
        "refund_pending_minor": pending,
        "refunds": refunds,
    }


@router.post("/refunds", status_code=201)
def create_refund(
    body: RefundRequest,
    response: Response,
    idempotency_key: str = Header(min_length=8, max_length=120),
    store: Store = Depends(get_store),
    faults: FaultRegistry = Depends(get_faults),
) -> dict[str, Any]:
    rule = faults.take("payments.create_refund", idempotency_key)
    if rule and rule.mode in ("error", "timeout", "delay"):
        try:
            apply_pre(rule)
        except HTTPException:
            store.query(
                "INSERT INTO create_requests (idempotency_key, outcome, created_at) VALUES (?,?,?)",
                (idempotency_key, f"fault:{rule.mode}", now_iso()),
            )
            raise
    req_hash = _request_hash(body)
    with store.tx() as c:
        existing = c.execute("SELECT * FROM refunds WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
        if existing is not None:
            existing = dict(existing)
            if existing["request_hash"] != req_hash:
                c.execute(
                    "INSERT INTO create_requests (idempotency_key, outcome, created_at) VALUES (?,?,?)",
                    (idempotency_key, "conflict", now_iso()),
                )
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "idempotency_key_reused",
                        "message": "This idempotency key was already used with a different payload.",
                    },
                )
            c.execute(
                "INSERT INTO create_requests (idempotency_key, outcome, created_at) VALUES (?,?,?)",
                (idempotency_key, "replayed", now_iso()),
            )
            response.status_code = 200
            response.headers["Idempotent-Replayed"] = "true"
            return _public(existing)
        payment = c.execute("SELECT * FROM payments WHERE reference = ?", (body.payment_reference,)).fetchone()
        if payment is None:
            raise HTTPException(status_code=404, detail={"error": "payment_not_found"})
        already = c.execute(
            "SELECT COALESCE(SUM(amount_minor), 0) FROM refunds WHERE payment_reference = ? AND status <> 'failed'",
            (body.payment_reference,),
        ).fetchone()[0]
        if already + body.amount_minor > payment["amount_minor"]:
            c.execute(
                "INSERT INTO create_requests (idempotency_key, outcome, created_at) VALUES (?,?,?)",
                (idempotency_key, "insufficient", now_iso()),
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "insufficient_refundable_amount",
                    "refundable_minor": payment["amount_minor"] - already,
                },
            )
        processing = rule is not None and rule.mode == "processing"
        refund_id = f"rf_{uuid.uuid4().hex[:14]}"
        c.execute(
            "INSERT INTO refunds (id, idempotency_key, request_hash, payment_reference, order_id, amount_minor, "
            "currency, status, reason, metadata, created_at, settle_after, settle_to) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                refund_id,
                idempotency_key,
                req_hash,
                body.payment_reference,
                payment["order_id"],
                body.amount_minor,
                body.currency,
                "processing" if processing else "succeeded",
                body.reason,
                json.dumps(body.metadata),
                now_iso(),
                rule.settle_after if processing and rule else 0,
                rule.settle_to if processing and rule else "succeeded",
            ),
        )
        c.execute(
            "INSERT INTO create_requests (idempotency_key, outcome, created_at) VALUES (?,?,?)",
            (idempotency_key, "created", now_iso()),
        )
    created = store.query("SELECT * FROM refunds WHERE id = ?", (refund_id,))[0]
    apply_post(rule)
    return _public(created)


@router.get("/refunds")
def lookup_refund(
    idempotency_key: str = Query(min_length=8),
    store: Store = Depends(get_store),
    faults: FaultRegistry = Depends(get_faults),
) -> dict[str, Any]:
    apply_pre(faults.take("payments.lookup_refund", idempotency_key))
    rows = store.query("SELECT * FROM refunds WHERE idempotency_key = ?", (idempotency_key,))
    if not rows:
        raise HTTPException(status_code=404, detail={"error": "refund_not_found"})
    return _public(_advance_settlement(store, rows[0]))


@router.get("/refunds/{refund_id}")
def get_refund(
    refund_id: str, store: Store = Depends(get_store), faults: FaultRegistry = Depends(get_faults)
) -> dict[str, Any]:
    apply_pre(faults.take("payments.get_refund", refund_id))
    rows = store.query("SELECT * FROM refunds WHERE id = ?", (refund_id,))
    if not rows:
        raise HTTPException(status_code=404, detail={"error": "refund_not_found"})
    return _public(_advance_settlement(store, rows[0]))
