"""Helpers to drive whole workflows through the public API + worker."""

from __future__ import annotations

from typing import Any

from app.worker import drain
from tests.conftest import customer_headers, reviewer_headers


def start(
    client: Any, customer_id: str, message: str, order_id: str | None = None, agent_mode: str | None = None
) -> tuple[str, str, dict[str, str]]:
    h = customer_headers(client, customer_id)
    body: dict[str, Any] = {"message": message}
    if order_id:
        body["order_id"] = order_id
    r = client.post("/tickets", json=body, headers=h)
    assert r.status_code == 201, r.text
    ticket_id = r.json()["ticket_id"]
    r = client.post(f"/tickets/{ticket_id}/runs", json={"agent_mode": agent_mode} if agent_mode else None, headers=h)
    assert r.status_code == 202, r.text
    return ticket_id, r.json()["run_id"], h


def run_state(client: Any, run_id: str, headers: dict[str, str]) -> dict[str, Any]:
    r = client.get(f"/runs/{run_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def start_and_drain(
    client: Any, customer_id: str, message: str, order_id: str | None = None, agent_mode: str | None = None
) -> tuple[dict[str, Any], dict[str, str]]:
    _, run_id, h = start(client, customer_id, message, order_id, agent_mode)
    drain()
    return review_state(client, run_id), h


def decide(client: Any, approval_id: str, decision: str = "approve", staff_id: str = "S-02") -> Any:
    return client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": decision, "note": "test"},
        headers=reviewer_headers(client, staff_id),
    )


def reply(client: Any, run_id: str, headers: dict[str, str], message: str) -> Any:
    return client.post(f"/runs/{run_id}/messages", json={"message": message}, headers=headers)


def events(client: Any, run_id: str, headers: dict[str, str]) -> list[str]:
    return [e["event_type"] for e in client.get(f"/runs/{run_id}/events", headers=headers).json()]


def review_state(client: Any, run_id: str) -> dict[str, Any]:
    """Full run state as a reviewer sees it (includes retry counts, errors and metrics)."""
    return run_state(client, run_id, reviewer_headers(client))
