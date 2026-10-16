"""Seed the deterministic synthetic dataset.

Usage:
    python -m scripts.seed_data            # seed only if the database is empty
    python -m scripts.seed_data --reset    # wipe application data + checkpoints, then seed

Historic refunds (already refunded / already processing orders) are seeded as
complete ledger chains: ticket -> run -> proposal -> approval -> refund operation,
so the ledger and order balance columns are always consistent.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import seed_dataset as ds
from app.db.models import (
    ActionProposal,
    Approval,
    Customer,
    Order,
    OrderItem,
    Payment,
    Product,
    RefundOperation,
    Return,
    Shipment,
    StaffUser,
    Ticket,
    WorkflowRun,
)
from app.db.session import session_scope, wait_for_db
from app.services.hashing import payload_hash

log = logging.getLogger("seed")

APP_TABLES = [
    "audit_events",
    "refund_operations",
    "approvals",
    "action_proposals",
    "workflow_jobs",
    "ticket_messages",
    "workflow_runs",
    "tickets",
    "shipments",
    "payments",
    "returns",
    "order_items",
    "orders",
    "products",
    "staff_users",
    "customers",
]


def reset(session: Session) -> None:
    session.execute(text("TRUNCATE " + ", ".join(APP_TABLES) + " RESTART IDENTITY CASCADE"))
    has_lg = session.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_schema='langgraph' AND table_name='checkpoints'")
    ).first()
    if has_lg:
        session.execute(text("TRUNCATE langgraph.checkpoints, langgraph.checkpoint_blobs, langgraph.checkpoint_writes"))


def _historic_chain(session: Session, spec: ds.OrderSpec, anchor: datetime) -> None:
    hist = spec.historic_refund
    assert hist is not None
    created = hist.created.at(anchor)
    amount = spec.paid_amount_minor
    ticket = Ticket(
        id=f"tkt_hist_{spec.id}",
        customer_id=spec.customer_id,
        order_id=spec.id,
        category="refund_request",
        status="resolved" if hist.status == "succeeded" else "in_progress",
        summary=f"Historic refund for {spec.id}",
    )
    # The historic run finished; a "processing" refund is still settling at the provider.
    run = WorkflowRun(
        id=f"run_hist_{spec.id}",
        ticket_id=ticket.id,
        thread_id=f"thread_hist_{spec.id}",
        status="completed",
        outcome="refund_succeeded" if hist.status == "succeeded" else "refund_processing",
        agent_mode="specialist",
        started_at=created,
        completed_at=created,
    )
    payload = {
        "action": "refund",
        "order_id": spec.id,
        "amount_minor": amount,
        "currency": "INR",
        "reason": "Historic refund (seed data)",
        "policy_references": [],
        "evidence_references": [],
    }
    proposal = ActionProposal(
        id=f"prp_hist_{spec.id}",
        run_id=run.id,
        order_id=spec.id,
        action_type="refund",
        revision=1,
        payload=payload,
        payload_hash=payload_hash(payload),
        status="executed",
    )
    approval = Approval(
        id=f"apr_hist_{spec.id}",
        proposal_id=proposal.id,
        proposal_payload_hash=proposal.payload_hash,
        reviewer_id="S-02",
        decision="approved",
        note="Seeded historic approval",
        expires_at=created + timedelta(days=1),
        decided_at=created,
    )
    op = RefundOperation(
        id=f"rfo_hist_{spec.id}",
        order_id=spec.id,
        proposal_id=proposal.id,
        approval_id=approval.id,
        amount_minor=amount,
        idempotency_key=ds.historic_idempotency_key(spec.id),
        provider_reference=ds.historic_provider_reference(spec.id),
        status=hist.status,
        attempts=1,
        completed_at=created if hist.status == "succeeded" else None,
    )
    for obj in (ticket, run, proposal, approval, op):
        session.add(obj)
        session.flush()


def seed(session: Session, anchor: datetime | None = None) -> datetime:
    anchor = anchor or ds.default_anchor()
    for pid, (name, category, price) in ds.PRODUCTS.items():
        session.add(Product(id=pid, name=name, category=category, price_minor=price))
    for cid, name, email in ds.CUSTOMERS:
        session.add(
            Customer(
                id=cid,
                external_auth_subject=ds.customer_subject(cid),
                name=name,
                email=email,
                phone=f"+91-98{cid[-4:]}0{cid[-2:]}",
            )
        )
    for sid, name, email, role, limit in ds.STAFF:
        session.add(
            StaffUser(
                id=sid,
                external_auth_subject=ds.staff_subject(sid),
                name=name,
                email=email,
                role=role,
                approval_limit_minor=limit,
            )
        )
    session.flush()

    for spec in ds.ORDERS:
        paid = spec.paid_amount_minor
        refunded = paid if spec.historic_refund and spec.historic_refund.status == "succeeded" else 0
        reserved = paid if spec.historic_refund and spec.historic_refund.status == "processing" else 0
        session.add(
            Order(
                id=spec.id,
                customer_id=spec.customer_id,
                status=spec.status,
                currency="INR",
                paid_amount_minor=paid,
                refunded_amount_minor=refunded,
                reserved_refund_minor=reserved,
                placed_at=spec.placed.at(anchor),
                delivered_at=spec.delivered.at(anchor) if spec.delivered else None,
            )
        )
        session.flush()
        for idx, (pid, qty) in enumerate(spec.items, start=1):
            session.add(
                OrderItem(
                    id=f"{spec.id}-L{idx}",
                    order_id=spec.id,
                    product_id=pid,
                    quantity=qty,
                    paid_amount_minor=ds.PRODUCTS[pid][2] * qty,
                )
            )
        session.add(
            Payment(
                id=f"PAY-{spec.id}",
                order_id=spec.id,
                provider_payment_reference=ds.payment_reference(spec.id),
                method=spec.method,
                amount_minor=paid,
                currency="INR",
                status="captured",
                captured_at=spec.placed.at(anchor),
            )
        )
        if spec.ret:
            r = spec.ret
            session.add(
                Return(
                    id=f"RET-{spec.id}",
                    order_id=spec.id,
                    status=r.status,
                    requested_at=r.requested.at(anchor),
                    received_at=r.received.at(anchor) if r.received else None,
                    inspection_status=r.inspection,
                    reason=r.reason,
                )
            )
        if spec.shipment:
            session.add(
                Shipment(
                    id=f"SHP-{spec.id}",
                    order_id=spec.id,
                    carrier=spec.shipment.carrier,
                    tracking_number=ds.tracking_number(spec.id),
                    estimated_delivery_at=spec.shipment.eta.at(anchor) if spec.shipment.eta else None,
                )
            )
        session.flush()
        if spec.historic_refund:
            _historic_chain(session, spec, anchor)
    return anchor


def seed_if_empty(session: Session) -> bool:
    if session.scalar(select(Customer.id).limit(1)):
        return False
    seed(session)
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="wipe application data and checkpoints first")
    args = parser.parse_args()
    wait_for_db()
    with session_scope() as session:
        if args.reset:
            reset(session)
            anchor = seed(session)
            log.info("database reset and seeded (anchor=%s)", anchor.isoformat())
        elif seed_if_empty(session):
            log.info("database seeded")
        else:
            log.info("database already contains data; use --reset to re-seed")


if __name__ == "__main__":
    main()
