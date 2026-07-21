"""Native Anthropic Messages API adapter (transport ``anthropic``).

Calls the Anthropic Messages API directly (no OpenRouter hop) using Claude's
first-party ``web_search`` server tool for grounding. Serves the ``claude``
logical engine. Ported from the reference ``ai_visibility/anthropic.py`` and
adapted to the shared error type + provenance triple.

The API key is supplied by the caller (resolved from the decrypted
``ProviderConnection``) — never read from env, never logged (invariant 6).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.connectors.answer_engines.anthropic_parser import (
    parse_anthropic_message,
)
from app.connectors.answer_engines.contracts import (
    AnswerEngineRequest,
    AnswerEngineResponse,
)
from app.connectors.answer_engines.errors import (
    ProviderError,
    classify_provider_status,
    parse_retry_after,
    raise_provider_http_error,
)
from app.core.config.provider_catalog import (
    ENGINE_CLAUDE,
    ERROR_AUTH,
    ERROR_CONNECTION,
    ERROR_RATE_LIMIT,
    ERROR_SERVER,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
    TRANSPORT_ANTHROPIC,
    provider_catalog_settings,
)

logger = logging.getLogger(__name__)

# Basic web search tool. Sufficient for a single grounded answer turn.
_WEB_SEARCH_TOOL_TYPE = "web_search_20250305"

# In-body web_search_tool_result_error codes worth retrying. The Messages API
# returns HTTP 200 even when a search fails, embedding the error in the content.
_RETRYABLE_SEARCH_ERRORS = {
    "too_many_requests": ERROR_RATE_LIMIT,
    "unavailable": ERROR_SERVER,
}


def _payload(request: AnswerEngineRequest, *, country_code: str) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "type": _WEB_SEARCH_TOOL_TYPE,
        "name": "web_search",
        "max_uses": provider_catalog_settings.anthropic_max_uses,
    }
    if country_code:
        tool["user_location"] = {"type": "approximate", "country": country_code}
    payload: dict[str, Any] = {
        "model": request.model,
        "max_tokens": provider_catalog_settings.max_output_tokens,
        "messages": [{"role": "user", "content": request.prompt}],
        "tools": [tool],
    }
    # Anthropic takes the system prompt as a top-level field, not a message.
    if request.system_instruction:
        payload["system"] = request.system_instruction
    return payload


def _raise_for_search_error(payload: dict[str, Any]) -> None:
    """Surface retryable in-body web_search failures as provider errors.

    Rate-limit / unavailable errors should retry; ``max_uses_exceeded`` and the
    like are non-fatal (the partial answer still parses).
    """
    for block in payload.get("content") or []:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "") != "web_search_tool_result":
            continue
        content = block.get("content")
        if not isinstance(content, dict):
            continue
        if str(content.get("type") or "") != "web_search_tool_result_error":
            continue
        code = str(content.get("error_code") or "")
        mapped = _RETRYABLE_SEARCH_ERRORS.get(code)
        if mapped is not None:
            raise ProviderError(
                f"Anthropic web_search failed: {code}",
                error_code=mapped,
                retryable=True,
            )


class AnthropicAnswerEngineAdapter:
    """Direct Anthropic adapter. Serves the ``claude`` logical engine."""

    logical_engine = ENGINE_CLAUDE
    transport_provider = TRANSPORT_ANTHROPIC

    def __init__(
        self, *, api_key: str, country_code: str = "", base_url: str = ""
    ) -> None:
        if not api_key:
            raise ProviderError(
                "Anthropic API key is not configured",
                error_code=ERROR_AUTH,
                retryable=False,
            )
        self._api_key = api_key
        self._country_code = country_code
        self._url = base_url or provider_catalog_settings.anthropic_messages_url

    async def execute(self, request: AnswerEngineRequest) -> AnswerEngineResponse:
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": provider_catalog_settings.anthropic_version,
            "content-type": "application/json",
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                response = await client.post(
                    self._url,
                    json=_payload(request, country_code=self._country_code),
                    headers=headers,
                )
        except (
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.PoolTimeout,
        ) as exc:
            raise ProviderError(
                f"Anthropic request timed out: {exc}",
                error_code=ERROR_TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Anthropic connection error: {exc}",
                error_code=ERROR_CONNECTION,
                retryable=True,
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            error_code, retryable = classify_provider_status(response.status_code)
            retry_after = parse_retry_after(response.headers.get("retry-after"))
            # Never log the response body verbatim (could echo the request),
            # only the status, a short reason token, and the advised wait.
            logger.warning(
                "anthropic call failed",
                extra={
                    "status": response.status_code,
                    "error_code": error_code,
                    "retry_after": retry_after,
                },
            )
            raise_provider_http_error(
                response,
                prefix="Anthropic returned HTTP",
                error_code=error_code,
                retryable=retryable,
                retry_after_seconds=retry_after,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"Anthropic returned non-JSON response: {exc}",
                error_code=ERROR_UNKNOWN,
                retryable=False,
            ) from exc
        _raise_for_search_error(payload)
        return parse_anthropic_message(
            payload,
            logical_engine=self.logical_engine,
            transport_provider=self.transport_provider,
            requested_model=request.model,
            latency_ms=latency_ms,
        )
