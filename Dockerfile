# Backend image: FastAPI API, worker, mock services, migrations (role chosen at runtime via ROLE).
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Optional build secret "extra_ca": a CA bundle for TLS-intercepting proxies. Used only during
# dependency installation; it is never written into an image layer. Not needed on normal networks.
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install --no-cache-dir uv==0.11.32

WORKDIR /srv
COPY pyproject.toml uv.lock .python-version ./
# Reproducible install from the lockfile; fails if the lockfile is out of date.
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    uv sync --frozen --no-dev --extra anthropic --no-install-project

COPY alembic.ini ./
COPY app ./app
COPY mock_services ./mock_services
COPY migrations ./migrations
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 appuser && mkdir -p /srv/data && chown -R appuser /srv/data
USER appuser

ENV PORT=8000 ROLE=api MOCK_DB_PATH=/srv/data/mock_services.sqlite3
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)" || exit 1

CMD ["sh", "scripts/start.sh"]
