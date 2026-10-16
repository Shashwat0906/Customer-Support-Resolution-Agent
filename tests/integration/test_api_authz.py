"""Authentication and authorization on every endpoint."""

from __future__ import annotations

import pytest

from tests.conftest import customer_headers, reviewer_headers


def test_health(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["checks"]["database"] == "ok"
    assert body["checks"]["llm_provider"] == "mock"


def test_readiness_checks_dependencies(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/health/ready")
    assert r.status_code == 200, r.json()
    assert r.json()["checks"]["service_payments"] == "ok"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/tickets"),
        ("post", "/tickets"),
        ("get", "/tickets/x"),
        ("post", "/tickets/x/runs"),
        ("get", "/runs/x"),
        ("get", "/runs/x/events"),
        ("post", "/runs/x/messages"),
        ("get", "/approvals"),
        ("post", "/approvals/x/decision"),
        ("get", "/orders"),
        ("get", "/auth/me"),
        ("get", "/demo/faults"),
    ],
)
def test_every_endpoint_requires_auth(client, method: str, path: str) -> None:  # type: ignore[no-untyped-def]
    r = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_REQUIRED"


def test_invalid_and_forged_tokens_rejected(client) -> None:  # type: ignore[no-untyped-def]
    import jwt

    assert client.get("/auth/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    forged = jwt.encode(
        {
            "sub": "demo|customer|C-1001",
            "kind": "customer",
            "exp": 9999999999,
            "iss": "voltcart-support-demo",
            "aud": "voltcart-support-api",
        },
        "wrong-secret-" * 4,
        algorithm="HS256",
    )
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"


def test_customer_cannot_use_reviewer_endpoints(client) -> None:  # type: ignore[no-untyped-def]
    h = customer_headers(client, "C-1001")
    assert client.get("/approvals", headers=h).status_code == 403
    assert client.post("/approvals/apr_x/decision", json={"decision": "approve"}, headers=h).status_code == 403
    assert client.get("/demo/faults", headers=h).status_code == 403


def test_reviewer_cannot_impersonate_customer(client) -> None:  # type: ignore[no-untyped-def]
    h = reviewer_headers(client)
    r = client.post("/tickets", json={"message": "refund me"}, headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CUSTOMER_ONLY"


def test_cross_customer_order_is_indistinguishable_from_missing(client) -> None:  # type: ignore[no-untyped-def]
    h = customer_headers(client, "C-1001")
    foreign = client.post("/tickets", json={"message": "Where is my order?", "order_id": "ORD-5003"}, headers=h)
    missing = client.post("/tickets", json={"message": "Where is my order?", "order_id": "ORD-9999"}, headers=h)
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"]["code"] == missing.json()["error"]["code"] == "ORDER_NOT_FOUND"
    assert "ORD-5003" in foreign.json()["error"]["message"]  # echoes only what the caller sent
    assert "Priya" not in foreign.text


def test_customers_see_only_their_tickets(client) -> None:  # type: ignore[no-untyped-def]
    a = customer_headers(client, "C-1001")
    b = customer_headers(client, "C-1002")
    t = client.post("/tickets", json={"message": "Where is my cable?", "order_id": "ORD-5002"}, headers=a).json()
    assert client.get(f"/tickets/{t['ticket_id']}", headers=b).status_code == 404
    assert client.post(f"/tickets/{t['ticket_id']}/runs", headers=b).status_code == 404
    assert [x["ticket_id"] for x in client.get("/tickets", headers=b).json()] == []
    assert len(client.get("/tickets", headers=reviewer_headers(client)).json()) == 1


def test_validation_errors_are_structured(client) -> None:  # type: ignore[no-untyped-def]
    h = customer_headers(client, "C-1001")
    r = client.post("/tickets", json={"message": "", "order_id": "bad id!"}, headers=h)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


def test_identity_comes_from_token_not_body(client) -> None:  # type: ignore[no-untyped-def]
    h = customer_headers(client, "C-1001")
    r = client.post("/tickets", json={"message": "hi there", "customer_id": "C-1002"}, headers=h)
    assert r.status_code == 201 and r.json()["customer_id"] == "C-1001"


def test_start_run_returns_202_and_rejects_duplicates(client) -> None:  # type: ignore[no-untyped-def]
    h = customer_headers(client, "C-1001")
    t = client.post("/tickets", json={"message": "Where is my cable?", "order_id": "ORD-5002"}, headers=h).json()
    r = client.post(f"/tickets/{t['ticket_id']}/runs", headers=h)
    assert r.status_code == 202 and r.headers["Location"] == f"/runs/{r.json()['run_id']}"
    dup = client.post(f"/tickets/{t['ticket_id']}/runs", headers=h)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
    assert client.get(f"/runs/{r.json()['run_id']}", headers=h).json()["status"] == "queued"
