# AI prompt/topic generation service (flips the /generate 501 stub).
#
# Uses the app-level default agent (``connectors/agent``) — never a measurement
# engine and never a BYOK measurement key. Brand context is sent to the agent
# only after the caller has explicitly confirmed (``confirm_send_evidence``,
# enforced HERE, not just in the UI). Suggestions are persisted as
# ``status='proposed'`` prompts with full ``generation_evidence`` provenance
# (invariant 4) via a conflict-safe upsert on the per-set normalized-text hash,
# so concurrent generations can never double-insert a concept, and an audit can
# never consume an unreviewed suggestion (planner filters status='active').
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.connectors.agent.client import DefaultAgentClient
from app.core.config.projects import PROMPT_INTENTS, PROMPT_ORIGIN_GENERATED
from app.core.config.prompts import (
    GENERATION_SYSTEM_PROMPT,
    GENERATOR_VERSION,
    PROMPT_STATUS_PROPOSED,
    TOPIC_ORIGIN_GENERATED,
    prompt_generation_settings,
)
from app.domain.projects.shim import project_scoring_identity
from app.domain.prompts.normalization import prompt_text_hash
from app.domain.prompts.service import PromptSetNotFoundError
from app.models.brand import Brand
from app.models.project import Project
from app.models.prompt import Prompt, PromptSet, Topic


class GenerationValidationError(ValueError):
    """Request-level validation failure (422 at the API layer)."""


class GenerationOutputError(RuntimeError):
    """The agent returned output that could not be parsed into suggestions."""


# --------------------------------------------------------------------------
# Agent-output contract (strict, unit-testable without a live provider)
# --------------------------------------------------------------------------
class SuggestedPrompt(BaseModel):
    text: str = Field(min_length=1)
    intent: str = ""


