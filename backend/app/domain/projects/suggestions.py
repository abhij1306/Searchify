# AI competitor / owned-domain suggestion service for the setup form.
#
# Stateless sibling of ``domain/prompts/generation.py``: uses the app-level
# default agent (``connectors/agent``) — never a measurement engine and never
# a BYOK measurement key — but persists NOTHING. The setup form may be for a
# brand-new, unsaved project, so brand context arrives in the request body and
# suggestions are returned for the user to review in the form; the existing
# project save flow persists whatever survives review. Brand context is sent
# to the agent only after the caller has explicitly confirmed
# (``confirm_send_evidence``, enforced HERE, not just in the UI).
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.connectors.agent.client import DefaultAgentClient
from app.core.config.suggestions import (
    COMPETITOR_SUGGESTION_SYSTEM_PROMPT,
    OWNED_DOMAIN_SUGGESTION_SYSTEM_PROMPT,
    brand_suggestion_settings,
)


class SuggestionValidationError(ValueError):
    """Request-level validation failure (422 at the API layer)."""


class SuggestionOutputError(RuntimeError):
    """The agent returned output that could not be parsed into suggestions."""


# --------------------------------------------------------------------------
# Agent-output contracts (strict, unit-testable without a live provider)
# --------------------------------------------------------------------------
class SuggestedCompetitor(BaseModel):
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)


class CompetitorSuggestionOutput(BaseModel):
    competitors: list[SuggestedCompetitor] = Field(default_factory=list)


class OwnedDomainSuggestionOutput(BaseModel):
    domains: list[str] = Field(default_factory=list)


# Mirrors the frontend ``DOMAIN_PATTERN`` (``lib/setup/forms.ts``) so every
# domain this service returns passes the form's per-row validation on append.
_DOMAIN_PATTERN = re.compile(
    r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$", re.IGNORECASE
)


def _normalize_domain(value: str) -> str | None:
    """Coerce agent output to a bare lowercase domain, or ``None`` if hopeless.

    Strips scheme, ``www.`` prefix, path/query, port, and trailing dots, then
    validates the remainder; the model sometimes returns URLs despite the
    bare-domain instruction, and dropping those silently would waste usable
    suggestions.
    """
    candidate = value.strip().lower()
    if not candidate:
        return None
    candidate = re.sub(r"^[a-z][a-z0-9+.-]*://", "", candidate)
    candidate = candidate.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    candidate = candidate.split("@")[-1].split(":", 1)[0]
    candidate = candidate.removeprefix("www.").rstrip(".")
    if not candidate or not _DOMAIN_PATTERN.match(candidate):
        return None
    return candidate


# --------------------------------------------------------------------------
# Parsing (strict on structure, lenient on content)
# --------------------------------------------------------------------------
def parse_competitor_output(
    raw: str, *, existing_names: list[str]
) -> tuple[list[SuggestedCompetitor], int]:
    """Parse + sanitize the agent's JSON into competitor suggestions.

    Strict on structure (malformed JSON / wrong shape raises), lenient on
    content: blank names dropped, invalid domains normalized or dropped,
    duplicates collapsed case-insensitively — both within the response and
    against ``existing_names`` already in the caller's form.

    Returns ``(competitors, dropped_duplicate_count)``.
    """
    try:
        output = CompetitorSuggestionOutput.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise SuggestionOutputError(f"Unparseable agent output: {exc}") from exc

    seen = {name.strip().casefold() for name in existing_names if name.strip()}
    dropped = 0
    competitors: list[SuggestedCompetitor] = []
    for competitor in output.competitors:
        name = competitor.name.strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        aliases: list[str] = []
        for alias in competitor.aliases:
            alias = alias.strip()
            if alias and alias.casefold() not in {a.casefold() for a in aliases}:
                aliases.append(alias)
        domains: list[str] = []
        for domain in competitor.domains:
            normalized = _normalize_domain(domain)
            if normalized and normalized not in domains:
                domains.append(normalized)
        competitors.append(
            SuggestedCompetitor(name=name, aliases=aliases, domains=domains)
        )
    if not competitors:
        raise SuggestionOutputError("Agent output contained no usable competitors")
    return competitors, dropped


