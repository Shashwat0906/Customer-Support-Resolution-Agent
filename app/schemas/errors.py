"""Structured errors shared by the API, services and tools."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class AppError(Exception):
    """An expected failure with a stable code, safe message and retryability."""

    status_code = 400

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code

    def body(self) -> ErrorBody:
        return ErrorBody(code=self.code, message=self.message, retryable=self.retryable, details=self.details)


class NotFound(AppError):
    status_code = 404


class Forbidden(AppError):
    status_code = 403


class Unauthorized(AppError):
    status_code = 401


class Conflict(AppError):
    status_code = 409


class ValidationFailed(AppError):
    status_code = 422


class ExternalServiceError(AppError):
    """A failure talking to an external service.

    ``kind`` is one of: timeout, unavailable, client_error, not_found, conflict.
    ``timeout``/``unavailable`` are uncertain for writes: the provider may have acted.
    """

    status_code = 502

    def __init__(
        self, service: str, kind: str, message: str, *, http_status: int | None = None, body: Any = None
    ) -> None:
        retryable = kind in ("timeout", "unavailable")
        super().__init__(
            f"{service.upper()}_{kind.upper()}",
            message,
            retryable=retryable,
            details={"service": service, "kind": kind, "http_status": http_status},
        )
        self.service = service
        self.kind = kind
        self.http_status = http_status
        self.body_json = body
