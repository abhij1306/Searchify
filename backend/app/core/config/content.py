# AI Content generation configuration (invariant 1: config lives here).
#
# Owns every tunable knob for the content-generation vertical: the env-driven
# provider settings (Mistral default, ``SecretStr`` key — deliberately NOT the
# BYOK ``ProviderConnection`` path used for measurement), the output-type
# vocabulary, prompt/context caps, retry budget, and the
# ``PostgresQueueSpec`` that parameterizes the shared generic queue over
# ``ContentGeneration`` rows. Service/worker/adapter code READS these; it never
# hard-codes the literals inline.
from __future__ import annotations

from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.task_queue import (
    ERROR_MAX_ATTEMPTS,
    PostgresQueueSpec,
)

if TYPE_CHECKING:
    # Type-only: config never imports a model at runtime (circular import).
    from app.models.content import ContentGeneration

# --- Providers -------------------------------------------------------------
CONTENT_PROVIDER_MISTRAL: Final = "mistral"
CONTENT_KNOWN_PROVIDERS: Final[frozenset[str]] = frozenset({CONTENT_PROVIDER_MISTRAL})

# --- Output types ----------------------------------------------------------
CONTENT_OUTPUT_TYPE_WEBSITE_PAGE: Final = "website_page"
CONTENT_OUTPUT_TYPES: Final[frozenset[str]] = frozenset(
    {CONTENT_OUTPUT_TYPE_WEBSITE_PAGE}
)
CONTENT_DEFAULT_OUTPUT_TYPE: Final = CONTENT_OUTPUT_TYPE_WEBSITE_PAGE

# --- Website-context statuses (frozen on the generation row) --------------
CONTEXT_STATUS_INCLUDED: Final = "included"
CONTEXT_STATUS_UNAVAILABLE: Final = "unavailable"
CONTEXT_STATUS_DISABLED: Final = "disabled"
CONTEXT_STATUSES: Final[frozenset[str]] = frozenset(
    {CONTEXT_STATUS_INCLUDED, CONTEXT_STATUS_UNAVAILABLE, CONTEXT_STATUS_DISABLED}
)

# --- Input caps ------------------------------------------------------------
CONTENT_PROMPT_MAX_LEN: Final = 4000
# Client-supplied Idempotency-Key cap — must match the DB column width so an
# overlong header is a 422 at the boundary, never a DataError mid-insert.
CONTENT_IDEMPOTENCY_KEY_MAX_LEN: Final = 128
# Deterministic history label: first prompt line trimmed to this many chars.
CONTENT_HISTORY_TITLE_MAX_LEN: Final = 80

# --- List bounds -----------------------------------------------------------
CONTENT_LIST_DEFAULT_LIMIT: Final = 50
CONTENT_LIST_MAX_LIMIT: Final = 100

# --- Website-context projection caps (bounded, deterministic) -------------
CONTENT_CONTEXT_MAX_PAGES: Final = 10
CONTEXT_MAX_H1: Final = 3
CONTEXT_MAX_H2: Final = 8
CONTENT_CONTEXT_PER_PAGE_BODY_CHARS: Final = 2000
CONTENT_CONTEXT_MAX_CHARS: Final = 16000
# Per-field hard cap applied after sanitisation (title/meta/heading strings).
CONTENT_CONTEXT_FIELD_MAX_CHARS: Final = 300

# --- Versioning + retry budget --------------------------------------------
CONTENT_GENERATOR_VERSION: Final = "content-v1"
CONTENT_MAX_ATTEMPTS: Final = 3

# --- Error tokens specific to the content vertical -------------------------
ERROR_PROVIDER_NOT_CONFIGURED: Final = "provider_not_configured"
ERROR_IDEMPOTENCY_CONFLICT: Final = "idempotency_conflict"
ERROR_CANCEL_NOT_ALLOWED: Final = "cancel_not_allowed"


