"""One-shot setup: wait for the database, migrate, set up checkpoints, seed and ingest.

Usage:
    python -m scripts.bootstrap                 # idempotent: migrate + seed if empty + ingest
    python -m scripts.bootstrap --reset-demo    # also wipe and re-seed demo data and reset mock services

Safe to run on every deploy/start.
"""

from __future__ import annotations

import argparse
import logging

import httpx
from alembic import command
from alembic.config import Config

from app.config import get_settings
from app.db.session import session_scope, wait_for_db
from app.workflows.checkpointer import setup_checkpointer
from scripts.ingest_policies import ingest
from scripts.seed_data import reset, seed, seed_if_empty

log = logging.getLogger("bootstrap")


def reset_mocks(anchor_iso: str) -> None:
    settings = get_settings()
    root = settings.payments_base_url.rstrip("/").rsplit("/", 1)[0]
    try:
        r = httpx.post(
            f"{root}/admin/reset",
            json={"anchor": anchor_iso},
            timeout=10,
            headers={"X-Admin-Token": settings.mock_admin_token},
        )
        r.raise_for_status()
        log.info("mock services reset (anchor=%s)", anchor_iso)
    except httpx.HTTPError as exc:
        log.warning("could not reset mock services at %s: %s (they reseed themselves on first start)", root, exc)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-demo", action="store_true", help="wipe and re-seed demo data, reset mock services")
    args = parser.parse_args()

    wait_for_db()
    command.upgrade(Config("alembic.ini"), "head")
    log.info("migrations applied")
    setup_checkpointer()
    log.info("checkpoint schema ready")
    with session_scope() as session:
        if args.reset_demo:
            reset(session)
            anchor = seed(session)
            log.info("demo data reset and seeded")
        else:
            anchor = None
            log.info("seeded" if seed_if_empty(session) else "existing data kept")
    log.info("ingested %s policy chunks", ingest(prune=True))
    if anchor is not None:
        reset_mocks(anchor.isoformat())


if __name__ == "__main__":
    main()
