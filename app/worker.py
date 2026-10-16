"""Workflow worker.

Run standalone (``python -m app.worker``) or embedded in the API process
(``RUN_EMBEDDED_WORKER=true``). Recovery is explicit, because checkpointing
does not schedule anything by itself:

* jobs whose lease expired (worker died) are re-queued (``jobs.requeue_expired``);
* a claimed job inspects the checkpoint: a pending interrupt + resume job ->
  ``Command(resume=...)``; otherwise ``invoke(None)`` continues from the last
  checkpoint, re-running the node that was in flight (all side-effecting nodes
  are idempotent);
* sweepers expire approvals, reconcile uncertain refunds and retry ticket syncs.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
import time
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from app.config import get_settings
from app.db.models import Ticket, TicketMessage, WorkflowRun
from app.db.session import session_scope, wait_for_db
from app.services import approvals, audit, jobs, refunds, tickets
from app.workflows.graph import get_graph
from app.workflows.nodes import write_snapshot
from app.workflows.transitions import TERMINAL, transition

log = logging.getLogger("worker")

OUTCOME_STATUS = {
    "answered": "completed",
    "refund_succeeded": "completed",
    "refund_processing": "completed",
    "refund_rejected": "rejected",
    "approval_expired": "expired",
    "escalated": "escalated",
}


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def _initial_state(run: WorkflowRun, ticket: Ticket, first_message: str) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "ticket_id": ticket.id,
        "authenticated_customer_id": ticket.customer_id,
        "order_id": ticket.order_id,
        "agent_mode": run.agent_mode,
        "customer_message": first_message,
        "customer_messages": [first_message],
        "reported_at": ticket.created_at,
    }


def _metrics(values: dict[str, Any], previous: dict[str, Any], active_ms: float) -> dict[str, Any]:
    retry_counts = values.get("retry_counts") or {}
    return {
        **previous,
        "model_calls": values.get("model_calls", 0),
        "tool_calls": values.get("tool_calls", 0),
        "tokens": values.get("tokens", 0),
        "estimated_cost_usd": round(values.get("estimated_cost_usd", 0.0), 6),
        "step_count": values.get("step_count", 0),
        "retries": sum(retry_counts.values()),
        "retry_counts": retry_counts,
        "unauthorized_tool_attempts": values.get("unauthorized_tool_attempts", 0),
        "proposal_corrections": values.get("proposal_corrections", 0),
        "active_ms": round(previous.get("active_ms", 0) + active_ms, 1),
    }


def run_job(job: Any) -> None:
    now = datetime.now(UTC)
    with session_scope() as s:
        run = s.get(WorkflowRun, job.run_id, with_for_update=True)
        assert run is not None
        if run.status in TERMINAL:
            return
        ticket = s.get(Ticket, run.ticket_id)
        assert ticket is not None
        metrics = dict(run.metrics or {})
        if run.waiting_since is not None:
            key = "waiting_approval_ms" if metrics.get("waiting_kind") == "approval" else "waiting_customer_ms"
            metrics[key] = round(metrics.get(key, 0) + (now - run.waiting_since).total_seconds() * 1000, 1)
            run.waiting_since = None
        transition(s, run, "running")
        run.started_at = run.started_at or now
        run.metrics = metrics
        first = (
            s.query(TicketMessage)
            .filter(TicketMessage.ticket_id == ticket.id, TicketMessage.author_type == "customer")
            .order_by(TicketMessage.created_at)
            .first()
        )
        initial = _initial_state(run, ticket, first.body if first else ticket.summary)
        thread_id = run.thread_id

    graph = get_graph()
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}, "recursion_limit": 250}
    snapshot = graph.get_state(config)
    started = time.monotonic()
    if not snapshot.values:
        graph.invoke(initial, config)
    elif snapshot.interrupts:
        if job.kind == "resume":
            graph.invoke(Command(resume=job.payload), config)
        # else: a recovered job found the graph already paused; just re-derive the waiting status below.
    elif snapshot.next:
        audit.record_now(
            run_id=job.run_id,
            ticket_id=None,
            actor="worker",
            event_type="run_recovered",
            payload={"resuming_at": list(snapshot.next), "job_kind": job.kind},
        )
        graph.invoke(None, config)
    active_ms = (time.monotonic() - started) * 1000
    _persist_after_invoke(job.run_id, graph.get_state(config), active_ms)


def _persist_after_invoke(run_id: str, snapshot: Any, active_ms: float) -> None:
    values = dict(snapshot.values)
    write_snapshot(values)
    now = datetime.now(UTC)
    with session_scope() as s:
        run = s.get(WorkflowRun, run_id, with_for_update=True)
        assert run is not None
        metrics = _metrics(values, dict(run.metrics or {}), active_ms)
        if snapshot.interrupts:
            kind = snapshot.interrupts[0].value.get("type")
            status = "awaiting_approval" if kind == "awaiting_approval" else "awaiting_customer"
            transition(s, run, status)
            run.waiting_since = now
            metrics["waiting_kind"] = "approval" if status == "awaiting_approval" else "customer"
            run.metrics = metrics
            return
        outcome = values.get("outcome") or "escalated"
        transition(s, run, OUTCOME_STATUS.get(outcome, "escalated"))
        run.outcome = outcome
        run.completed_at = now
        run.final_response = values.get("final_response")
        run.error_code = values.get("escalation_reason")
        metrics.pop("waiting_kind", None)
        run.metrics = metrics


def _fail_run(run_id: str, error: str) -> None:
    with session_scope() as s:
        run = s.get(WorkflowRun, run_id, with_for_update=True)
        if run is None or run.status in TERMINAL:
            return
        run.status = "failed"
        run.error_code = "WORKER_ERROR"
        run.completed_at = datetime.now(UTC)
        ticket = s.get(Ticket, run.ticket_id)
        if ticket is not None:
            ticket.status = "escalated"
        audit.record(
            s,
            run_id=run_id,
            ticket_id=run.ticket_id,
            actor="worker",
            event_type="run_failed",
            payload={"error": error[:500]},
        )


def process_one(wid: str | None = None) -> bool:
    """Claim and run one job. Returns False when the queue is empty."""
    job = jobs.claim(wid or worker_id())
    if job is None:
        return False
    try:
        run_job(job)
    except Exception as exc:  # SimulatedCrash (BaseException) deliberately propagates, like a dying process
        log.exception("job %s for run %s failed", job.id, job.run_id)
        attempts_left = job.attempts < get_settings().job_max_attempts
        jobs.finish(
            job.id, error=f"{type(exc).__name__}: {exc}", retry_in=2.0 * job.attempts if attempts_left else None
        )
        if not attempts_left:
            _fail_run(job.run_id, str(exc))
        return True
    jobs.finish(job.id)
    return True


def sweep() -> dict[str, int]:
    """Recovery sweepers; safe to run concurrently with workers."""
    requeued = jobs.requeue_expired()
    expired = approvals.expire_due()
    reconciled = refunds.reconcile_pending(stale_seconds=30)
    synced = tickets.retry_unsynced()
    if requeued or expired or reconciled or synced:
        log.info(
            "sweep: requeued=%s expired=%s reconciled=%s ticket_syncs=%s",
            len(requeued),
            len(expired),
            len(reconciled),
            synced,
        )
    return {"requeued": len(requeued), "expired": len(expired), "reconciled": len(reconciled), "ticket_syncs": synced}


def drain(max_jobs: int = 100) -> int:
    """Process jobs until the queue is empty (tests, evaluation, CLI --once)."""
    count = 0
    while count < max_jobs and process_one():
        count += 1
    return count


class EmbeddedWorker:
    """Background thread inside the API process (single-container deployments)."""

    def __init__(self, sweep_every: float = 15.0) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="embedded-worker", daemon=True)
        self._sweep_every = sweep_every

    def start(self) -> None:
        self._thread.start()
        log.info("embedded worker started")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def _loop(self) -> None:
        poll = get_settings().worker_poll_seconds
        last_sweep = 0.0
        while not self._stop.is_set():
            try:
                if time.monotonic() - last_sweep > self._sweep_every:
                    sweep()
                    last_sweep = time.monotonic()
                if not process_one():
                    self._stop.wait(poll)
            except Exception:
                log.exception("worker loop error; backing off")
                self._stop.wait(min(30.0, poll * 5))


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Workflow worker")
    parser.add_argument("--once", action="store_true", help="drain the queue once and exit")
    args = parser.parse_args()
    wait_for_db()
    if args.once:
        sweep()
        log.info("processed %s jobs", drain())
        return
    worker = EmbeddedWorker(sweep_every=10.0)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker.start()
    stop.wait()
    worker.stop()


if __name__ == "__main__":
    main()
