"""Direct OpenAI Responses API adapter (transport ``openai``).

Calls the OpenAI Responses API directly using the built-in
``web_search`` tool for grounding. Serves the ``chatgpt`` logical engine — the
only active path to ChatGPT after the v2 direct-provider retirement.

Each call is fresh and stateless:
  * ``store=false`` — no account/response chaining, no server-side memory.
  * The tracked brand/competitor list is NEVER placed in the request; it is
    used only during scoring, after generation (invariant 6). The system
    instruction is fixed and neutral.

The API key is supplied by the caller (resolved from the decrypted
``ProviderConnection`` at execution time) and passed straight through as a
Bearer token in the ``Authorization`` header ONLY — never read from env, never
logged, never echoed into the request body or metadata (invariant 6).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

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
from app.connectors.answer_engines.openai_parser import parse_openai_response
from app.core.config.provider_catalog import (
    ENGINE_CHATGPT,
    ERROR_AUTH,
    ERROR_CONNECTION,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
    TRANSPORT_OPENAI,
    provider_catalog_settings,
)

logger = logging.getLogger(__name__)


def _payload(request: AnswerEngineRequest, *, country_code: str) -> dict[str, Any]:
    """Build a stateless, brand-free Responses API request body.

    Only the user prompt, a neutral instruction, the built-in web-search tool,
    and the global output-token cap are sent. An optional approximate country
    hint is attached to the web-search tool. No brand/competitor/domain list,
    no credentials.
    """
    web_search_tool: dict[str, Any] = {"type": "web_search"}
    if country_code:
        web_search_tool["user_location"] = {
            "type": "approximate",
            "country": country_code,
        }
    payload: dict[str, Any] = {
        "model": request.model,
        "input": request.prompt,
        "tools": [web_search_tool],
        # No response chaining / server-side retention.
        "store": False,
        # Global per-call output cap so one generation cannot run away.
        "max_output_tokens": provider_catalog_settings.max_output_tokens,
    }
    # Responses API takes the system prompt as a top-level ``instructions``
    # field, not a message role.
    if request.system_instruction:
        payload["instructions"] = request.system_instruction
    return payload


class OpenAIAnswerEngineAdapter:
    """Direct OpenAI adapter. Serves the ``chatgpt`` logical engine."""

    logical_engine = ENGINE_CHATGPT
    transport_provider = TRANSPORT_OPENAI

    def __init__(
        self, *, api_key: str, country_code: str = "", base_url: str = ""
    ) -> None:
        if not api_key:
            raise ProviderError(
                "OpenAI API key is not configured",
                error_code=ERROR_AUTH,
                retryable=False,
            )
        self._api_key = api_key
        self._country_code = country_code
        self._url = base_url or provider_catalog_settings.openai_responses_url

    async def execute(self, request: AnswerEngineRequest) -> AnswerEngineResponse:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
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
                f"OpenAI request timed out: {exc}",
                error_code=ERROR_TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"OpenAI connection error: {exc}",
                error_code=ERROR_CONNECTION,
                retryable=True,
            ) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            error_code, retryable = classify_provider_status(response.status_code)
            retry_after = parse_retry_after(response.headers.get("retry-after"))
            # Never log the response body verbatim (could echo the request),
            # only the status and a short reason token.
            logger.warning(
                "openai call failed",
                extra={
                    "status": response.status_code,
                    "error_code": error_code,
                },
            )
            raise_provider_http_error(
                response,
                prefix="OpenAI returned HTTP",
                error_code=error_code,
                retryable=retryable,
                retry_after_seconds=retry_after,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"OpenAI returned non-JSON response: {exc}",
                error_code=ERROR_UNKNOWN,
                retryable=False,
            ) from exc

        return parse_openai_response(
            payload,
            logical_engine=self.logical_engine,
            transport_provider=self.transport_provider,
            requested_model=request.model,
            latency_ms=latency_ms,
        )