class ContentSettings(BaseSettings):
    """Env-driven content-generation provider settings (``CONTENT_*``).

    The provider key is env-driven (``MISTRAL_API_KEY``, ``SecretStr``) — a
    deliberate deviation from the BYOK measurement path (user-approved): the
    content model is an app capability, not a customer-metered engine. The key
    is resolved only at call time and never enters any DTO/log/snapshot.
    """

    model_config = SettingsConfigDict(env_prefix="CONTENT_", extra="ignore")

    provider: str = CONTENT_PROVIDER_MISTRAL
    model: str = "mistral-small-latest"
    endpoint: str = Field(
        default="https://api.mistral.ai/v1/chat/completions",
        validation_alias="CONTENT_PROVIDER_ENDPOINT",
    )
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    max_output_tokens: int = Field(default=4096, gt=0)
    mistral_api_key: SecretStr = Field(
        default=SecretStr(""), validation_alias="MISTRAL_API_KEY"
    )
    lease_ttl_seconds: float = Field(default=120.0, gt=0)
    heartbeat_interval_seconds: float = Field(default=30.0, gt=0)
    poll_interval_seconds: float = Field(default=1.0, gt=0)
    retry_base_delay_seconds: float = Field(default=2.0, gt=0)
    retry_max_delay_seconds: float = Field(default=45.0, gt=0)

    @field_validator("endpoint")
    @classmethod
    def _check_endpoint(cls, value: str) -> str:
        # The endpoint is forwarded verbatim to the provider HTTP client, so a
        # bad env value must fail at startup, not mid-generation. https only;
        # plain http is allowed solely for loopback (local mock servers).
        parts = urlsplit(value)
        if not parts.hostname:
            raise ValueError("endpoint must be an absolute URL with a host")
        if parts.scheme == "https":
            return value
        if parts.scheme == "http" and parts.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            return value
        raise ValueError("endpoint must use https (http is loopback-only)")

    @model_validator(mode="after")
    def _check_operational_bounds(self) -> ContentSettings:
        # Fail at startup, not mid-run: a heartbeat slower than the lease TTL
        # guarantees lease expiry during healthy work, and a retry cap below
        # the base delay makes retry_delay() nonsensical.
        if self.heartbeat_interval_seconds >= self.lease_ttl_seconds:
            raise ValueError(
                "heartbeat_interval_seconds must be shorter than lease_ttl_seconds"
            )
        if self.retry_max_delay_seconds < self.retry_base_delay_seconds:
            raise ValueError(
                "retry_max_delay_seconds must not be below retry_base_delay_seconds"
            )
        return self

    def retry_delay(
        self, attempt: int, retry_after_seconds: float | None = None
    ) -> float:
        """Seconds before the next attempt: Retry-After if advised, else
        deterministic exponential backoff capped at the max (no RNG)."""
        cap = self.retry_max_delay_seconds
        if retry_after_seconds is not None:
            return min(retry_after_seconds, cap)
        return min(self.retry_base_delay_seconds * (2**attempt), cap)


content_settings = ContentSettings()


def _content_model() -> type[ContentGeneration]:
    # Imported lazily so this config module never imports a model at import
    # time (would create a config <-> models circular import).
    from app.models.content import ContentGeneration

    return ContentGeneration


def _content_claim_order(model: type[ContentGeneration]) -> tuple:
    # Deterministic claim order mirroring ``_audit_claim_order``: priority,
    # then FIFO by availability, then the stable randomized position.
    return (
        model.priority.desc(),
        model.available_at.asc(),
        model.randomized_position.asc(),
    )


# Parameterizes the one generic ``PostgresTaskQueue`` over ``ContentGeneration``
# rows with the content lease TTL + claim order. The content worker uses the
# queue only for claim/heartbeat/mark_running/cancel/release_expired; terminal
# attempt accounting goes through its own atomic ``finalize_attempt`` helper.
CONTENT_QUEUE_SPEC: Final[PostgresQueueSpec[ContentGeneration]] = PostgresQueueSpec(
    model_ref=_content_model,
    lease_ttl=lambda: content_settings.lease_ttl_seconds,
    claim_order=_content_claim_order,
    max_attempts_error=ERROR_MAX_ATTEMPTS,
)
