"""Engine and session management."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

log = logging.getLogger(__name__)


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_size,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Explicit unit of work: commit on success, roll back on error."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Endpoints commit explicitly."""
    session = get_sessionmaker()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_db() -> None:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def wait_for_db(retries: int | None = None, delay: float = 1.5) -> None:
    """Bounded wait for the database (startup ordering on slow hosts)."""
    attempts = get_settings().db_connect_retries if retries is None else retries
    for attempt in range(attempts + 1):
        try:
            check_db()
            return
        except OperationalError as exc:
            if attempt >= attempts:
                raise RuntimeError(f"Database unavailable after {attempts + 1} attempts: {exc.orig}") from exc
            log.warning("database not ready (attempt %s/%s); retrying", attempt + 1, attempts + 1)
            time.sleep(delay)
