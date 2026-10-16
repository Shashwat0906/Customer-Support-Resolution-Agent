"""The authenticated customer's own orders (used by the complaint form)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import Principal, require_customer
from app.db.session import get_db
from app.services import records

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", summary="List the authenticated customer's orders")
def my_orders(principal: Principal = Depends(require_customer), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {
            "order_id": o.order_id,
            "status": o.status,
            "placed_at": o.placed_at,
            "delivered_at": o.delivered_at,
            "paid_amount_minor": o.paid_amount_minor,
            "items": [i.name for i in o.items],
        }
        for o in records.list_customer_orders(db, principal.id)
    ]
