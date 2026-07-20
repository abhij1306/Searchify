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
    "- Prompts must be predominantly UNBRANDED discovery queries: questions a "
    "consumer would ask before knowing any specific brand. At least 8 in "
    "every 10 prompts must NOT contain the brand's name, its aliases, or any "
    "competitor's name. Measuring unaided visibility is the whole point — a "
    "prompt that names the brand trivially guarantees a mention and corrupts "
    "the score.\n"
    "- At most 2 in every 10 prompts may be branded/comparison queries, and "
    "only where a real consumer would naturally name a brand (e.g. an "
    "explicit head-to-head comparison).\n"
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

    # ``ge=1`` floors mirror ``PromptGenerateRequest.count``'s ``ge=1``: a
    # zero/negative env override would otherwise produce an unrequestable
    # default or an always-rejecting cap, so fail at settings construction.
    default_count: int = Field(
        default=10,
        ge=1,
        validation_alias=AliasChoices(
            "GENERATION_DEFAULT_COUNT", "generation_default_count"
        ),
    )
    max_count: int = Field(
        default=20,
        ge=1,
        validation_alias=AliasChoices("GENERATION_MAX_COUNT", "generation_max_count"),
    )
    # Set-wide pool of prompts that are ``active`` (audit/scheduled-run
    # eligible across all three AI providers). Generation promotes the
    # earliest newly-inserted prompts to fill this pool; anything beyond it
    # stays ``proposed`` until a human promotes it. Existing manual/active
    # rows count toward the pool; archived rows are never auto-reactivated.
    active_threshold: int = Field(
        default=20,
        ge=1,
        validation_alias=AliasChoices(
            "GENERATION_ACTIVE_THRESHOLD", "generation_active_threshold"
        ),
    )
    # Upper bound on how many existing prompt texts are sent to the model as
    # "do not duplicate" context, so the user message can't grow unbounded as
    # a set accumulates prompts. Must be >= 0: a negative env override would
    # silently reverse the slice (``[:negative]`` drops from the tail), so
    # reject it at construction. Zero is valid — it sends no existing prompts.
    existing_prompt_context_limit: int = Field(
        default=200,
        ge=0,
        validation_alias=AliasChoices(
            "GENERATION_EXISTING_PROMPT_CONTEXT_LIMIT",
            "generation_existing_prompt_context_limit",
        ),
    )
    # Cap on the share of the ACTIVE pool that may be branded prompts (brand,
    # alias, or competitor name in the text — the deterministic ``branded``
    # flag). A branded prompt trivially guarantees a brand mention, so an
    # active pool dominated by them inflates the Visibility Score toward 100%.
    # Auto-activation never exceeds this share; a human can still promote
    # branded prompts past it deliberately. 0 disables auto-activating any
    # branded prompt; 1 disables the cap.
    max_branded_active_share: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "GENERATION_MAX_BRANDED_ACTIVE_SHARE",
            "generation_max_branded_active_share",
        ),
    )


prompt_generation_settings = PromptGenerationSettings()