class SuggestedTopic(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    prompts: list[SuggestedPrompt] = Field(default_factory=list)


class GenerationOutput(BaseModel):
    topics: list[SuggestedTopic] = Field(default_factory=list)


def parse_generation_output(raw: str) -> list[SuggestedTopic]:
    """Parse + sanitize the agent's JSON into suggested topics.

    Strict on structure (malformed JSON / wrong shape raises), lenient on
    content: unknown intents are blanked, empty prompts dropped, duplicate
    texts within the response collapsed (first wins), empty topics dropped.
    """
    try:
        output = GenerationOutput.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise GenerationOutputError(f"Unparseable agent output: {exc}") from exc

    seen_hashes: set[str] = set()
    topics: list[SuggestedTopic] = []
    for topic in output.topics:
        name = topic.name.strip()
        if not name:
            continue
        prompts: list[SuggestedPrompt] = []
        for prompt in topic.prompts:
            text = prompt.text.strip()
            if not text:
                continue
            text_hash = prompt_text_hash(text)
            if text_hash in seen_hashes:
                continue
            seen_hashes.add(text_hash)
            intent = prompt.intent.strip().casefold()
            prompts.append(
                SuggestedPrompt(
                    text=text,
                    intent=intent if intent in PROMPT_INTENTS else "",
                )
            )
        if prompts:
            topics.append(SuggestedTopic(name=name, prompts=prompts))
    if not topics:
        raise GenerationOutputError("Agent output contained no usable prompts")
    return topics


# --------------------------------------------------------------------------
# Request building (pure)
# --------------------------------------------------------------------------
def build_generation_user_message(
    *,
    brand_context: dict[str, Any],
    existing_topics: list[str],
    existing_prompts: list[str],
    count: int,
    intents: list[str],
    target_topic: str = "",
) -> str:
    """Assemble the user message with brand evidence + generation constraints."""
    competitors = [c["name"] for c in brand_context.get("competitors", [])]
    lines = [
        f"Brand: {brand_context.get('brand_name', '')}",
        f"Brand aliases: {', '.join(brand_context.get('brand_aliases', [])) or 'none'}",
        f"Competitors: {', '.join(competitors) or 'none'}",
        f"Market country: {brand_context.get('country_code') or 'unspecified'}",
        f"Language: {brand_context.get('language_code') or 'unspecified'}",
    ]
    if target_topic:
        lines.append(
            f"Generate prompts ONLY for this topic (use it verbatim): {target_topic}"
        )
    elif existing_topics:
        lines.append("Existing topics: " + "; ".join(existing_topics))
    if intents:
        lines.append("Restrict prompt intents to: " + ", ".join(intents))
    lines.append(f"Generate exactly {count} prompts in total across topics.")
    if existing_prompts:
        lines.append(
            "Existing prompts (do NOT duplicate any of these):\n- "
            + "\n- ".join(existing_prompts)
        )
    return "\n".join(lines)


def _brand_context_hash(brand_context: dict[str, Any]) -> str:
    canonical = json.dumps(brand_context, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
async def _load_prompt_set_with_project(
    session: AsyncSession, *, workspace_id: uuid.UUID, prompt_set_id: uuid.UUID
) -> PromptSet:
    result = await session.execute(
        select(PromptSet)
        .join(Project, Project.id == PromptSet.project_id)
        .options(
            selectinload(PromptSet.prompts),
            selectinload(PromptSet.project)
            .selectinload(Project.brand)
            .selectinload(Brand.aliases),
            selectinload(PromptSet.project).selectinload(Project.competitors),
            selectinload(PromptSet.project).selectinload(Project.owned_domains),
            selectinload(PromptSet.project).selectinload(Project.unintended_domains),
            selectinload(PromptSet.project).selectinload(Project.topics),
        )
        .where(PromptSet.id == prompt_set_id, Project.workspace_id == workspace_id)
    )
    prompt_set = result.scalars().unique().one_or_none()
    if prompt_set is None:
        raise PromptSetNotFoundError("Prompt set not found")
    return prompt_set


def _is_branded(text: str, brand_context: dict[str, Any]) -> bool:
    """Deterministic branded detection: any brand/competitor name in the text."""
    haystack = text.casefold()
    names = [brand_context.get("brand_name", "")]
    names += brand_context.get("brand_aliases", [])
    for competitor in brand_context.get("competitors", []):
        names.append(competitor.get("name", ""))
        names += competitor.get("aliases", [])
    return any(name and name.casefold() in haystack for name in names)


def _validate_generation_payload(prompt_set: PromptSet, payload: Any) -> Topic | None:
    """Confirmation + bounds + topic-ownership checks (422 at the API layer).

    Returns the target topic when ``payload.topic_id`` is set.
    """
    if not payload.confirm_send_evidence:
        raise GenerationValidationError(
            "confirm_send_evidence must be true to send brand evidence to the "
            "default agent"
        )
    max_count = prompt_generation_settings.max_count
    if payload.count > max_count:
        raise GenerationValidationError(
            f"count must be at most {max_count} (requested {payload.count})"
        )
    if payload.topic_id is None:
        return None
    target_topic = next(
        (t for t in prompt_set.project.topics if t.id == payload.topic_id), None
    )
    if target_topic is None:
        raise GenerationValidationError("topic_id is not a topic of this project")
    return target_topic


async def validate_generation_request(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_set_id: uuid.UUID,
    payload: Any,
) -> PromptSet:
    """Scope + payload validation without touching the agent.

    Lets the API layer order guards as 404 -> 422 -> 503: an invalid payload
    must fail validation even when no agent is configured.
    """
    prompt_set = await _load_prompt_set_with_project(
        session, workspace_id=workspace_id, prompt_set_id=prompt_set_id
    )
    _validate_generation_payload(prompt_set, payload)
    return prompt_set


async def generate_prompts(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_set_id: uuid.UUID,
    payload: Any,
    agent: DefaultAgentClient,
    prompt_set: PromptSet | None = None,
) -> tuple[list[Prompt], list[Topic], int]:
    """Generate topic-organized prompt suggestions into the set as ``proposed``.

    Returns ``(inserted_prompts, touched_topics, dropped_duplicate_count)``.
    Caller (the API layer) resolves the agent client so configuration errors
    surface before any DB work, and monkeypatching in tests stays trivial.
    ``prompt_set`` may be passed pre-loaded (from
    ``validate_generation_request``) to avoid a second scope query; the
    payload checks always re-run here so direct service calls stay guarded.
    """
    # 1. Scope first (404 before anything runs), then confirmation + bounds.
    if prompt_set is None:
        prompt_set = await _load_prompt_set_with_project(
            session, workspace_id=workspace_id, prompt_set_id=prompt_set_id
        )
    target_topic = _validate_generation_payload(prompt_set, payload)

    project = prompt_set.project
    # ``lower()`` (not ``casefold()``) so the in-memory match uses the same
    # canonical form as the DB's unique index on ``lower(name)``.
    topics_by_name = {topic.name.lower(): topic for topic in project.topics}

    # 2. Brand evidence via the one existing serializer (invariant 2).
    brand_context = project_scoring_identity(project)
    user_message = build_generation_user_message(
        brand_context=brand_context,
        existing_topics=[t.name for t in project.topics],
        existing_prompts=[p.text for p in prompt_set.prompts],
        count=payload.count,
        intents=[i for i in payload.intents if i],
        target_topic=target_topic.name if target_topic is not None else "",
    )

    # 3. The only provider I/O — after all validation. End the read
    #    transaction first so no DB transaction is held across the network
    #    call (invariant 8's rule, applied to generation); loaded objects
    #    stay usable because the session factory sets expire_on_commit=False.
    await session.commit()
    raw = await agent.complete_json(system=GENERATION_SYSTEM_PROMPT, user=user_message)
    suggestions = parse_generation_output(raw)

    # 4. Persist: get-or-create topics by name, then conflict-safe inserts.
    generation_run_id = str(uuid.uuid4())
    evidence_base = {
        "model_identity": {
            "transport_host": agent.base_url_host,
            "transport_model": agent.model,
        },
        "generation_run_id": generation_run_id,
        "generator_version": GENERATOR_VERSION,
        "brand_context_hash": _brand_context_hash(brand_context),
        "requested_count": payload.count,
        "requested_intents": [i for i in payload.intents if i],
    }

    touched_topics: list[Topic] = []
    inserted_ids: list[uuid.UUID] = []
    dropped = 0
    for suggestion in suggestions:
        if target_topic is not None:
            # Scoped generation: everything lands in the requested topic.
            topic = target_topic
        else:
            topic = topics_by_name.get(suggestion.name.lower())
            if topic is None:
                topic = Topic(
                    project_id=project.id,
                    name=suggestion.name.strip(),
                    origin=TOPIC_ORIGIN_GENERATED,
                )
                try:
                    # Savepoint so a lost create race doesn't discard the
                    # prompts already inserted in this transaction.
                    async with session.begin_nested():
                        session.add(topic)
                except IntegrityError:
                    # Lost the create race: re-select the winner. Match
                    # case-insensitively — the unique index is on
                    # ``lower(name)``, so the winner's casing may differ.
                    topic = (
                        await session.execute(
                            select(Topic).where(
                                Topic.project_id == project.id,
                                func.lower(Topic.name)
                                == suggestion.name.strip().lower(),
                            )
                        )
                    ).scalar_one()
                topics_by_name[topic.name.lower()] = topic
        if topic not in touched_topics:
            touched_topics.append(topic)

        # One multi-row insert per topic batch; the parse step already
        # de-duplicated texts across the whole response, so rows within a
        # batch can never conflict with each other — only with pre-existing
        # prompts, which ``on_conflict_do_nothing`` silently skips. Dropped
        # count = rows submitted minus ids the DB actually returned.
        rows = [
            {
                "id": uuid.uuid4(),
                "prompt_set_id": prompt_set.id,
                "topic_id": topic.id,
                "text": prompt.text,
                "normalized_text_hash": prompt_text_hash(prompt.text),
                "theme": topic.name,
                "intent": prompt.intent,
                "branded": _is_branded(prompt.text, brand_context),
                "enabled": True,
                "status": PROMPT_STATUS_PROPOSED,
                "origin": PROMPT_ORIGIN_GENERATED,
                "generation_evidence": evidence_base,
            }
            for prompt in suggestion.prompts
        ]
        stmt = (
            pg_insert(Prompt)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_prompt_set_normalized_text")
            .returning(Prompt.id)
        )
        returned = list((await session.execute(stmt)).scalars().all())
        inserted_ids.extend(returned)
        dropped += len(rows) - len(returned)
    await session.commit()

    inserted = (
        list(
            (
                await session.execute(
                    select(Prompt)
                    .where(Prompt.id.in_(inserted_ids))
                    .order_by(Prompt.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        if inserted_ids
        else []
    )
    for topic in touched_topics:
        await session.refresh(topic)
    return inserted, touched_topics, dropped
