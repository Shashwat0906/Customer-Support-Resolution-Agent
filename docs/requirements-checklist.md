# Requirements Checklist

Every requirement from the project specification, mapped to an implementation
task (phase), the place it is implemented, and how it is verified.

Status legend: **Verified** (implemented and checked by the named test / command),
**Implemented** (code exists, not independently verified), **Pending**,
**Blocked** (needs an external decision/credential), **Unverified** (cannot be
checked in this environment), **Out of scope** (explicit spec boundary).

> Statuses are updated as phases complete. This initial version is the plan.

## 1–3. Overview, business problem, FDE relevance

| ID | Requirement | Phase | Implementation | Verification | Status |
|---|---|---|---|---|---|
| R1.1 | Working app, not mockups (Python, FastAPI, LangGraph, PostgreSQL, SQLAlchemy, Alembic, Pydantic, React/TS, Docker, Pytest, GH Actions) | all | repo | CI + compose run | Pending |
| R1.2 | LLM provider via env vars | 2 | `app/llm/` | unit tests | Pending |
| R1.3 | Synthetic data + mock payment/shipping/ticketing | 1 | `scripts/seed_data.py`, `mock_services/` | integration tests | Pending |
| R2.1 | Identify authenticated customer | 1 | `app/auth/` | `tests/integration/test_auth.py` | Pending |
| R2.2 | Retrieve authorized order/return/payment records | 1–2 | `app/services/records.py`, `app/tools/` | tool tests | Pending |
| R2.3 | Find applicable refund policy | 2 | `app/policies/retrieval.py` | retrieval tests | Pending |
| R2.4 | Eligible / processing / blocked determination | 1 | `app/services/eligibility.py` | `tests/unit/test_eligibility.py` | Pending |
| R2.5 | Identify missing information | 2 | `decide` node | workflow tests | Pending |
| R2.6 | Propose resolution | 2 | resolution agent + proposals service | workflow tests | Pending |
| R2.7 | Human approval before refund | 3 | approvals service + interrupt | approval tests | Pending |
| R2.8 | Execute through mock payment API | 3 | `app/services/refunds.py` | refund tests | Pending |
| R2.9 | Verify external result | 3 | `verify_refund` node | refund tests | Pending |
| R2.10 | Update ticket + audit trail | 3 | `app/services/tickets.py`, `audit.py` | workflow tests | Pending |
| R2.11 | Explanation limited to verified evidence | 2–3 | `respond` node + response guard | grounding tests/evals | Pending |
| R2.12 | Model proposes, app decides and executes | 2–3 | architecture | adversarial tests | Pending |
| R3.1 | FDE objectives documented (workflows, integrations, risk controls, failures, metrics, LLM-vs-code rationale) | 4 | README, architecture.md | doc review | Pending |

## 4. Complaint types and scope

| ID | Requirement | Phase | Implementation | Verification | Status |
|---|---|---|---|---|---|
| R4.A | Order status: order + shipment status, latest verified info, ticket update | 2 | workflow | eval category `order_status` | Pending |
| R4.B | Refund: retrieve, deterministic eligibility, proposal, approval, revalidate, execute, verify, persist | 2–3 | workflow | eval `refund_eligible` | Pending |
| R4.C | Damaged item: order + policy, ask missing info, resolution or escalate | 2 | workflow | eval `missing_info` / damaged cases | Pending |
| R4.S | Scope boundaries (one store, INR, one issue/workflow, mocks, approval for every refund, in-app messaging, no real payments, no chargebacks/fraud/partial returns) documented in README | 4 | README | doc review | Pending |

## 5. Architecture

