"""Shared test fixtures.

* A dedicated PostgreSQL test database, migrated with Alembic (TEST_DATABASE_URL).
* The mock external services running in a background uvicorn server on a real
  port, so timeouts and retries behave like the real thing.
* Every test starts from a freshly seeded dataset and cleared faults.
"""

from __future__ import annotations

import os
import socket
import tempfile
import threading
import time
from collections.abc import Iterator
from datetime import datetime

import httpx
import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


MOCK_PORT = _free_port()
MOCK_ROOT = f"http://127.0.0.1:{MOCK_PORT}"
TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://support:support@localhost:5432/support_agent_test")

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": TEST_DB,
        "PAYMENTS_BASE_URL": f"{MOCK_ROOT}/payments",
        "SHIPPING_BASE_URL": f"{MOCK_ROOT}/shipping",
        "TICKETING_BASE_URL": f"{MOCK_ROOT}/ticketing",
        "EXTERNAL_TIMEOUT_SECONDS": "1.0",
        "RETRY_BACKOFF_SECONDS": "0.01",
        "LLM_PROVIDER": "mock",
        "RUN_EMBEDDED_WORKER": "false",
        "MOCK_ADMIN_TOKEN": "dev-mock-admin-token",
        "PAYMENTS_API_KEY": "dev-payments-key",
    }
)
os.environ.pop("LLM_API_KEY", None)

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session")
def migrated_db() -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    engine = create_engine(TEST_DB)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("DROP SCHEMA IF EXISTS langgraph CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    from app.workflows.checkpointer import setup_checkpointer

    setup_checkpointer()
    from scripts.ingest_policies import ingest

    ingest()


@pytest.fixture(scope="session")
def mock_server() -> Iterator[str]:
    import uvicorn

    from mock_services.app_factory import create_app

    db_path = os.path.join(tempfile.mkdtemp(prefix="mocks-"), "mocks.sqlite3")
    config = uvicorn.Config(create_app(db_path), host="127.0.0.1", port=MOCK_PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            if httpx.get(f"{MOCK_ROOT}/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        raise RuntimeError("mock services did not start")
    yield MOCK_ROOT
    server.should_exit = True
    thread.join(timeout=5)


ADMIN = {"X-Admin-Token": "dev-mock-admin-token"}


@pytest.fixture
def anchor(migrated_db: None, mock_server: str) -> Iterator[datetime]:
    """Fresh seeded dataset in the DB and the mock services, sharing one anchor time."""
    from app.db.session import session_scope
    from app.llm import mock as mock_llm
    from app.services import faults
    from scripts.ingest_policies import ingest
    from scripts.seed_data import reset, seed

    ingest(prune=True)  # undo policy rows added by adversarial tests
    faults.disarm_all()
    mock_llm.disarm_all()
    with session_scope() as session:
        reset(session)
        anchor_time = seed(session)
    r = httpx.post(f"{mock_server}/admin/reset", json={"anchor": anchor_time.isoformat()}, headers=ADMIN, timeout=5)
    assert r.status_code == 200, r.text
    yield anchor_time
    faults.disarm_all()
    mock_llm.disarm_all()


@pytest.fixture
def client(anchor: datetime) -> Iterator[object]:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def login(client: object, subject: str) -> dict[str, str]:
    r = client.post("/auth/demo-login", json={"subject": subject})  # type: ignore[attr-defined]
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def customer_headers(client: object, customer_id: str) -> dict[str, str]:
    return login(client, f"demo|customer|{customer_id}")


def reviewer_headers(client: object, staff_id: str = "S-02") -> dict[str, str]:
    return login(client, f"demo|staff|{staff_id}")


def add_fault(**rule: object) -> None:
    r = httpx.post(f"{MOCK_ROOT}/admin/faults", json=rule, headers=ADMIN, timeout=5)
    assert r.status_code == 201, r.text


def provider_refunds() -> list[dict[str, object]]:
    return httpx.get(f"{MOCK_ROOT}/admin/refunds", headers=ADMIN, timeout=5).json()["refunds"]


def provider_tickets() -> dict[str, object]:
    return httpx.get(f"{MOCK_ROOT}/admin/tickets", headers=ADMIN, timeout=5).json()
