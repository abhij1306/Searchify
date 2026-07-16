# BYOK provider catalog + answer-engine guardrails (invariant 1: config lives
# in core/config, never inline in service/adapter code).
#
# Owns the approved logical-engine -> transport -> model catalog for the MVP,
# the transport/engine enumerations, and the provider-agnostic guardrail knobs
# (token caps, timeouts, endpoint URLs, retry classification tokens). Adapters,
# services, and routers READ these values; they never hard-code them.
#
# Adapted from the reference ``config/ai_visibility.py``. MVP engine transports
# (decision B-3):
#   * chatgpt reaches MVP via ``openrouter`` ONLY (direct OpenAI is a reserved
#     fast-follow, disabled here — no ``openai`` transport is approved);
#   * gemini via ``google`` (direct) or ``openrouter``;
#   * claude via ``anthropic`` (direct) or ``openrouter``.
from __future__ import annotations

from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Logical engines (what the user asked for) ----------------------------
ENGINE_CHATGPT: Final = "chatgpt"
ENGINE_GEMINI: Final = "gemini"
ENGINE_CLAUDE: Final = "claude"
LOGICAL_ENGINES: Final[frozenset[str]] = frozenset(
    {ENGINE_CHATGPT, ENGINE_GEMINI, ENGINE_CLAUDE}
)

# --- Transport providers (how we physically reach the engine) -------------
TRANSPORT_ANTHROPIC: Final = "anthropic"
TRANSPORT_GOOGLE: Final = "google"
TRANSPORT_OPENROUTER: Final = "openrouter"
# Reserved fast-follow, DISABLED at MVP (decision B-3). Defined so the enum is
# stable, but never present in the approved-route catalog below.
TRANSPORT_OPENAI: Final = "openai"

# Transports a BYOK ``ProviderConnection`` may declare at MVP.
MVP_TRANSPORTS: Final[frozenset[str]] = frozenset(
    {TRANSPORT_ANTHROPIC, TRANSPORT_GOOGLE, TRANSPORT_OPENROUTER}
)
# Superset including reserved transports (validation / future use).
ALL_TRANSPORTS: Final[frozenset[str]] = MVP_TRANSPORTS | {TRANSPORT_OPENAI}

# --- Approved routes: logical engine -> {transport: default model} --------
# One catalog, the single source of truth for which (engine, transport, model)
# tuples are allowed at MVP. The ``/provider-catalog`` endpoint projects this;
# adapters validate their requested model against it.
APPROVED_ROUTES: Final[dict[str, dict[str, str]]] = {
    ENGINE_CHATGPT: {
        # chatgpt is OpenRouter-only at MVP (no direct openai transport).
        TRANSPORT_OPENROUTER: "openai/gpt-5.4",
    },
    ENGINE_GEMINI: {
        TRANSPORT_GOOGLE: "gemini-flash-latest",
        TRANSPORT_OPENROUTER: "google/gemini-2.5-flash",
    },
    ENGINE_CLAUDE: {
        TRANSPORT_ANTHROPIC: "claude-sonnet-4-6",
        TRANSPORT_OPENROUTER: "anthropic/claude-sonnet-4.6",
    },
}

# Model-prefix allowlists for native web-search over OpenRouter, keyed by the
# logical engine. A requested OpenRouter model must match its engine's surface.
OPENROUTER_MODEL_PREFIXES: Final[dict[str, tuple[str, ...]]] = {
    ENGINE_CHATGPT: ("openai/gpt-5", "openai/gpt-4.1", "openai/o3", "openai/o4"),
    ENGINE_CLAUDE: ("anthropic/claude-",),
    ENGINE_GEMINI: ("google/gemini-",),
}


def transports_for_engine(logical_engine: str) -> frozenset[str]:
    """Approved transports for a logical engine (empty if unknown)."""
    return frozenset(APPROVED_ROUTES.get(logical_engine, {}))


def is_route_approved(logical_engine: str, transport_provider: str) -> bool:
    """True when (engine, transport) is an approved MVP route."""
    return transport_provider in APPROVED_ROUTES.get(logical_engine, {})


def default_model(logical_engine: str, transport_provider: str) -> str:
    """The catalog default model for an approved (engine, transport) route."""
    return APPROVED_ROUTES.get(logical_engine, {}).get(transport_provider, "")


def engines_for_transport(transport_provider: str) -> tuple[str, ...]:
    """Logical engines reachable through a transport, in catalog order."""
    return tuple(
        engine
        for engine, routes in APPROVED_ROUTES.items()
        if transport_provider in routes
    )


def default_probe_engine(transport_provider: str) -> str:
    """A logical engine to use when probing a transport's connectivity.

    Picks the first engine the transport can serve so a connectivity test can
    build a concrete adapter/model without a caller-supplied route.
    """
    engines = engines_for_transport(transport_provider)
    return engines[0] if engines else ""


# --- Retry / error classification tokens (recorded on tests + attempts) ---
ERROR_TIMEOUT: Final = "timeout"
ERROR_CONNECTION: Final = "connection"
ERROR_RATE_LIMIT: Final = "rate_limit"
ERROR_SERVER: Final = "server_error"
ERROR_CLIENT: Final = "client_error"
ERROR_AUTH: Final = "auth_failure"
ERROR_PARSE: Final = "parse_error"
ERROR_UNKNOWN: Final = "unknown"
ERROR_INVALID_SURFACE: Final = "invalid_surface"

RETRYABLE_ERRORS: Final[frozenset[str]] = frozenset(
    {ERROR_TIMEOUT, ERROR_CONNECTION, ERROR_RATE_LIMIT, ERROR_SERVER}
)

# --- Connectivity-test statuses -------------------------------------------
TEST_STATUS_OK: Final = "ok"
TEST_STATUS_FAILED: Final = "failed"

# Neutral, brand-free probe used by the ``/test`` endpoint. The tracked
# brand/competitor list is NEVER sent to a provider (invariant 6).
PROBE_PROMPT: Final = "Reply with the single word: ok."


class ProviderCatalogSettings(BaseSettings):
    """Tunable answer-engine knobs (env-overridable, invariant 1).

    Provider-agnostic guardrails plus the transport endpoint URLs. A single set
    of knobs bounds every transport so a stray call cannot run away in tokens or
    time regardless of provider.
    """

    model_config = SettingsConfigDict(env_prefix="PROVIDER_", extra="ignore")

    # Endpoint URLs (overridable per environment / for a self-hosted gateway).
    google_interactions_url: str = (
        "https://generativelanguage.googleapis.com/v1beta/interactions"
    )
    anthropic_messages_url: str = "https://api.anthropic.com/v1/messages"
    openrouter_chat_completions_url: str = (
        "https://openrouter.ai/api/v1/chat/completions"
    )
    anthropic_version: str = "2023-06-01"
    # Caps server-side web_search invocations per Anthropic request.
    anthropic_max_uses: int = 3
    # Per-call output-token cap sent to every transport payload.
    max_output_tokens: int = 4096
    # HTTP client timeout for a single provider call.
    request_timeout_seconds: float = 60.0
    # Shorter timeout for the lightweight connectivity probe.
    test_timeout_seconds: float = 20.0
    # Title header sent to OpenRouter for attribution.
    openrouter_app_title: str = "Searchify AI Visibility"


provider_catalog_settings = ProviderCatalogSettings()