| ID | Requirement | Phase | Implementation | Verification | Status |
|---|---|---|---|---|---|
| R5.1 | FastAPI: requests, authn/z, workflow create/status, decisions, validation, errors | 1–3 | `app/api/` | API tests | Pending |
| R5.2 | LangGraph coordinates classification → escalation | 2 | `app/workflows/graph.py` | workflow tests | Pending |
| R5.3 | PostgreSQL stores all listed data incl. durable checkpoints | 1–2 | models + PostgresSaver | migration test | Pending |
| R5.4 | App tables and checkpoint tables logically separate | 2 | schema `langgraph` | migration test | Pending |
| R5.5 | Mock services with configurable errors, delays, timeouts, repeat requests | 1 | `mock_services/` | `tests/integration/test_mock_services.py` | Pending |
| R5.6 | pgvector optional | 2 | documented; local embeddings default | doc | Pending |

## 6. Agent design

| ID | Requirement | Phase | Implementation | Verification | Status |
|---|---|---|---|---|---|
| R6.1 | Single tool-using agent baseline with deterministic control | 2 | `app/agents/single.py` | workflow tests (`AGENT_MODE=single`) | Pending |
| R6.2 | Investigation agent → structured evidence summary | 2 | `app/agents/investigation.py` | unit tests | Pending |
| R6.3 | Policy agent → citations, versions, interpretation | 2 | `app/agents/policy.py` | unit tests | Pending |
| R6.4 | Resolution agent → structured proposal | 2 | `app/agents/resolution.py` | unit tests | Pending |
| R6.5 | Deterministic code for auth, access, amounts, eligibility, approval, execution, transactions, retries, transitions | 1–3 | services | tests | Pending |
| R6.6 | Per-agent tool allowlists; investigation agent has no refund tool | 2 | `app/tools/registry.py` | `tests/unit/test_tool_permissions.py` | Pending |
| R6.7 | Concurrent investigation + policy where independent; dependent facts first | 2 | graph fan-out after `load_order_facts` | graph structure test | Pending |
| R6.8 | Compare specialist vs single baseline (correctness, latency, cost) | 4 | `evals/run_evals.py` | eval report | Pending |

## 7. Database design

| ID | Requirement | Phase | Implementation | Verification | Status |
|---|---|---|---|---|---|
| R7.1 | All 12 entities with specified fields, relationships, constraints, indexes, timestamps | 1 | `app/db/models.py`, `migrations/` | `tests/integration/test_db_constraints.py` | Pending |
| R7.2 | Supporting tables as needed | 1 | payments, shipments, staff_users, ticket_messages, workflow_jobs | migration test | Pending |
| R7.3 | Money in integer minor units | 1 | BIGINT `*_minor` | constraint tests | Pending |
| R7.4 | Unique refund idempotency key | 1 | unique constraint | constraint test | Pending |
| R7.5 | Track succeeded + reserved amounts; prevent exceeding refundable balance | 1 | order balance columns + CHECK + row lock | constraint + concurrency tests | Pending |
| R7.6 | Approval timestamps + exact proposal approved | 3 | `approvals.proposal_payload_hash`, `decided_at` | approval tests | Pending |
| R7.7 | Alembic migrations | 1 | `migrations/versions/` | `alembic upgrade head` in CI | Pending |
| R7.8 | Deterministic seed data | 1 | `scripts/seed_data.py` | seed idempotency test | Pending |
| R7.9 | Explicit transactions for financial changes | 3 | `app/services/refunds.py` | code review + tests | Pending |
| R7.10 | Checkpoints not treated as ledger | 3 | execution reads ledger + provider | crash tests | Pending |
| R7.11 | Constraints tested automatically | 1 | `test_db_constraints.py` | pytest | Pending |

## 8. Tools and API integrations

| ID | Requirement | Phase | Implementation | Verification | Status |
|---|---|---|---|---|---|
| R8.1 | Read tools: get_order, get_return_status, get_payment_status, get_refund_status, search_policy, get_ticket | 2 | `app/tools/read_tools.py` | tool tests | Pending |
| R8.2 | Write tools: create_refund_proposal, execute_approved_refund, update_ticket | 2–3 | `app/tools/write_tools.py` | tool tests | Pending |
| R8.3 | Identity from authenticated request; model never chooses customer | 2 | `ToolContext` | adversarial tests | Pending |
| R8.4 | Ownership checked in service layer; supplied order ID ≠ access | 1 | `records.py` | cross-customer tests | Pending |
| R8.5 | Reviewer identity, approval status, credentials server-controlled | 3 | approvals API | API tests | Pending |
| R8.6 | Structured failures (code, message, retryable) | 1 | `app/schemas/errors.py` | tool tests | Pending |
| R8.7 | Proposal separate from execution; proposal never issues refund | 3 | separate services/nodes | test asserts no provider call | Pending |
| R8.8 | Authorization on every relevant endpoint | 1–3 | dependencies | `test_api_authz.py` | Pending |

