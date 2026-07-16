"""Adapter resolution for a (logical_engine, transport_provider) route.

Given a decrypted BYOK key and an approved route, build the concrete adapter.
Key resolution reads the decrypted ``ProviderConnection`` at execution time and
the key is passed straight into the adapter — never read from env, never
persisted into snapshots/logs (invariant 6). B5's worker reuses this resolver.
"""

from __future__ import annotations

from app.connectors.answer_engines.anthropic import AnthropicAnswerEngineAdapter
from app.connectors.answer_engines.errors import ProviderError
from app.connectors.answer_engines.gemini import GeminiAnswerEngineAdapter
from app.connectors.answer_engines.openrouter import (
    OpenRouterAnswerEngineAdapter,
)
from app.core.config.provider_catalog import (
    ERROR_INVALID_SURFACE,
    TRANSPORT_ANTHROPIC,
    TRANSPORT_GOOGLE,
    TRANSPORT_OPENROUTER,
    is_route_approved,
)


def build_adapter(
    *,
    logical_engine: str,
    transport_provider: str,
    api_key: str,
    country_code: str = "",
    base_url: str = "",
):
    """Construct the adapter for an approved (engine, transport) route.

    Raises ``ProviderError`` if the route is not approved at MVP (e.g. direct
    OpenAI, or chatgpt over anything other than OpenRouter).
    """
    if not is_route_approved(logical_engine, transport_provider):
        raise ProviderError(
            f"Route not approved at MVP: {logical_engine} via "
            f"{transport_provider}",
            error_code=ERROR_INVALID_SURFACE,
            retryable=False,
        )
    if transport_provider == TRANSPORT_GOOGLE:
        return GeminiAnswerEngineAdapter(api_key=api_key, base_url=base_url)
    if transport_provider == TRANSPORT_ANTHROPIC:
        return AnthropicAnswerEngineAdapter(
            api_key=api_key, country_code=country_code, base_url=base_url
        )
    if transport_provider == TRANSPORT_OPENROUTER:
        return OpenRouterAnswerEngineAdapter(
            api_key=api_key,
            logical_engine=logical_engine,
            country_code=country_code,
            base_url=base_url,
        )
    raise ProviderError(
        f"Unsupported transport provider: {transport_provider}",
        error_code=ERROR_INVALID_SURFACE,
        retryable=False,
    )
