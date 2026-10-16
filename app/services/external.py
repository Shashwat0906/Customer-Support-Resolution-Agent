"""HTTP clients for the payment, shipping and ticketing services.

* Every call has a timeout. Failures become ``ExternalServiceError`` with a
  ``kind`` (timeout / unavailable / client_error / not_found / conflict).
* Reads may be retried with bounded exponential backoff (``with_read_retry``).
* Writes are never retried here: the refund service decides, after
  reconciling, whether a retry with the same idempotency key is safe.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.config import get_settings
from app.schemas.errors import ExternalServiceError

log = logging.getLogger(__name__)


def _request(service: str, method: str, url: str, **kwargs: Any) -> httpx.Response:
    timeout = kwargs.pop("timeout", get_settings().external_timeout_seconds)
    try:
        resp = httpx.request(method, url, timeout=timeout, **kwargs)
    except httpx.TimeoutException as exc:
        raise ExternalServiceError(service, "timeout", f"{service} did not respond within {timeout}s") from exc
    except httpx.TransportError as exc:
        raise ExternalServiceError(service, "unavailable", f"{service} is unreachable: {type(exc).__name__}") from exc
    if resp.status_code < 400:
        return resp
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:200]}
    status = resp.status_code
    if status in (502, 503, 504) or status >= 500:
        kind = "timeout" if status == 504 else "unavailable"
    elif status == 404:
        kind = "not_found"
    elif status == 409:
        kind = "conflict"
    else:
        kind = "client_error"
    raise ExternalServiceError(service, kind, f"{service} returned HTTP {status}", http_status=status, body=body)


def with_read_retry[T](
    fn: Callable[[], T],
    *,
    on_retry: Callable[[int, ExternalServiceError], None] | None = None,
    max_retries: int | None = None,
) -> T:
    """Retry a *read* on transient failures with bounded exponential backoff."""
    settings = get_settings()
    retries = settings.max_read_retries if max_retries is None else max_retries
    attempt = 0
    while True:
        try:
            return fn()
        except ExternalServiceError as exc:
            if not exc.retryable or attempt >= retries:
                raise
            attempt += 1
            if on_retry:
                on_retry(attempt, exc)
            delay = settings.retry_backoff_seconds * (2 ** (attempt - 1))
            log.info("transient %s failure (%s); retry %s/%s in %.2fs", exc.service, exc.kind, attempt, retries, delay)
            time.sleep(delay)


class PaymentsClient:
    service = "payments"

    def __init__(self, base_url: str | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.payments_base_url).rstrip("/")
        self.headers = {"Authorization": f"Bearer {settings.payments_api_key}"}

    def get_payment(self, payment_reference: str) -> dict[str, Any]:
        return _request(
            self.service, "GET", f"{self.base_url}/charges/{payment_reference}", headers=self.headers
        ).json()

    def create_refund(
        self, *, idempotency_key: str, payment_reference: str, amount_minor: int, reason: str, metadata: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        resp = _request(
            self.service,
            "POST",
            f"{self.base_url}/refunds",
            headers={**self.headers, "Idempotency-Key": idempotency_key},
            json={
                "payment_reference": payment_reference,
                "amount_minor": amount_minor,
                "currency": "INR",
                "reason": reason[:500],
                "metadata": metadata,
            },
        )
        return resp.status_code, resp.json()

    def lookup_refund(self, idempotency_key: str) -> dict[str, Any] | None:
        try:
            return _request(
                self.service,
                "GET",
                f"{self.base_url}/refunds",
                headers=self.headers,
                params={"idempotency_key": idempotency_key},
            ).json()
        except ExternalServiceError as exc:
            if exc.kind == "not_found":
                return None
            raise

    def get_refund(self, refund_id: str) -> dict[str, Any]:
        return _request(self.service, "GET", f"{self.base_url}/refunds/{refund_id}", headers=self.headers).json()


class ShippingClient:
    service = "shipping"

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or get_settings().shipping_base_url).rstrip("/")

    def get_shipment(self, tracking_number: str) -> dict[str, Any]:
        return _request(self.service, "GET", f"{self.base_url}/shipments/{tracking_number}").json()


class TicketingClient:
    service = "ticketing"

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or get_settings().ticketing_base_url).rstrip("/")

    def upsert_ticket(
        self, ticket_id: str, *, customer_ref: str, status: str, category: str, summary: str
    ) -> dict[str, Any]:
        return _request(
            self.service,
            "PUT",
            f"{self.base_url}/tickets/{ticket_id}",
            json={"customer_ref": customer_ref, "status": status, "category": category, "summary": summary[:4000]},
        ).json()

    def add_note(self, ticket_id: str, *, idempotency_key: str, body: str, status: str | None) -> dict[str, Any]:
        return _request(
            self.service,
            "POST",
            f"{self.base_url}/tickets/{ticket_id}/notes",
            headers={"Idempotency-Key": idempotency_key},
            json={"body": body[:8000], "status": status},
        ).json()


def check_mock_services() -> dict[str, str]:
    """Readiness probe for external dependencies."""
    results: dict[str, str] = {}
    settings = get_settings()
    for name, url in (
        ("payments", settings.payments_base_url),
        ("shipping", settings.shipping_base_url),
        ("ticketing", settings.ticketing_base_url),
    ):
        root = url.rstrip("/").rsplit("/", 1)[0]
        try:
            r = httpx.get(f"{root}/health", timeout=2.0)
            results[name] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
        except httpx.HTTPError as exc:
            results[name] = f"unreachable ({type(exc).__name__})"
    return results
