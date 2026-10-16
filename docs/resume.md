# Resume-ready description

*Only verified features are listed. Numbers come from the repository's test suite and evaluation report.*

**Customer Support Resolution Agent** — Python, FastAPI, LangGraph, PostgreSQL, SQLAlchemy/Alembic, React/TypeScript, Docker, GitHub Actions

- Built an agentic support workflow that resolves order-status, refund and damaged-item complaints end to end: LLM
  agents investigate and cite versioned policies, while deterministic code enforces identity, ownership, eligibility,
  amounts and state transitions.
- Designed human-in-the-loop refunds with LangGraph `interrupt()`/resume on durable Postgres checkpoints, approvals
  bound to a SHA-256 hash of an immutable proposal, reviewer limits, expiry, and revalidation under row locks.
- Implemented crash-safe refund execution (reserve → commit → idempotent provider call → reconcile by idempotency key)
  with database-enforced balance constraints; verified zero duplicate refunds across timeouts, provider errors and
  worker crashes at four injection points.
- Ran investigation and policy agents as concurrent branches and benchmarked them against a single-agent baseline on a
  60-case evaluation (12 held out): 59/60 correct for both, 0 unauthorized actions, 0 duplicate refunds; specialists
  used 72% more model calls (mock LLM).
- Delivered a React/TypeScript dashboard (customer complaints, reviewer approval queue, evidence, citations, workflow
  timeline, fault injection), Docker Compose stack with health-gated startup, and CI running lint, type checks,
  migration round trips, 153 tests and the evaluation harness.
