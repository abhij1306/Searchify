"""Default-agent client (OpenAI-compatible ``/chat/completions``).

The app-level general model that powers assisted features (prompt generation
now; content generation later). Configured entirely from env
(``config/agent.py``) — Mistral by default, but any OpenAI-compatible endpoint
works. This is NOT a measurement engine and NOT a BYOK connection:
measurement engines are only ever measured (roadmap non-goal), and BYOK keys
belong to ``ProviderConnection``.

Secret handling mirrors the answer-engine adapters (invariant 6 spirit): the
key is sent only as a Bearer header, never logged, never echoed into any DTO,
snapshot, or error message.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.connectors.answer_engines.errors import (
    ProviderError,
    classify_provider_status,
    parse_retry_after,
)
from app.core.config.agent import DefaultAgentSettings, default_agent_settings
from app.core.config.provider_catalog import (
    ERROR_CONNECTION,
    ERROR_PARSE,
    ERROR_TIMEOUT,
)

logger = logging.getLogger(__name__)


class AgentNotConfiguredError(RuntimeError):
    """Raised when no default-agent API key is configured in the environment."""


class DefaultAgentClient:
    """Thin JSON-mode chat client over an OpenAI-compatible endpoint."""

    def __init__(self, settings: DefaultAgentSettings | None = None) -> None:
        self._settings = settings or default_agent_settings
        if not self._settings.configured:
            raise AgentNotConfiguredError(
                "No default agent API key configured "
                "(set DEFAULT_AGENT_API_KEY or MISTRALAI_API_KEY)"
            )

    @property
    def model(self) -> str:
        return self._settings.model

    @property
    def base_url_host(self) -> str:
        """Credential-free endpoint host, safe for provenance records."""
        return httpx.URL(self._settings.base_url).host or ""

    async def complete_json(self, *, system: str, user: str) -> str:
        """Run one JSON-mode completion and return the raw content string."""
        settings = self._settings
        payload: dict[str, Any] = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": settings.max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
        }
        url = settings.base_url.rstrip("/") + "/chat/completions"
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=headers)
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout) as exc:
            raise ProviderError(
                f"Default agent request timed out: {exc}",
                error_code=ERROR_TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Default agent connection error: {exc}",
                error_code=ERROR_CONNECTION,
                retryable=True,
            ) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            error_code, retryable = classify_provider_status(response.status_code)
            # Status + reason token only — never the body (could echo input).
            logger.warning(
                "default agent call failed",
                extra={"status": response.status_code, "error_code": error_code},
            )
            raise ProviderError(
                f"Default agent returned HTTP {response.status_code}",
                error_code=error_code,
                retryable=retryable,
                retry_after_seconds=parse_retry_after(
                    response.headers.get("retry-after")
                ),
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, LookupError, TypeError) as exc:
            raise ProviderError(
                f"Default agent returned an unparseable response: {type(exc).__name__}",
                error_code=ERROR_PARSE,
                retryable=False,
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(
                "Default agent returned empty content",
                error_code=ERROR_PARSE,
                retryable=False,
            )
        logger.info(
            "default agent call ok",
            extra={"latency_ms": latency_ms, "model": settings.model},
        )
        return content
