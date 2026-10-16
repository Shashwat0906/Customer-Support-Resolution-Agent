# Deployment

## 1. Hosting architecture (chosen after checking provider limits)

| Piece | Render resource | Plan | Notes |
|---|---|---|---|
| API + embedded worker + mock services | Web service (Docker, `ROLE=all`) | Free (512 MB RAM) | Measured ~135–150 MiB RSS under the smoke test with a 512 MiB cap |
| Dashboard | Static site (`frontend/dist`) | Free | `VITE_API_BASE_URL` baked in at build time; hash routing (no rewrite rules needed) |
| PostgreSQL 16 | **External** (`DATABASE_URL`) | Owner's choice | Render allows one free Postgres per workspace and this workspace's slot is used by another project (`create_postgres` returned *"cannot have more than one active free tier database"*). The owner chose to supply a database URL (e.g. Neon/Supabase free tier, or a paid Render Postgres). |

Why one container for API, worker and mocks: free hosting gives one process container per service, and the mock
services are part of the demo (they stand in for the payment, shipping and ticketing providers). `scripts/start.sh`
(`ROLE=all`) starts the mocks on `127.0.0.1:8001`, waits for them, runs `scripts.bootstrap` (migrations, checkpoint
schema, seed if empty, policy ingestion), then runs uvicorn with the embedded worker. `/health/ready` reports the
mocks' status, so a dead mock process is visible.

**Free-tier realities (do not assume more):** the web service sleeps after ~15 minutes idle (first request takes about a minute);
the embedded worker is paused while asleep, but jobs are durable and resume on wake; the container filesystem is
ephemeral, so the **mock provider's SQLite records reset on every restart** while the application ledger lives in
PostgreSQL. After a restart, use `POST /demo/reset` (or the Demo ops page) before demos so provider and ledger agree;
otherwise the system correctly *escalates* any ledger/provider disagreement rather than refunding.

## 2. Configuration

Backend web service environment (set in the Render dashboard; never committed):

| Key | Value |
|---|---|
| `ROLE` | `all` |
| `ENVIRONMENT` | `production` (enables the production checks below) |
| `DATABASE_URL` | the PostgreSQL URL (`postgres://…` accepted; add `?sslmode=require` if the provider needs TLS) |
| `JWT_SECRET` | random, ≥ 32 chars |
| `MOCK_ADMIN_TOKEN`, `PAYMENTS_API_KEY` | random |
| `CORS_ORIGINS` | the static site origin, e.g. `https://support-agent-dashboard.onrender.com` |
| `PAYMENTS_BASE_URL`, `SHIPPING_BASE_URL`, `TICKETING_BASE_URL` | `http://127.0.0.1:8001/{payments,shipping,ticketing}` |
| `DEMO_AUTH_ENABLED` | `true` (synthetic personas) |
| `LLM_PROVIDER` / `LLM_API_KEY` | optional; default `mock` |

Production refuses to start (clear error in the logs) if `DATABASE_URL` is unset/local, `JWT_SECRET` is the dev value
or short, `MOCK_ADMIN_TOKEN` is the default, or `CORS_ORIGINS` contains `*`. `PORT` is provided by Render and used by
`start.sh`.

Static site: build command `cd frontend && npm ci && npm run build`, publish directory `frontend/dist`, environment
`VITE_API_BASE_URL=https://<api-service>.onrender.com`.

## 3. Steps

1. Create the PostgreSQL database (external provider or paid Render plan) and copy its connection URL.
2. Web service: *New → Web Service → this repository*, runtime **Docker**, Dockerfile `./Dockerfile`, plan Free,
   health check path **`/health`**, environment from §2.
3. Static site: *New → Static Site → this repository*, build/publish from §2.
4. Set `CORS_ORIGINS` on the web service to the static site URL (redeploys automatically).
5. Verify (§5): `python -m scripts.smoke_api --base-url https://<api> --origin https://<site> --reset` and
   `UI_URL=https://<site> node tests/e2e/ui_smoke.cjs`.

Migrations run on every start (`scripts.bootstrap`, idempotent). To run them manually:
`docker run --rm -e DATABASE_URL=… <image> python -m scripts.bootstrap`.

Automated deployment: Render auto-deploys the configured branch on push. CI does not deploy (no deploy credentials
are stored in GitHub); a green CI run does not prove the live service works — run §5 after each deploy.

