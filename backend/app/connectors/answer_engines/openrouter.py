"""OpenRouter adapter for native provider web search (transport ``openrouter``).

OpenRouter is the multiplexing transport: it serves every MVP logical engine —
``chatgpt`` (the ONLY MVP path to ChatGPT, per decision B-3), ``claude``, and
``gemini`` — by routing to the matching upstream model. The requested model must
match its logical engine's approved surface (``OPENROUTER_MODEL_PREFIXES``).

Ported from the reference ``ai_visibility/openrouter.py`` and adapted to the
shared error type, the provenance triple, and per-engine surface validation.
The API key is supplied by the caller (decrypted ``ProviderConnection``) —
never read from env, never logged (invariant 6).
"""

from __future__ import annotations

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
)
from app.connectors.answer_engines.openrouter_parser import (
    parse_openrouter_completion,
)
from app.core.config.provider_catalog import (
    ERROR_AUTH,
    ERROR_CONNECTION,
    ERROR_INVALID_SURFACE,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
    OPENROUTER_MODEL_PREFIXES,
    TRANSPORT_OPENROUTER,
    provider_catalog_settings,
)


def _payload(
    request: AnswerEngineRequest, *, country_code: str
) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    if request.system_instruction:
        messages.append(
            {"role": "system", "content": request.system_instruction}
        )
    messages.append({"role": "user", "content": request.prompt})
    location: dict[str, str] = {"type": "approximate"}
    if country_code:
        location["country"] = country_code
    return {
        "model": request.model,
        "messages": messages,
        "tools": [
            {
                "type": "openrouter:web_search",
                "parameters": {
                    "engine": "native",
                    "user_location": location,
                },
            }
        ],
        "stream": False,
        # Global per-call output cap so one generation cannot run away.
        "max_tokens": provider_catalog_settings.max_output_tokens,
    }


def _model_matches_surface(logical_engine: str, model: str) -> bool:
    prefixes = OPENROUTER_MODEL_PREFIXES.get(logical_engine, ())
    normalized = model.lower()
    return bool(prefixes) and normalized.startswith(prefixes)


class OpenRouterAnswerEngineAdapter:
    """OpenRouter adapter. Serves chatgpt / claude / gemini via ``openrouter``."""

    transport_provider = TRANSPORT_OPENROUTER

    def __init__(
        self,
        *,
        api_key: str,
        logical_engine: str,
        country_code: str = "",
        base_url: str = "",
    ) -> None:
        if logical_engine not in OPENROUTER_MODEL_PREFIXES:
            raise ValueError(
                f"Unsupported OpenRouter logical engine: {logical_engine}"
            )
        if not api_key:
            raise ProviderError(
                "OpenRouter API key is not configured",
                error_code=ERROR_AUTH,
                retryable=False,
            )
        self.logical_engine = logical_engine
        self._api_key = api_key
        self._country_code = country_code
        self._url = (
            base_url
            or provider_catalog_settings.openrouter_chat_completions_url
        )

    async def execute(
        self, request: AnswerEngineRequest
    ) -> AnswerEngineResponse:
        if not _model_matches_surface(self.logical_engine, request.model):
            raise ProviderError(
                f"Model is not approved for native search: {request.model}",
                error_code=ERROR_INVALID_SURFACE,
                retryable=False,
            )
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": provider_catalog_settings.openrouter_app_title,
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds
            ) as client:
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
                f"OpenRouter request timed out: {exc}",
                error_code=ERROR_TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"OpenRouter connection error: {exc}",
                error_code=ERROR_CONNECTION,
                retryable=True,
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            error_code, retryable = classify_provider_status(
                response.status_code
            )
            raise ProviderError(
                f"OpenRouter returned HTTP {response.status_code}",
                error_code=error_code,
                retryable=retryable,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"OpenRouter returned non-JSON response: {exc}",
                error_code=ERROR_UNKNOWN,
                retryable=False,
            ) from exc
        return parse_openrouter_completion(
            payload,
            logical_engine=self.logical_engine,
            transport_provider=self.transport_provider,
            requested_model=request.model,
            latency_ms=latency_ms,
        )
