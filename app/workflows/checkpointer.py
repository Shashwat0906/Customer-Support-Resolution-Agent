"""LangGraph Postgres checkpointer, isolated in the `langgraph` schema.

Checkpoints make the *workflow* durable (pause/resume, crash recovery). They
are not a financial ledger: refund state always comes from `refund_operations`
and the payment provider.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import get_settings

SCHEMA = "langgraph"


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
            "options": f"-c search_path={SCHEMA}",
        },
        open=True,
    )


@lru_cache
def get_checkpointer() -> PostgresSaver:
    return PostgresSaver(_pool())  # type: ignore[arg-type]


def setup_checkpointer() -> None:
    import psycopg

    with psycopg.connect(get_settings().psycopg_dsn, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    get_checkpointer().setup()