## 4. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Deploy fails, log says `DATABASE_URL must point to the production database` | Set `DATABASE_URL` on the web service |
| `Database unavailable after N attempts` in logs | Wrong host/credentials, provider asleep, or TLS required (`?sslmode=require`); startup waits with bounded retries, then exits |
| `alembic` errors on start | Database user lacks DDL rights, or the schema was changed by hand; run `python -m scripts.bootstrap` locally against the URL to see the full error |
| `JWT_SECRET must be set…` / `MOCK_ADMIN_TOKEN must be changed…` / CORS error at start | Production config checks; set the variables |
| Browser shows "Cannot reach the API" | `VITE_API_BASE_URL` missing/wrong at build time (rebuild the static site), or the API is asleep (first request ~1 min) |
| CORS error in the browser console | `CORS_ORIGINS` must exactly match the site origin (scheme + host, no trailing slash) |
| Service healthy but `/health/ready` is 503 | Mock services not running, data not seeded, or policies not ingested — check start logs |
| Port/binding issues | The app binds `0.0.0.0:$PORT`; do not hard-code a port in Render settings |
| LLM errors (`LLM_AUTH_FAILED`, `LLM_RATE_LIMITED`, `LLM_REFUSAL`) | Runs escalate with the code in `escalation_reason`; check the key/quota; unset `LLM_PROVIDER` to return to the mock |
| Docker build fails with TLS/certificate errors behind a proxy | Pass the proxy CA as a build secret: `--secret id=extra_ca,src=ca.pem` |
| Docker Hub `429 Too Many Requests` | Authenticate or override base images: `--build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.12-slim` (also `NODE_IMAGE`, `NGINX_IMAGE`) |
| Worker crashed mid-run | Jobs have leases; the sweeper re-queues them and the graph resumes from its checkpoint; uncertain refunds are reconciled by idempotency key |
| A refund shows `unknown` / run escalated `refund_outcome_unknown` | Provider outcome could not be confirmed; the reserved amount stays held; the reconciliation sweeper settles it once the provider answers |
| Free instance asleep / slow first request | Expected on the free plan; jobs are durable and resume on wake |

## 5. Verification gate

Results of the 20-step gate. **Local** = Docker Compose / single-container image on the development machine.
**Live** = the hosted deployment. Items not yet performed are marked *Unverified*.

| # | Check | Result |
|---|---|---|
| 1 | Build Docker images | ✅ Local: backend + frontend images built; CI builds both on every push |
| 2 | Dependencies & lockfile consistency | ✅ `uv lock --check`, `uv sync --frozen`, `npm ci` (local + CI) |
| 3 | Linting & static checks | ✅ ruff, ruff format, mypy, tsc (local + CI) |
| 4 | Automated test suite | ✅ 153 tests pass locally and in CI (run #2) |
| 5 | Services start, health checks | ✅ Local Compose: db, mocks, api, frontend healthy; bootstrap exited 0 |
| 6 | Database migrations | ✅ Local + CI (upgrade, `alembic check`, downgrade, upgrade) |
| 7 | Seed synthetic data | ✅ Local (bootstrap: 12 customers, 26 policy chunks) |
| 8 | DB connectivity & authentication | ✅ Local `/health/ready`; authz tests; smoke checks 401/403 |
| 9 | API endpoints & frontend production build | ✅ Local smoke (22 checks) + browser smoke against Compose; CI frontend build |
| 10 | Environment configuration & secret handling | ✅ Production config validation tests; no secrets in repo; CA build secret not in image layers |
| 11 | Production API URL & CORS | ✅ Local single-container smoke: allowed origin accepted, other origin rejected — Live: *Unverified* |
| 12 | Order-status workflow | ✅ Local — Live: *Unverified* |
| 13 | Approved refund through mock provider | ✅ Local — Live: *Unverified* |
| 14 | Ineligible refund, no refund occurs | ✅ Local — Live: *Unverified* |
| 15 | Timeout after acceptance, no duplicate | ✅ Local — Live: *Unverified* |
| 16 | Ticket-update retries | ✅ Local — Live: *Unverified* |
| 17 | Inspect deployment logs | ✅ Partial: Render built the image (~40 s) and the container failed fast with `DATABASE_URL must point to the production database (it is unset or local)` — the intended clear failure. Logs of a healthy start: *Unverified* |
| 18 | Deployed health endpoint | *Unverified* (pending live deploy) |
| 19 | End-to-end tests against deployment | *Unverified* (pending live deploy) |
| 20 | Document exact tests and results | This table; updated after the live run |

**Live status (2026-10-16):** services created on Render (Singapore, free):
`https://support-resolution-agent-api.onrender.com` (fails at start until `DATABASE_URL` is set) and
`https://support-resolution-agent-dashboard.onrender.com` (deploy `live`). The static site's first deploy succeeded.

**Blockers for 11–19 (live):**
1. The production `DATABASE_URL` (owner-provided; see §1).
2. The development session that built this project cannot reach `*.onrender.com` (its egress proxy returns 403), so
   the live smoke tests must be run from a machine with access, or after the environment's network policy allows
   `onrender.com`: `python -m scripts.smoke_api --base-url https://support-resolution-agent-api.onrender.com
   --origin https://support-resolution-agent-dashboard.onrender.com --reset` and
   `UI_URL=https://support-resolution-agent-dashboard.onrender.com node tests/e2e/ui_smoke.cjs`.
