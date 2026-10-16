"""Factory for the mock external services app (payments, shipping, ticketing)."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from mock_services.deps import get_faults, get_store, require_admin
from mock_services.faults import FaultRegistry, FaultRule
from mock_services.payments.api import router as payments_router
from mock_services.shipping.api import router as shipping_router
from mock_services.store import Store
from mock_services.ticketing.api import router as ticketing_router


class ResetRequest(BaseModel):
    anchor: datetime | None = None


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="VoltCart mock external services", version="1.0.0")
    app.state.store = Store(db_path or os.environ.get("MOCK_DB_PATH", "data/mock_services.sqlite3"))
    app.state.faults = FaultRegistry()
    app.include_router(payments_router)
    app.include_router(shipping_router)
    app.include_router(ticketing_router)

    @app.get("/health", tags=["admin"])
    def health(store: Store = Depends(get_store)) -> dict[str, Any]:
        store.query("SELECT 1")
        return {"status": "ok", "anchor": store.get_meta("anchor")}

    @app.post("/admin/reset", tags=["admin"], dependencies=[Depends(require_admin)])
    def reset(
        body: ResetRequest, store: Store = Depends(get_store), faults: FaultRegistry = Depends(get_faults)
    ) -> dict[str, Any]:
        faults.clear()
        anchor = store.reset(body.anchor)
        return {"status": "reset", "anchor": anchor.isoformat()}

    @app.get("/admin/faults", tags=["admin"], dependencies=[Depends(require_admin)])
    def list_faults(faults: FaultRegistry = Depends(get_faults)) -> dict[str, Any]:
        return {"rules": [r.model_dump() for r in faults.list()], "hits": faults.hits}

    @app.post("/admin/faults", tags=["admin"], status_code=201, dependencies=[Depends(require_admin)])
    def add_fault(rule: FaultRule, faults: FaultRegistry = Depends(get_faults)) -> dict[str, Any]:
        try:
            faults.add(rule)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"error": "invalid_fault", "message": str(exc)}) from exc
        return {"status": "added", "rule": rule.model_dump()}

    @app.delete("/admin/faults", tags=["admin"], dependencies=[Depends(require_admin)])
    def clear_faults(faults: FaultRegistry = Depends(get_faults)) -> dict[str, str]:
        faults.clear()
        return {"status": "cleared"}

    @app.get("/admin/refunds", tags=["admin"], dependencies=[Depends(require_admin)])
    def all_refunds(store: Store = Depends(get_store)) -> dict[str, Any]:
        return {
            "refunds": store.query(
                "SELECT id, idempotency_key, order_id, amount_minor, status, created_at FROM refunds"
            ),
            "create_requests": store.query("SELECT * FROM create_requests ORDER BY seq"),
        }

    @app.get("/admin/tickets", tags=["admin"], dependencies=[Depends(require_admin)])
    def all_tickets(store: Store = Depends(get_store)) -> dict[str, Any]:
        return {
            "tickets": store.query("SELECT * FROM tickets"),
            "notes": store.query("SELECT * FROM ticket_notes ORDER BY id"),
        }

    return app
