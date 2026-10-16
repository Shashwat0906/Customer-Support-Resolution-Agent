"""FastAPI application entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.api import approvals, auth, demo, health, orders, runs, tickets
from app.config import get_settings
from app.schemas.errors import AppError, ErrorBody, ErrorResponse

log = logging.getLogger("app")


def _error(status: int, code: str, message: str, retryable: bool = False, details: dict | None = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, retryable=retryable, details=details or {}))
    return JSONResponse(status_code=status, content=body.model_dump())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if settings.llm_fallback_reason:
        log.warning(settings.llm_fallback_reason)
    log.info(
        "starting (environment=%s, llm=%s, agent_mode=%s)",
        settings.environment,
        settings.llm_provider,
        settings.agent_mode,
    )
    worker = None
    if settings.run_embedded_worker:
        from app.db.session import wait_for_db
        from app.worker import EmbeddedWorker

        wait_for_db()
        worker = EmbeddedWorker()
        worker.start()
    yield
    if worker is not None:
        worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Customer Support Resolution Agent",
        version=health.VERSION,
        description=(
            "Agentic resolution of customer complaints for a fictional electronics retailer. The model proposes; "
            "the application validates eligibility, requires human approval for every refund, executes through "
            "controlled, idempotent APIs and reports only verified outcomes. Authenticate with "
            "`POST /auth/demo-login` and use the token as a Bearer token."
        ),
        lifespan=lifespan,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, exc.retryable, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return _error(422, "REQUEST_VALIDATION_FAILED", "The request is invalid.", details={"errors": errors})

    @app.exception_handler(OperationalError)
    async def db_error(_: Request, exc: OperationalError) -> JSONResponse:
        log.error("database error: %s", exc.orig)
        return _error(503, "DATABASE_UNAVAILABLE", "The database is temporarily unavailable.", retryable=True)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _error(500, "INTERNAL_ERROR", "An unexpected error occurred.")

    for module in (health, auth, orders, tickets, runs, approvals, demo):
        app.include_router(module.router)
    return app


app = create_app()