## 9. Policy retrieval and RAG

| ID | Requirement | Phase | Implementation | Verification | Status |
|---|---|---|---|---|---|
| R9.1 | Synthetic policies: refund eligibility, return processing, damaged products, shipping delays, human-review exceptions | 2 | `app/policies/documents/` | ingestion test | Pending |
| R9.2 | IDs, versions, effective dates, source refs | 2 | front-matter metadata | ingestion test | Pending |
| R9.3 | Section chunking, embeddings, storage | 2 | `scripts/ingest_policies.py` | ingestion test | Pending |
| R9.4 | Retrieval using complaint + order facts; citations returned with proposal | 2 | `retrieval.py` | retrieval tests | Pending |
| R9.5 | Filter to applicable version | 2 | effective-date filter | version test | Pending |
| R9.6 | Report missing / conflicting evidence; escalate when no policy | 2 | retrieval + decide | tests | Pending |
| R9.7 | Retrieved text treated as untrusted | 2 | prompt wrapping; no authority | injection test | Pending |
| R9.8 | Similarity never proves eligibility | 2 | eligibility independent of retrieval | test | Pending |

## 10. Structured workflow state

| ID | Requirement | Phase | Implementation | Verification | Status |
|---|---|---|---|---|---|
| R10.1 | State has all 16 listed fields | 2 | `app/workflows/state.py` | unit test | Pending |
| R10.2 | Pydantic schemas for agent outputs | 2 | `app/schemas/agents.py` | unit tests | Pending |
| R10.3 | Proposal fields: action, order ID, amount, currency, reason, evidence refs, policy refs, missing info | 2 | `ResolutionProposal` | unit test | Pending |
| R10.4 | Values independently validated against records | 2 | `decide` node | tests | Pending |
| R10.5 | Explicit states + validated transitions (clarification, answer-only, approval pending, rejection, expiration, execution, verification, escalation, recoverable failure) | 2 | `transitions.py` | unit test | Pending |
| R10.6 | Bounded retries; step/cost limits | 2 | settings + guards | loop-limit test | Pending |

## 11. Human approval

| ID | Requirement | Phase | Implementation | Verification | Status |
|---|---|---|---|---|---|
| R11.1 | Reviewer screen: customer, order, summary, action/amount, evidence, citations, expiry, approve/reject | 4 | `frontend/src/pages/Reviewer*` | manual + build | Pending |
| R11.2 | Lifecycle steps 1–9 (immutable proposal → … → execute exact approved action) | 3 | services + graph | approval tests | Pending |
| R11.3 | Material change invalidates approval, new approval requested | 3 | revalidation | test | Pending |
| R11.4 | interrupt() + checkpointer + Command(resume) on same thread ID; node restart accounted for | 3 | `await_approval` node | resume test | Pending |
| R11.5 | Non-idempotent side effects in separate step | 3 | `execute_refund` node | test | Pending |
| R11.6 | Execution verifies stored approval and reviewer decision; chat message insufficient | 3 | `refunds.py` | adversarial test | Pending |

## 12. Safe refund execution

| ID | Requirement | Phase | Implementation | Verification | Status |
|---|---|---|---|---|---|
| R12.1 | Stable idempotency key per operation | 3 | `refund-<proposal_id>` | test | Pending |
| R12.2 | Validate approval + reserve in short TX; commit before provider call | 3 | `refunds.py` | test | Pending |
| R12.3 | Persist provider response; unknown → pending reconciliation | 3 | `refunds.py` | timeout test | Pending |
| R12.4 | Query provider by key before next action; retry same key only per contract | 3 | `reconcile()` | test | Pending |
| R12.5 | Release reservation on definitive failure | 3 | `refunds.py` | test | Pending |
| R12.6 | Mock returns same op for same key+payload; rejects changed payload | 1 | `mock_services/payments` | mock tests | Pending |
| R12.7 | Locking for concurrency; no TX across network call | 3 | `FOR UPDATE` | concurrency test | Pending |

