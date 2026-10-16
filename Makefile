# Developer commands. Requires uv, Node 22, and PostgreSQL (or `make up` for Docker).
.PHONY: install lint format typecheck test evals migrate seed bootstrap mocks api worker frontend up down build

install:        ## install backend + frontend dependencies from lockfiles
	uv sync --frozen --all-extras
	cd frontend && npm ci
lint:           ## ruff lint + format check + frontend typecheck
	uv run ruff check . && uv run ruff format --check . && cd frontend && npm run typecheck
format:
	uv run ruff format . && uv run ruff check . --fix
typecheck:
	uv run mypy app mock_services scripts
test:           ## unit + integration + failure-scenario tests (needs PostgreSQL; see TEST_DATABASE_URL)
	uv run pytest -q
evals:          ## 60-case evaluation, both agent designs -> evals/reports/latest.md
	uv run python -m evals.run_evals
migrate:
	uv run alembic upgrade head
seed:
	uv run python -m scripts.seed_data --reset
bootstrap:      ## migrate + checkpoints + seed + ingest + reset mocks
	uv run python -m scripts.bootstrap --reset-demo
mocks:
	uv run uvicorn mock_services.main:app --port 8001
api:
	RUN_EMBEDDED_WORKER=true uv run uvicorn app.main:app --reload --port 8000
worker:
	uv run python -m app.worker
frontend:
	cd frontend && npm run dev
build:          ## build Docker images
	docker compose build
up:             ## full stack in Docker: http://localhost:8080 (UI), http://localhost:8000/docs (API)
	docker compose up --build -d && docker compose ps
down:
	docker compose down
