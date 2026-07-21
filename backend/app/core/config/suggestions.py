# Brand-suggestion configuration (invariant 1: all config lives here).
#
# Owns the knobs and system-prompt templates for the AI-assisted setup-form
# suggestions (competitors and owned domains). Domain and API code READ these
# values; they never hard-code the literals inline. The suggestion model is
# the app-level default agent (``config/agent.py``) — never a measurement
# engine.
from __future__ import annotations

from typing import Final

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- System prompts ---------------------------------------------------------
# Neutral instructions for the default agent. The brand context is supplied in
# the *user* message by the request builders; the response contracts are strict
# JSON so the parsers stay deterministic and unit-testable.
COMPETITOR_SUGGESTION_SYSTEM_PROMPT: Final = (
    "You are a market-research assistant. Given a brand's context, you "
    "identify that brand's direct competitors: real companies a consumer "
    "would plausibly consider instead of the brand, in the same market and "
    "product category.\n"
    "Rules:\n"
    "- Only real, currently operating companies. Never invent a company.\n"
    "- Never include the brand itself or any of its aliases as a competitor.\n"
    "- Never duplicate any of the existing competitors you are shown.\n"
    "- For each competitor, include its common short aliases (if any) and its "
    "primary web domains as bare domains (e.g. acme.com — no scheme, no "
    "path).\n"
    'Respond with ONLY a JSON object of the shape: {"competitors": [{"name": '
    'str, "aliases": [str], "domains": [str]}]}. No prose, no markdown.'
)

OWNED_DOMAIN_SUGGESTION_SYSTEM_PROMPT: Final = (
    "You are a brand-research assistant. Given a brand's context, you list "
    "the web domains that brand itself owns and operates (first-party "
    "domains only).\n"
    "Rules:\n"
    "- Only domains owned by THIS brand: the apex domain, common regional or "
    "ccTLD variants, and product/subsidiary domains it operates.\n"
    "- NEVER include competitor domains.\n"
    "- NEVER include typosquat, lookalike, or otherwise unintended domains — "
    "only domains the brand legitimately operates.\n"
    "- Never duplicate any of the existing domains you are shown.\n"
    "- Return bare domains only (e.g. example.com or example.co.uk — no "
    "scheme, no www prefix, no path).\n"
    'Respond with ONLY a JSON object of the shape: {"domains": [str]}. '
    "No prose, no markdown."
)


class BrandSuggestionSettings(BaseSettings):
    """Env-overridable suggestion knobs (``BRAND_SUGGESTION_*``)."""

    model_config = SettingsConfigDict(extra="ignore")

    # ``ge=1`` floors mirror the request models' ``ge=1`` on ``count``: a
    # zero/negative env override would otherwise produce an unrequestable
    # default or an always-rejecting cap, so fail at settings construction.
    default_count: int = Field(
        default=5,
        ge=1,
        validation_alias=AliasChoices(
            "BRAND_SUGGESTION_DEFAULT_COUNT", "brand_suggestion_default_count"
        ),
    )
    max_count: int = Field(
        default=15,
        ge=1,
        validation_alias=AliasChoices(
            "BRAND_SUGGESTION_MAX_COUNT", "brand_suggestion_max_count"
        ),
    )

    @model_validator(mode="after")
    def _default_within_max(self) -> BrandSuggestionSettings:
        # A default above the cap would make every omitted-count request fail
        # ``validate_suggestion_payload``; reject the env combination up front.
        if self.default_count > self.max_count:
            raise ValueError(
                "BRAND_SUGGESTION_DEFAULT_COUNT must not exceed "
                f"BRAND_SUGGESTION_MAX_COUNT ({self.default_count} > {self.max_count})"
            )
        return self


brand_suggestion_settings = BrandSuggestionSettings()
