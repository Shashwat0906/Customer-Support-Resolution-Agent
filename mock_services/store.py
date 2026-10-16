"""SQLite-backed storage shared by the mock services.

Records survive a mock-service restart, which matters for recovery tests: a
refund the provider accepted must still be discoverable by idempotency key
after the caller (or the provider) restarts.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from app.db import seed_dataset as ds

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    reference TEXT PRIMARY KEY, order_id TEXT NOT NULL, amount_minor INTEGER NOT NULL,
    currency TEXT NOT NULL, method TEXT NOT NULL, captured_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS refunds (
    id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
    payment_reference TEXT NOT NULL, order_id TEXT, amount_minor INTEGER NOT NULL, currency TEXT NOT NULL,
    status TEXT NOT NULL, reason TEXT, metadata TEXT NOT NULL, created_at TEXT NOT NULL,
    polls INTEGER NOT NULL DEFAULT 0, settle_after INTEGER NOT NULL DEFAULT 0,
    settle_to TEXT NOT NULL DEFAULT 'succeeded');
CREATE TABLE IF NOT EXISTS create_requests (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT, outcome TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shipments (
    tracking_number TEXT PRIMARY KEY, order_id TEXT NOT NULL, carrier TEXT NOT NULL, status TEXT NOT NULL,
    estimated_delivery_at TEXT, events TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY, customer_ref TEXT, status TEXT NOT NULL, category TEXT, summary TEXT,
    updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ticket_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL, idempotency_key TEXT UNIQUE NOT NULL,
    body TEXT NOT NULL, status TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)  # executescript manages its own transaction
        if not self.get_meta("anchor"):
            self.reset()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def get_meta(self, key: str) -> str | None:
        rows = self.query("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else None

    def reset(self, anchor: datetime | None = None) -> datetime:
        anchor = anchor or ds.default_anchor()
        with self.tx() as c:
            for table in ("payments", "refunds", "create_requests", "shipments", "tickets", "ticket_notes", "meta"):
                c.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
            for spec in ds.ORDERS:
                c.execute(
                    "INSERT INTO payments VALUES (?,?,?,?,?,?)",
                    (
                        ds.payment_reference(spec.id),
                        spec.id,
                        spec.paid_amount_minor,
                        "INR",
                        spec.method,
                        spec.placed.at(anchor).isoformat(),
                    ),
                )
                if spec.historic_refund:
                    h = spec.historic_refund
                    c.execute(
                        "INSERT INTO refunds (id, idempotency_key, request_hash, payment_reference, order_id, "
                        "amount_minor, currency, status, reason, metadata, created_at, settle_after) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            ds.historic_provider_reference(spec.id),
                            ds.historic_idempotency_key(spec.id),
                            "historic",
                            ds.payment_reference(spec.id),
                            spec.id,
                            spec.paid_amount_minor,
                            "INR",
                            h.status,
                            "historic",
                            json.dumps({"order_id": spec.id}),
                            h.created.at(anchor).isoformat(),
                            # a seeded "processing" refund settles after a few status polls
                            3 if h.status == "processing" else 0,
                        ),
                    )
                if spec.shipment:
                    s = spec.shipment
                    events = [
                        {"status": st, "at": when.at(anchor).isoformat(), "location": loc} for st, when, loc in s.events
                    ]
                    c.execute(
                        "INSERT INTO shipments VALUES (?,?,?,?,?,?)",
                        (
                            ds.tracking_number(spec.id),
                            spec.id,
                            s.carrier,
                            s.tracking_status,
                            s.eta.at(anchor).isoformat() if s.eta else None,
                            json.dumps(events),
                        ),
                    )
            c.execute("INSERT INTO meta VALUES ('anchor', ?)", (anchor.isoformat(),))
        return anchor
