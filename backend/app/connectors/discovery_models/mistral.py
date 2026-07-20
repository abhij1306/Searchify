"""Mistral chat-completions client (the default content provider).

httpx POST to the OpenAI-compatible ``/v1/chat/completions`` endpoint. The API
key comes from the caller (resolved from the ``SecretStr`` setting at call
time), rides ONLY in the ``Authorization`` header, and is never logged or
echoed into any snapshot (invariant 6). Error classification reuses the
neutral ``answer_engines/errors.py`` module + ``provider_catalog`` tokens
(invariant 2) — the only code shared with the measurement adapters.
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
from app.connectors.discovery_models.contracts import (
    DiscoveryRequest,
    DiscoveryResponse,
)
from app.core.config.provider_catalog import (
    ERROR_AUTH,
    ERROR_CONNECTION,
    ERROR_PARSE,
    ERROR_TIMEOUT,
)

logger = logging.getLogger(__name__)

PROVIDER_MISTRAL = "mistral"


class MistralDiscoveryClient:
    """Content-generation client for the Mistral chat-completions API."""

    provider = PROVIDER_MISTRAL

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError(
                "Mistral API key is not configured",
                error_code=ERROR_AUTH,
                retryable=False,
            )
        self._api_key = api_key
        self._endpoint = endpoint
        # Test seam: a mock transport lets component tests run the real client
        # + parsing with no live network.
        self._transport = transport

    async def generate(self, request: DiscoveryRequest) -> DiscoveryResponse:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": list(request.messages),
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, transport=self._transport
            ) as client:
                response = await client.post(
                    self._endpoint, json=payload, headers=headers
                )
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout) as exc:
            raise ProviderError(
                f"Mistral request timed out: {exc}",
                error_code=ERROR_TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Mistral connection error: {exc}",
                error_code=ERROR_CONNECTION,
                retryable=True,
            ) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code >= 400:
            error_code, retryable = classify_provider_status(response.status_code)
            retry_after = parse_retry_after(response.headers.get("retry-after"))
            # Never log the response body (could echo the request); only the
            # status and a short reason token.
            logger.warning(
                "mistral call failed",
                extra={"status": response.status_code, "error_code": error_code},
            )
            raise ProviderError(
                f"Mistral returned HTTP {response.status_code}",
                error_code=error_code,
                retryable=retryable,
                retry_after_seconds=retry_after,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"Mistral returned non-JSON response: {exc}",
                error_code=ERROR_PARSE,
                retryable=False,
            ) from exc

        return _parse_completion(
            body, requested_model=request.model, latency_ms=latency_ms
        )


def _parse_completion(
    body: Any, *, requested_model: str, latency_ms: int
) -> DiscoveryResponse:
    try:
        choice = body["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError(
            "Mistral response missing choices[0].message.content",
            error_code=ERROR_PARSE,
            retryable=False,
        ) from exc
    if not isinstance(content, str):
        raise ProviderError(
            "Mistral message content is not a string",
            error_code=ERROR_PARSE,
            retryable=False,
        )
    usage = body.get("usage") if isinstance(body, dict) else None
    return DiscoveryResponse(
        provider=PROVIDER_MISTRAL,
        requested_model=requested_model,
        returned_model=str(body.get("model") or requested_model),
        output_text=content,
        finish_reason=str(choice.get("finish_reason") or ""),
        usage=dict(usage) if isinstance(usage, dict) else {},
        latency_ms=latency_ms,
    )
