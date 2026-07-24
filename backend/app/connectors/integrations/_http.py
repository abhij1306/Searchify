"""Shared HTTP plumbing for the integration connectors (invariant 2).

Single owner of the machinery the GSC/GA4/Bing data-API clients and the
OAuth transport client previously mirrored per module: the SSRF
approved-host guard, HTTP-status classification, provider error-detail
extraction (length-capped), ``Retry-After`` parsing, and requests/minute
pacing. The allow-list, error tokens, and knobs themselves stay
config-owned (invariant 1) — this module only reads them.

Tokens never pass through this module (invariant 6): it sees URLs, status
codes, and provider error bodies only. Each connector keeps its own
public error class as a thin subclass alias of ``IntegrationApiError`` so
existing imports and ``except`` clauses are untouched; catches stay
per-class (the worker's ``_PROVIDER_API_ERRORS`` tuple deliberately does
NOT catch the shared base, which would also match OAuth errors).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from urllib.parse import urlsplit

import httpx

from app.core.config.integrations import (
    ERROR_GRANT_AUTH_FAILED,
    ERROR_PROVIDER_API,
    ERROR_RATE_LIMITED,
    ERROR_UNAPPROVED_ENDPOINT,
    INTEGRATION_APPROVED_ENDPOINT_HOSTS,
)

# Cap on provider-supplied error text surfaced in exceptions (defensive:
# keeps messages short even if the provider returns a huge error body).
ERROR_DETAIL_MAX_LEN = 240


class IntegrationApiError(RuntimeError):
    """An integration HTTP call failed; carries a config-owned error token."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


def assert_approved_url(
    url: str, *, label: str, error_type: type[IntegrationApiError]
) -> None:
    """SSRF guard: integration clients only call allow-listed hosts (config)."""
    host = (urlsplit(url).hostname or "").lower()
    if host not in INTEGRATION_APPROVED_ENDPOINT_HOSTS:
        raise error_type(
            f"{label} endpoint host is not approved: {host or '<none>'}",
            error_code=ERROR_UNAPPROVED_ENDPOINT,
        )


def classify_status(status_code: int) -> tuple[str, bool]:
    """Map an HTTP status to a config-owned (error_code, retryable) pair."""
    if status_code == 429:
        return ERROR_RATE_LIMITED, True
    if status_code in (401, 403):
        return ERROR_GRANT_AUTH_FAILED, False
    return ERROR_PROVIDER_API, status_code in (500, 502, 503, 504)


def parse_retry_after(response: httpx.Response) -> float | None:
    """Parse a ``Retry-After`` header; malformed/negative values degrade."""
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def capped_error_text(text: object) -> str:
    """Strip + length-cap provider-supplied error text."""
    return str(text or "").strip()[:ERROR_DETAIL_MAX_LEN]


def nested_error_detail(payload: object) -> str:
    """Extract the length-capped ``error.message`` from a Google error body."""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if not isinstance(error, dict):
        return ""
    return capped_error_text(error.get("message"))


def flat_error_detail(payload: object, keys: Sequence[str]) -> str:
    """Extract the first non-empty length-capped detail among flat ``keys``.

    Only known message fields are read (never the full body); non-dict
    payloads degrade to an empty string.
    """
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        text = capped_error_text(payload.get(key))
        if text:
            return text
    return ""


def oauth_error_detail(payload: object) -> str:
    """Extract ``error`` + ``error_description`` from an OAuth error body.

    Only these two known fields are read (never the full body) and both are
    length-capped; non-dict payloads degrade to an empty string.
    """
    if not isinstance(payload, dict):
        return ""
    parts = []
    error = capped_error_text(payload.get("error"))
    if error:
        parts.append(error)
    description = capped_error_text(payload.get("error_description"))
    if description:
        parts.append(description)
    return ": ".join(parts)


class RequestPacer:
    """Enforce a requests/minute budget between requests (async sleep).

    The budget is passed per ``wait`` call so the config-owned knob is read
    fresh each time (invariant 1) and test monkeypatching takes effect
    immediately. State is per-pacer (one per client instance).
    """

    def __init__(self) -> None:
        self._last_request_at: float | None = None

    async def wait(self, requests_per_minute: int) -> None:
        """Sleep as needed so calls stay within the requests/minute budget."""
        min_interval = 60.0 / requests_per_minute
        now = time.monotonic()
        if self._last_request_at is not None:
            delay = min_interval - (now - self._last_request_at)
            if delay > 0:
                await asyncio.sleep(delay)
        self._last_request_at = time.monotonic()
