# Default-agent configuration (invariant 1: all config lives in core/config).
#
# The "default agent" is the app-level, env-configured general model that powers
# assisted features (prompt generation now; content generation later). It is
# deliberately DISTINCT from:
#   * the three measurement engines (chatgpt/gemini/claude) — those are only
#     ever measured, never used for generation (roadmap non-goal), and
#   * the per-workspace BYOK ``ProviderConnection`` keys (invariant 6) — the
#     default-agent key is an application credential, not a customer secret.
# Like every credential, the key is never logged, never echoed into a DTO or
# request snapshot, and is passed only as a Bearer header at call time.
#
# The endpoint is OpenAI-compatible (``{base_url}/chat/completions``) so any
# compatible provider (Mistral, OpenAI, Groq, a local gateway, ...) works by
# swapping env values.
from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import BASE_DIR, PROJECT_ROOT


class DefaultAgentSettings(BaseSettings):
    """Env-overridable default-agent knobs (``DEFAULT_AGENT_*``).

    ``api_key`` also accepts the legacy ``MISTRALAI_API_KEY`` env name so a
    plain Mistral key in ``.env`` works without renaming.
    """

    model_config = SettingsConfigDict(
        # Same .env chain as the main ``Settings`` so a repo-root key is found.
        env_file=(str(PROJECT_ROOT / ".env"), str(BASE_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DEFAULT_AGENT_API_KEY", "MISTRALAI_API_KEY", "default_agent_api_key"
        ),
    )
    base_url: str = Field(
        default="https://api.mistral.ai/v1",
        validation_alias=AliasChoices(
            "DEFAULT_AGENT_BASE_URL", "default_agent_base_url"
        ),
    )
    model: str = Field(
        default="mistral-small-latest",
        validation_alias=AliasChoices("DEFAULT_AGENT_MODEL", "default_agent_model"),
    )
    # HTTP client timeout for a single agent call.
    timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "DEFAULT_AGENT_TIMEOUT_SECONDS", "default_agent_timeout_seconds"
        ),
    )
    # Per-call output cap so one generation cannot run away.
    max_output_tokens: int = Field(
        default=4096,
        validation_alias=AliasChoices(
            "DEFAULT_AGENT_MAX_OUTPUT_TOKENS", "default_agent_max_output_tokens"
        ),
    )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


default_agent_settings = DefaultAgentSettings()