## 13. FastAPI endpoints

| ID | Requirement | Phase | Verification | Status |
|---|---|---|---|---|
| R13.1 | `POST /tickets` | 1 | API tests | Pending |
| R13.2 | `POST /tickets/{ticket_id}/runs` (202 + run ID) | 2 | API tests | Pending |
| R13.3 | `GET /runs/{run_id}` | 2 | API tests | Pending |
| R13.4 | `GET /runs/{run_id}/events` (redacted) | 3 | API tests | Pending |
| R13.5 | `GET /approvals` | 3 | API tests | Pending |
| R13.6 | `POST /approvals/{approval_id}/decision` | 3 | API tests | Pending |
| R13.7 | `POST /runs/{run_id}/messages` | 2 | API tests | Pending |
| R13.8 | `GET /health` | 1 | API tests | Pending |
| R13.9 | Authorization on every endpoint; status codes; structured errors; OpenAPI | 1–3 | `test_api_authz.py`, `/docs` | Pending |
| R13.10 | Persisted jobs + worker + status polling; recovery mechanism documented | 2–3 | worker tests, architecture.md | Pending |

## 14. Failure handling

| ID | Scenario | Phase | Verification | Status |
|---|---|---|---|---|
| R14.1 | Missing order info → targeted clarification | 2 | failure test + eval | Pending |
| R14.2 | Cross-customer access denied without details | 1 | test + eval | Pending |
| R14.3 | Missing applicable policy → escalate | 2 | test + eval | Pending |
| R14.4 | Conflicting policy → flag for review | 2 | test | Pending |
| R14.5 | Temporary read API failure → bounded retry with backoff | 2 | failure test | Pending |
| R14.6 | Invalid model output → validate, limited repair, stop/escalate | 2 | failure test | Pending |
| R14.7 | Rejected approval path | 3 | test | Pending |
| R14.8 | Expired approval requires new approval | 3 | test | Pending |
| R14.9 | Uncertain refund → reconcile before retry/claim | 3 | test | Pending |
| R14.10 | Refund OK + ticket update fails → retry only ticket update | 3 | test | Pending |
| R14.11 | Worker crash → resume + reconcile | 3 | crash test | Pending |
| R14.12 | Excessive loops → step/cost limit | 2 | test | Pending |
| R14.13 | DB/external failures → accurate errors | 1–3 | test | Pending |
| R14.14 | Distinct submitted/processing/succeeded/failed/unknown/escalated states | 3 | enum + tests | Pending |
| R14.15 | Customer messages match verified state; never claim unverified success | 3 | response guard tests | Pending |

## 15. Frontend

| ID | Requirement | Phase | Verification | Status |
|---|---|---|---|---|
| R15.1 | Complaint panel, demo customer selection, ticket list/details, timeline, order/return/payment evidence, policy citations, proposal, approval queue, approve/reject, final outcome, error/escalation states | 4 | build + browser check | Pending |
| R15.2 | Loading, validation, empty, error states; no private reasoning shown | 4 | browser check | Pending |

## 16. Evaluation harness

| ID | Requirement | Phase | Verification | Status |
|---|---|---|---|---|
| R16.1 | ~60 cases in the 6 categories (10 each) | 4 | dataset count test | Pending |
| R16.2 | Held-out subset not used for tuning | 4 | `split` field | Pending |
| R16.3 | Each case: customer, order, policy, allowed/forbidden actions, approval required, expected state, clarification/escalation | 4 | schema validation | Pending |
| R16.4 | Metrics: verified resolution, correctness, unauthorized actions, duplicate refunds, retrieval quality, groundedness, latency, model/tool calls, retries, cost | 4 | report | Pending |
| R16.5 | Active time vs reviewer wait separated | 4 | report | Pending |
| R16.6 | DB + mock-provider assertions decide money movement (no LLM judge for money) | 4 | harness code | Pending |
| R16.7 | Human-assisted vs automated resolution reported separately | 4 | report | Pending |
| R16.8 | Run harness, report actual results only | 4 | `evals/reports/` | Pending |

