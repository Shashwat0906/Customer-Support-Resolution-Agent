"""Shared dependencies for mock service routers."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException, Request

from mock_services.faults import FaultRegistry
from mock_services.store import Store


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_faults(request: Request) -> FaultRegistry:
    return request.app.state.faults


def require_provider_key(authorization: str = Header(default="")) -> None:
    """Simulates provider credentials. The application holds the key server-side."""
    expected = os.environ.get("PAYMENTS_API_KEY", "dev-payments-key")
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail={"error": "invalid_api_key"})


def require_admin(x_admin_token: str = Header(default="")) -> None:
    expected = os.environ.get("MOCK_ADMIN_TOKEN", "dev-mock-admin-token")
    if x_admin_token != expected:
        raise HTTPException(status_code=401, detail={"error": "invalid_admin_token"})
