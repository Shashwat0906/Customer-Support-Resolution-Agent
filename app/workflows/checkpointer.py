"""LangGraph Postgres checkpointer, isolated in the `langgraph` schema.

Checkpoints make the *workflow* durable (pause/resume, crash recovery). They
are not a financial ledger: refund state always comes from `refund_operations`
and the payment provider.

The schema is selected with ``SET search_path`` right after each connection is
opened, not with a ``-c search_path=...`` startup option: managed poolers such
as Neon's (PgBouncer) reject startup ``options``. A session-level setting needs
a direct (unpooled) connection, which ``Settings`` enforces for Neon URLs.
"""

from __future__ import annotations

from functools import lru_cache

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

SCHEMA = "langgraph"
CONNECT_TIMEOUT_SECONDS = 10


def _use_checkpoint_schema(conn: psycopg.Connection) -> None:
    # Connections are autocommit, so this persists for the life of the session.
    conn.execute(f"SET search_path TO {SCHEMA}")


@lru_cache
def _pool() -> ConnectionPool:
    settings = get_settings()
    return ConnectionPool(
        settings.psycopg_dsn,
        min_size=1,
        max_size=max(2, settings.db_pool_size),
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        },
        configure=_use_checkpoint_schema,
        # Validate connections before use: serverless databases close idle connections.
        check=ConnectionPool.check_connection,
        open=True,
    )


@lru_cache
def get_checkpointer() -> PostgresSaver:
    return PostgresSaver(_pool())  # type: ignore[arg-type]


def setup_checkpointer() -> None:
    with psycopg.connect(get_settings().psycopg_dsn, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    get_checkpointer().setup()
