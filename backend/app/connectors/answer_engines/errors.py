"""Shared error type + HTTP status classification for answer-engine adapters.

The reference implementation hangs ``AiVisibilityProviderError`` and
``classify_provider_status`` off the Gemini module and imports them elsewhere;
here they live in a neutral module so every adapter (Gemini/Anthropic/
provider adapters) depends on the shared type rather than on a sibling adapter.
"""

from __future__ import annotations

from typing import Any, NoReturn

import httpx

from app.core.config.provider_catalog import (
    ERROR_AUTH,
    ERROR_CLIENT,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
)

# Cap on the provider-supplied error message we surface (defensive: keeps the
# ProviderError message short even if the provider returns a huge body).
_ERROR_DETAIL_MAX_LEN = 240


def safe_error_detail(payload: Any) -> str:
    """Extract the error type + message from a provider error body.

    Anthropic and OpenAI error bodies share the shape ``{"error": {"type":
    "...", "message": "..."}}``. Only these two known fields are read (never
    the full body), and both are length-capped. The message names the
    actual failure (e.g. a credit-balance problem behind a generic HTTP 400),
    which is essential for the connection-test UI. Non-dict payloads (lists,
    strings, numbers, ``None``) degrade to an empty string, never raise.
    """
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if not isinstance(error, dict):
        return ""
    parts = []
    error_type = str(error.get("type") or "").strip()
    if error_type:
        parts.append(error_type[:_ERROR_DETAIL_MAX_LEN])
    message = str(error.get("message") or "").strip()
    if message:
        parts.append(message[:_ERROR_DETAIL_MAX_LEN])
    return ": ".join(parts)


class ProviderError(RuntimeError):
    """Raised when a provider call fails. Carries a retry classification."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        # Provider-advised wait (from a Retry-After header), when present.
        self.retry_after_seconds = retry_after_seconds


def classify_provider_status(status_code: int) -> tuple[str, bool]:
    """Map an HTTP status to an (error_code, retryable) pair."""
    if status_code == 429:
        return ERROR_RATE_LIMIT, True
    if status_code in (500, 502, 503, 504):
        return ERROR_SERVER, True
    if status_code in (401, 403):
        return ERROR_AUTH, False
    return ERROR_CLIENT, False


def parse_retry_after(value: str | None) -> float | None:
    """Parse a numeric (delta-seconds) ``Retry-After`` header into seconds.

    The HTTP-date form is uncommon here and intentionally ignored (returns
    ``None``) so the caller falls back to exponential backoff.
    """
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def raise_provider_http_error(
    response: httpx.Response,
    *,
    prefix: str,
    error_code: str,
    retryable: bool,
    retry_after_seconds: float | None = None,
) -> NoReturn:
    """Raise a ``ProviderError`` for an HTTP error response.

    Shared by the Anthropic/OpenAI adapters: appends the safe (type + capped
    message) error detail when the body is JSON, degrading to the bare status
    line on non-JSON bodies. ``prefix`` names the provider, e.g. ``"Anthropic
    returned HTTP"``.
    """
    try:
        safe_detail = safe_error_detail(response.json())
    except ValueError:
        safe_detail = ""
    suffix = f" ({safe_detail})" if safe_detail else ""
    raise ProviderError(
        f"{prefix} {response.status_code}{suffix}",
        error_code=error_code,
        retryable=retryable,
        retry_after_seconds=retry_after_seconds,
    )
