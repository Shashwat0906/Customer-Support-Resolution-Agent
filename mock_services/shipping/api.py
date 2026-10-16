"""Mock shipping carrier tracking API."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from mock_services.deps import get_faults, get_store
from mock_services.faults import FaultRegistry, apply_pre
from mock_services.store import Store

router = APIRouter(prefix="/shipping", tags=["mock-shipping"])


@router.get("/shipments/{tracking_number}")
def get_shipment(
    tracking_number: str, store: Store = Depends(get_store), faults: FaultRegistry = Depends(get_faults)
) -> dict[str, Any]:
    apply_pre(faults.take("shipping.get_shipment", tracking_number))
    rows = store.query("SELECT * FROM shipments WHERE tracking_number = ?", (tracking_number,))
    if not rows:
        raise HTTPException(status_code=404, detail={"error": "shipment_not_found"})
    row = rows[0]
    return {
        "tracking_number": row["tracking_number"],
        "order_id": row["order_id"],
        "carrier": row["carrier"],
        "status": row["status"],
        "estimated_delivery_at": row["estimated_delivery_at"],
        "events": json.loads(row["events"]),
    }
