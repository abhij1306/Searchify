"""Shared error type + HTTP status classification for answer-engine adapters.

The reference implementation hangs ``AiVisibilityProviderError`` and
``classify_provider_status`` off the Gemini module and imports them elsewhere;
here they live in a neutral module so every adapter (Gemini/Anthropic/
OpenRouter) depends on the shared type rather than on a sibling adapter.
"""

from __future__ import annotations

from app.core.config.provider_catalog import (
    ERROR_AUTH,
    ERROR_CLIENT,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
)


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
