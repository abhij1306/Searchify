# Prompt-generation configuration (invariant 1: all config lives here).
#
# Owns the knobs, enumerations, and the system-prompt template for the
# AI-assisted prompt/topic generation surface (flips the ``/generate`` 501
# stub). Domain and API code READ these values; they never hard-code the
# literals inline. The generation model itself is the app-level default agent
# (``config/agent.py``) — never a measurement engine.
from __future__ import annotations

from typing import Final

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Prompt review status --------------------------------------------------
# Lifecycle of a prompt in the library. Generated suggestions land as
# ``proposed`` and are audit-INELIGIBLE until a human accepts them (the
# roadmap's "no auto-run" rule); ``archived`` keeps history without deleting.
PROMPT_STATUS_PROPOSED: Final = "proposed"
PROMPT_STATUS_ACTIVE: Final = "active"
PROMPT_STATUS_ARCHIVED: Final = "archived"
PROMPT_STATUSES: Final[frozenset[str]] = frozenset(
    {PROMPT_STATUS_PROPOSED, PROMPT_STATUS_ACTIVE, PROMPT_STATUS_ARCHIVED}
)
DEFAULT_PROMPT_STATUS: Final = PROMPT_STATUS_ACTIVE

# --- Topic origin ----------------------------------------------------------
TOPIC_ORIGIN_MANUAL: Final = "manual"
TOPIC_ORIGIN_GENERATED: Final = "generated"
TOPIC_ORIGINS: Final[frozenset[str]] = frozenset(
    {TOPIC_ORIGIN_MANUAL, TOPIC_ORIGIN_GENERATED}
)

# --- Generation pipeline version (stamped into generation_evidence) --------
GENERATOR_VERSION: Final = "prompt-gen-v1"

# --- System prompt ---------------------------------------------------------
# Neutral instruction for the default agent. The brand context is supplied in
# the *user* message by the request builder; the response contract is strict
# JSON so the parser stays deterministic and unit-testable.
GENERATION_SYSTEM_PROMPT: Final = (
    "You are an AEO (answer-engine optimization) research assistant. Given a "
    "brand's context, you propose realistic consumer search prompts a person "
    "might ask an AI assistant, organized under topical categories.\n"
    "Rules:\n"
    "- Prompts must read like natural consumer questions or requests, not "
    "marketing copy.\n"
    "- Cover a mix of unbranded discovery queries and, where natural, "
    "branded/comparison queries.\n"
    "- Reuse an existing topic name verbatim when a prompt fits it; only "
    "invent a new topic when none fits.\n"
    "- Never duplicate any of the existing prompts you are shown.\n"
    "- Each prompt's intent must be one of: discovery, comparison, purchase, "
    "service, local.\n"
    'Respond with ONLY a JSON object of the shape: {"topics": [{"name": str, '
    '"prompts": [{"text": str, "intent": str}]}]}. No prose, no markdown.'
)


class PromptGenerationSettings(BaseSettings):
    """Env-overridable generation knobs (``GENERATION_*``).

    ``max_count`` stands in for the future subscription-tier limit; keep it in
    env until billing tiers exist.
    """

    model_config = SettingsConfigDict(extra="ignore")

    default_count: int = Field(
        default=10,
        validation_alias=AliasChoices(
            "GENERATION_DEFAULT_COUNT", "generation_default_count"
        ),
    )
    max_count: int = Field(
        default=20,
        validation_alias=AliasChoices("GENERATION_MAX_COUNT", "generation_max_count"),
    )


prompt_generation_settings = PromptGenerationSettings()