def parse_owned_domain_output(
    raw: str, *, existing_domains: list[str]
) -> tuple[list[str], int]:
    """Parse + sanitize the agent's JSON into owned-domain suggestions.

    Returns ``(domains, dropped_duplicate_count)``. Unnormalizable candidates
    are dropped silently (not counted as duplicates); duplicates are counted
    both within the response and against ``existing_domains``.
    """
    try:
        output = OwnedDomainSuggestionOutput.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise SuggestionOutputError(f"Unparseable agent output: {exc}") from exc

    seen = {
        normalized
        for domain in existing_domains
        if (normalized := _normalize_domain(domain)) is not None
    }
    dropped = 0
    domains: list[str] = []
    for candidate in output.domains:
        normalized = _normalize_domain(candidate)
        if normalized is None:
            continue
        if normalized in seen:
            dropped += 1
            continue
        seen.add(normalized)
        domains.append(normalized)
    if not domains:
        raise SuggestionOutputError("Agent output contained no usable domains")
    return domains, dropped


# --------------------------------------------------------------------------
# Request building (pure)
# --------------------------------------------------------------------------
def _brand_context_lines(brand_context: dict[str, Any]) -> list[str]:
    return [
        f"Brand: {brand_context.get('brand_name', '')}",
        f"Brand aliases: {', '.join(brand_context.get('brand_aliases', [])) or 'none'}",
        f"Website: {brand_context.get('website_url') or 'unspecified'}",
        f"Market country: {brand_context.get('country_code') or 'unspecified'}",
        f"Language: {brand_context.get('language_code') or 'unspecified'}",
    ]


def build_competitor_user_message(
    *, brand_context: dict[str, Any], existing_names: list[str], count: int
) -> str:
    """Assemble the user message with brand evidence + suggestion constraints."""
    lines = _brand_context_lines(brand_context)
    lines.append(f"Suggest exactly {count} competitors.")
    if existing_names:
        lines.append(
            "Existing competitors (do NOT duplicate any of these):\n- "
            + "\n- ".join(existing_names)
        )
    return "\n".join(lines)


def build_owned_domain_user_message(
    *, brand_context: dict[str, Any], existing_domains: list[str], count: int
) -> str:
    """Assemble the user message with brand evidence + suggestion constraints."""
    lines = _brand_context_lines(brand_context)
    lines.append(
        f"Suggest up to {count} domains owned and operated by this brand. "
        "Only first-party domains: NOT competitor domains, NOT typosquat or "
        "unintended domains."
    )
    if existing_domains:
        lines.append(
            "Existing owned domains (do NOT duplicate any of these):\n- "
            + "\n- ".join(existing_domains)
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def validate_suggestion_payload(payload: Any) -> None:
    """Confirmation + bounds checks (422 at the API layer).

    Pure and agent-free so the API layer can order guards as 422 -> 503: an
    invalid payload must fail validation even when no agent is configured.
    Runs BEFORE any brand context is assembled for the agent — the backend
    enforces consent, never just the UI.
    """
    if not payload.confirm_send_evidence:
        raise SuggestionValidationError(
            "confirm_send_evidence must be true to send brand evidence to the "
            "default agent"
        )
    max_count = brand_suggestion_settings.max_count
    if payload.count > max_count:
        raise SuggestionValidationError(
            f"count must be at most {max_count} (requested {payload.count})"
        )


def _payload_brand_context(payload: Any) -> dict[str, Any]:
    return {
        "brand_name": payload.brand_name,
        "brand_aliases": [a for a in payload.brand_aliases if a.strip()],
        "website_url": payload.website_url,
        "country_code": payload.country_code,
        "language_code": payload.language_code,
    }


async def suggest_competitors(
    *, payload: Any, agent: DefaultAgentClient
) -> tuple[list[SuggestedCompetitor], int]:
    """Validate consent/bounds, call the agent, and parse competitor suggestions."""
    validate_suggestion_payload(payload)
    existing = [n for n in payload.existing_competitor_names if n.strip()]
    user_message = build_competitor_user_message(
        brand_context=_payload_brand_context(payload),
        existing_names=existing,
        count=payload.count,
    )
    raw = await agent.complete_json(
        system=COMPETITOR_SUGGESTION_SYSTEM_PROMPT, user=user_message
    )
    competitors, dropped = parse_competitor_output(raw, existing_names=existing)
    return competitors[: payload.count], dropped


async def suggest_owned_domains(
    *, payload: Any, agent: DefaultAgentClient
) -> tuple[list[str], int]:
    """Validate consent/bounds, call the agent, and parse owned-domain suggestions."""
    validate_suggestion_payload(payload)
    existing = [d for d in payload.existing_owned_domains if d.strip()]
    user_message = build_owned_domain_user_message(
        brand_context=_payload_brand_context(payload),
        existing_domains=existing,
        count=payload.count,
    )
    raw = await agent.complete_json(
        system=OWNED_DOMAIN_SUGGESTION_SYSTEM_PROMPT, user=user_message
    )
    domains, dropped = parse_owned_domain_output(raw, existing_domains=existing)
    return domains[: payload.count], dropped