## 17. Four-week acceptance criteria

| ID | Criterion | Verification | Status |
|---|---|---|---|
| R17.1 | Week 1: manual resolution via APIs without LLM | `test_manual_resolution.py` | Pending |
| R17.2 | Week 2: evidence + validated proposal with citations | `test_workflow_refund.py` | Pending |
| R17.3 | Week 3: restart/retry → no duplicate refunds | `tests/failure_scenarios/` | Pending |
| R17.4 | Week 4: reproducible setup, tests, demos | README walkthrough in clean container | Pending |

## 18–19. Repository structure, Docker, local development

| ID | Requirement | Verification | Status |
|---|---|---|---|
| R18.1 | Baseline structure (deviations documented) | tree review | Pending |
| R18.2 | Lockfiles (`uv.lock`, `package-lock.json`) | `uv lock --check`, `npm ci` | Pending |
| R18.3 | Model/embedding/env/setup docs | README | Pending |
| R19.1 | Backend + frontend Dockerfiles | `docker build` | Pending |
| R19.2 | Compose with app, PostgreSQL, mocks; health checks; startup ordering | `docker compose up` + health | Pending |
| R19.3 | Env validation; migration + seed commands | startup test | Pending |
| R19.4 | Lint, format, test, build commands | Makefile / README | Pending |
| R19.5 | Mock/demo mode without paid services; graceful missing LLM key | test | Pending |
| R19.6 | Verify service health and DB connectivity (not just "started") | `/health/ready` | Pending |

## 20–22. Deployment, CI/CD, runtime failure handling

| ID | Requirement | Verification | Status |
|---|---|---|---|
| R20.1 | Hosting architecture chosen after checking provider limits | deployment.md | Pending |
| R20.2 | Production Docker build, start commands, host/port binding | deploy | Pending |
| R20.3 | Frontend production build with production API URL; explicit CORS | deploy | Pending |
| R20.4 | PostgreSQL config, migrations, env docs, health checks, secrets | deploy | Pending |
| R20.5 | Deployment troubleshooting guide | deployment.md | Pending |
| R20.6 | No secrets committed; `.env.example` placeholders | secret scan | Pending |
| R20.7 | 20-step deployment verification gate with actual results | deployment.md §Verification | Pending |
| R21.1 | GH Actions: deps, lint/format, unit+integration tests, frontend build, import/startup check, Docker builds, migration check | CI run | Pending |
| R21.2 | Safe permissions; pinned actions; no secret leakage | workflow review | Pending |
| R21.3 | Auto-deploy only if supported; else manual docs | deployment.md | Pending |
| R22.1 | Documented handling of dependency, env, DB URL, connectivity, migration, startup delay, host/port, CORS, API URL, LLM auth/quota, Docker build, health, worker crash, uncertain refunds, hosting limits | deployment.md | Pending |
| R22.2 | Bounded retries; clear logs; fail clearly when dependency unavailable | tests | Pending |

## 23. Documentation deliverables

| ID | Deliverable | Status |
|---|---|---|
| R23.1 | README with all listed sections | Pending |
| R23.2 | docs/architecture.md | Pending |
| R23.3 | docs/implementation-plan.md | Pending |
| R23.4 | docs/requirements-checklist.md | Pending |
| R23.5 | docs/deployment.md | Pending |
| R23.6 | Architecture diagram | Pending |
| R23.7 | Demo recording checklist (`docs/demo-checklist.md`) | Pending |
| R23.8 | Resume-ready description (`docs/resume.md`) from verified features only | Pending |

## 25. Definition of done

Tracked by the rows above; summarised in the README "Status" section.
