"""Durable job queue for workflow execution.

Jobs are claimed with ``FOR UPDATE SKIP LOCKED`` and a lease. If a worker dies,
its lease expires and ``requeue_expired`` makes the job claimable again (up to
``JOB_MAX_ATTEMPTS``); the graph then continues from its last checkpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import WorkflowJob, WorkflowRun
from app.db.session import session_scope


def enqueue(session: Session, run_id: str, kind: str, payload: dict[str, Any] | None = None) -> WorkflowJob:
    job = WorkflowJob(run_id=run_id, kind=kind, payload=payload or {}, status="queued")
    session.add(job)
    session.flush()
    return job


def claim(worker_id: str) -> WorkflowJob | None:
    settings = get_settings()
    now = datetime.now(UTC)
    with session_scope() as session:
        # Only one active job per run: skip runs that already have a running job.
        running_runs = select(WorkflowJob.run_id).where(WorkflowJob.status == "running")
        job = session.scalar(
            select(WorkflowJob)
            .where(
                WorkflowJob.status == "queued", WorkflowJob.available_at <= now, WorkflowJob.run_id.not_in(running_runs)
            )
            .order_by(WorkflowJob.available_at, WorkflowJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_by = worker_id
        job.lease_expires_at = now + timedelta(seconds=settings.job_lease_seconds)
        session.flush()
        session.expunge(job)
        return job


def finish(job_id: str, *, error: str | None = None, retry_in: float | None = None) -> None:
    with session_scope() as session:
        job = session.get(WorkflowJob, job_id, with_for_update=True)
        if job is None:
            return
        if error is None:
            job.status = "done"
        elif retry_in is not None and job.attempts < get_settings().job_max_attempts:
            job.status = "queued"
            job.available_at = datetime.now(UTC) + timedelta(seconds=retry_in)
        else:
            job.status = "failed"
        job.last_error = error
        job.lease_expires_at = None
        job.locked_by = None


def requeue_expired() -> list[str]:
    """Recover jobs whose worker died (lease expired). Returns affected job IDs."""
    now = datetime.now(UTC)
    max_attempts = get_settings().job_max_attempts
    with session_scope() as session:
        expired = session.scalars(
            select(WorkflowJob)
            .where(WorkflowJob.status == "running", WorkflowJob.lease_expires_at < now)
            .with_for_update(skip_locked=True)
        ).all()
        ids = []
        for job in expired:
            ids.append(job.id)
            job.locked_by = None
            job.lease_expires_at = None
            if job.attempts >= max_attempts:
                job.status = "failed"
                job.last_error = "lease expired too many times"
                session.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == job.run_id, WorkflowRun.status.not_in(("completed", "escalated")))
                    .values(status="failed", error_code="WORKER_RETRIES_EXHAUSTED")
                )
            else:
                job.status = "queued"
                job.last_error = "lease expired; re-queued for recovery"
        return ids


def has_open_job(session: Session, run_id: str) -> bool:
    return (
        session.scalar(
            select(WorkflowJob.id)
            .where(WorkflowJob.run_id == run_id, WorkflowJob.status.in_(("queued", "running")))
            .limit(1)
        )
        is not None
    )
