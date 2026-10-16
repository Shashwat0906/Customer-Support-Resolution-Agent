# Implementation Plan — Customer Support Resolution Agent

This plan turns the project specification into ordered, verifiable phases. Each
phase ends with an acceptance criterion that is checked by automated tests
before the next phase starts. Status is tracked in
[`requirements-checklist.md`](requirements-checklist.md).

## 0. Starting point (inspected)

- Repository `customer-support-resolution-agent`, branch `claude/jolly-dijkstra-m2bh01`.
- The repository was empty (no commits, no files) — no existing frontend, so
  React + TypeScript (Vite) is used.
- Tooling available for development: Python 3.12 (via `uv`), Node 22,
  PostgreSQL 16, Docker 29.
- No LLM API key is configured. Consequence: the default LLM provider is a
  deterministic, rule-based **mock provider** so the full workflow, tests and
  evaluations run without paid services. The Anthropic and OpenAI-compatible
  adapters are implemented but cannot be verified against a live API here.

## 1. Key decisions (sensible documented defaults)

| Decision | Choice | Why |
|---|---|---|
| Python packaging | `uv` with `uv.lock` (pinned), non-packaged app | Reproducible installs, fast CI |
| DB driver | psycopg 3 (sync) for SQLAlchemy and LangGraph | One driver; sync code is simpler to reason about for transactions |
| Execution model | `POST /tickets/{id}/runs` returns `202`; jobs persisted in `workflow_jobs`; worker claims with `FOR UPDATE SKIP LOCKED` and a lease | Durable, restartable, works with one or many workers |
| Worker deployment | Separate `worker` container in Compose; embedded worker thread in the single deployed web service | Free hosting provides one process container |
| Checkpointer | `langgraph-checkpoint-postgres` in schema `langgraph`; app tables in `public` | Logical separation; checkpoint is not the ledger |
| Embeddings | Local deterministic feature-hashing embeddings (default) + optional OpenAI-compatible embeddings | No paid dependency, no large ML packages in a 512 MB container |
| Vector search | Cosine similarity in Python over version-filtered chunks + PostgreSQL full-text rank (hybrid) | Corpus is small; pgvector is optional and documented as future work |
| Mock services | One FastAPI process hosting payments, shipping and ticketing routers under separate prefixes, SQLite-backed so records survive restarts | Real HTTP, real timeouts, configurable faults |
| Auth | JWT (HS256) issued by a demo login endpoint for synthetic personas; `sub` maps to `external_auth_subject` | Realistic token flow without an external IdP; documented as demo-only |
| Money | Integer paise (`*_minor`), INR only | Spec requirement |
| Refund balance guard | `orders.refunded_amount_minor` + `orders.reserved_refund_minor` with a DB `CHECK` (≤ paid) and row lock | Enforced in DB, not only in code |
| Hosting | Render (workspace available): Docker web service (API + embedded worker + mock services) + static site (frontend) + PostgreSQL | Free tier, HTTPS, health checks, logs |

## 2. Phases

### Phase 1 — Business workflow and backend (Week 1)

Tasks
1. Project skeleton, settings with validation (`app/config.py`), logging.
2. SQLAlchemy models for all 12 specified entities + supporting tables
   (`payments`, `shipments`, `staff_users`, `ticket_messages`, `workflow_jobs`).
3. Alembic migration `0001` (constraints, indexes, CHECKs, unique idempotency key).
4. Deterministic synthetic seed data (`scripts/seed_data.py`).
5. Demo authentication (JWT) and role-based authorization (customer, reviewer).
6. Ticket endpoints, structured errors, OpenAPI.
7. Mock payment / shipping / ticketing services with fault injection.
8. Deterministic eligibility engine (`app/services/eligibility.py`) driven by
   versioned policy rules.
9. Refund operation service (reserve → call provider → persist → reconcile).

Acceptance criterion: an automated integration test resolves a sample refund
ticket purely through API/service calls without any LLM
(`tests/integration/test_manual_resolution.py`).

### Phase 2 — Agent and retrieval (Week 2)

Tasks
1. Synthetic policy documents (5 topics, 2 versions of the refund policy).
2. Ingestion: section chunking, embeddings, metadata (`scripts/ingest_policies.py`).
3. Retrieval with version filtering, citations, conflict / missing detection.
4. Typed read and write tools with structured errors and per-agent allowlists.
5. LLM provider abstraction: mock (deterministic), Anthropic, OpenAI-compatible;
   structured output validation and bounded repair.
6. Agents: single tool-using baseline; specialist investigation / policy /
   resolution agents.
7. LangGraph state (Pydantic) and routing; investigation and policy branches
   run concurrently after the order facts they depend on are loaded.
8. Worker, job queue, leases, step / cost limits.

Acceptance criterion: a workflow gathers evidence and produces a validated
proposal with citations (`tests/integration/test_workflow_refund.py`).

### Phase 3 — Approval and reliability (Week 3)

Tasks
1. Immutable proposal + payload hash, approval bound to hash, expiry.
2. `interrupt()` / `Command(resume=...)` pause/resume on the same thread ID.
3. Reviewer decision endpoint with permission and approval-limit checks.
4. Execution step separated from the interrupted node; revalidation; invalidation
   of approvals on material change.
5. Idempotent refund operations, reconciliation of unknown outcomes, release of
   reservations on definitive failure.
6. Ticket updates with bounded retry (only the ticket update is retried).
7. Audit trail with redaction; event timeline endpoint.
8. Recovery: lease expiry re-queue, reconciliation sweeper, approval-expiry sweeper.

Acceptance criterion: restarting or retrying a workflow does not create
duplicate refunds (`tests/failure_scenarios/`).

### Phase 4 — Evaluation and presentation (Week 4)

Tasks
1. React + TypeScript dashboard (customer, reviewer, ops/fault panel).
2. 60-case evaluation dataset with a held-out subset; harness with DB and
   mock-provider assertions; single-agent vs specialist comparison.
3. Dockerfiles, Compose with health checks, CI workflow.
4. README, architecture diagram, deployment guide, demo checklist, resume text.
5. Deployment to Render and the 20-step verification gate.

Acceptance criterion: another developer can reproduce setup, tests and demo
scenarios from the README alone.

## 3. Dependencies, risks and blockers

| Risk / blocker | Impact | Mitigation |
|---|---|---|
| No LLM API key | Real-LLM path unverifiable | Mock provider is the default; adapters unit-tested with stubbed transport; documented as unverified |
| Render free plan: one free PostgreSQL per workspace (already used by another project) | Cannot create a second free DB | Decide at deploy time; options documented in `docs/deployment.md` |
| Render free web services sleep after inactivity | Cold start ≈ 1 min; embedded worker paused while asleep | Jobs are durable; worker resumes on wake; documented |
| Free Render PostgreSQL expires after 30 days | Demo data loss | Migrations + seed are idempotent and re-runnable |
| LangGraph API drift (v1.x) | Interrupt semantics | Verified behaviour with a probe before design (resume re-runs the interrupted node) |
| Network side effects inside interrupted nodes | Duplicate refunds | Execution lives in its own node; operation keyed by proposal ID |
