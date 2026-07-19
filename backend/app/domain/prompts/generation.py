# AI prompt/topic generation service (flips the /generate 501 stub).
#
# Uses the app-level default agent (``connectors/agent``) — never a measurement
# engine and never a BYOK measurement key. Brand context is sent to the agent
# only after the caller has explicitly confirmed (``confirm_send_evidence``,
# enforced HERE, not just in the UI). Suggestions are persisted with full
# ``generation_evidence`` provenance (invariant 4) via a conflict-safe upsert
# on the per-set normalized-text hash, so concurrent generations can never
# double-insert a concept. The earliest generated rows fill a set-wide pool of
# ``active`` prompts (``GENERATION_ACTIVE_THRESHOLD``, default 20) — the
# scheduled-run/audit eligibility gate — promoted atomically in the insert
# transaction under a prompt-set advisory lock so concurrent generations can
# never exceed the pool; rows beyond it stay ``proposed`` until a human
# promotes them (planner filters status='active').
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.connectors.agent.client import DefaultAgentClient
from app.core.config.projects import PROMPT_INTENTS, PROMPT_ORIGIN_GENERATED
from app.core.config.prompts import (
    GENERATION_SYSTEM_PROMPT,
    GENERATOR_VERSION,
    PROMPT_STATUS_ACTIVE,
    PROMPT_STATUS_PROPOSED,
    TOPIC_ORIGIN_GENERATED,
    prompt_generation_settings,
)
from app.domain.projects.shim import project_scoring_identity
from app.domain.prompts.locks import acquire_project_lock, acquire_prompt_set_lock
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


def parse_generation_output(raw: str) -> tuple[list[SuggestedTopic], int]:
    """Parse + sanitize the agent's JSON into suggested topics.

    Strict on structure (malformed JSON / wrong shape raises), lenient on
    content: unknown intents are blanked, empty prompts dropped, duplicate
    texts within the response collapsed (first wins), empty topics dropped.

    Returns ``(topics, intra_response_duplicate_count)`` where the count is
    the number of prompt texts collapsed because an equivalent text already
    appeared earlier in the same response. The caller folds this into the
    total ``dropped_duplicates`` alongside DB ``ON CONFLICT`` drops.
    """
    try:
        output = GenerationOutput.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise GenerationOutputError(f"Unparseable agent output: {exc}") from exc

    seen_hashes: set[str] = set()
    intra_duplicates = 0
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
                intra_duplicates += 1
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
    return topics, intra_duplicates


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
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_set_id: uuid.UUID,
    for_update: bool = False,
) -> PromptSet:
    stmt = (
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
    if for_update:
        # Row-lock only the prompt-set row (never the joined project) so the
        # lock scope is minimal and the ordering — advisory lock first, then
        # this row lock — is identical to every other writer, so no deadlock.
        stmt = stmt.with_for_update(of=PromptSet)
    result = await session.execute(stmt)
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


def _resolve_target_topic(prompt_set: PromptSet, payload: Any) -> Topic | None:
    """Resolve ``payload.topic_id`` against the prompt set's project topics.

    Returns ``None`` for unscoped generation. Raises
    ``GenerationValidationError`` (422 at the API layer) when a ``topic_id`` is
    given but is not a topic of this set's project — including the case where a
    topic that existed at validation time was deleted before persistence, so a
    disappearance surfaces as a scoped 422 rather than an FK 500.
    """
    if payload.topic_id is None:
        return None
    target_topic = next(
        (t for t in prompt_set.project.topics if t.id == payload.topic_id), None
    )
    if target_topic is None:
        raise GenerationValidationError("topic_id is not a topic of this project")
    return target_topic


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
    return _resolve_target_topic(prompt_set, payload)


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


def _cap_suggestions_to_count(
    suggestions: list[SuggestedTopic], count: int
) -> list[SuggestedTopic]:
    """Trim parsed suggestions to at most ``count`` prompts total.

    A misbehaving model can return more prompts than requested; enforce the
    cap before persistence, preserving topic grouping and response order
    (topics are truncated once the budget is spent, and an emptied topic is
    dropped).
    """
    if count <= 0:
        return []
    remaining = count
    capped: list[SuggestedTopic] = []
    for topic in suggestions:
        if remaining <= 0:
            break
        kept = topic.prompts[:remaining]
        if kept:
            capped.append(SuggestedTopic(name=topic.name, prompts=kept))
            remaining -= len(kept)
    return capped


async def _get_or_create_topic(
    session: AsyncSession,
    *,
    project: Project,
    name: str,
    topics_by_name: dict[str, Topic],
) -> Topic:
    """Resolve an existing topic by case-insensitive name or create it.

    ``lower()`` matches the DB's functional unique index on ``lower(name)``.
    A create race is caught on a savepoint so the prompts already inserted in
    this transaction survive, then the race winner is re-selected.
    """
    topic = topics_by_name.get(name.lower())
    if topic is not None:
        return topic
    topic = Topic(
        project_id=project.id,
        name=name.strip(),
        origin=TOPIC_ORIGIN_GENERATED,
    )
    try:
        # Savepoint so a lost create race doesn't discard the prompts already
        # inserted in this transaction.
        async with session.begin_nested():
            session.add(topic)
    except IntegrityError:
        # Lost the create race: re-select the winner. Match case-insensitively
        # — the unique index is on ``lower(name)``, so casing may differ.
        topic = (
            await session.execute(
                select(Topic).where(
                    Topic.project_id == project.id,
                    func.lower(Topic.name) == name.strip().lower(),
                )
            )
        ).scalar_one()
    topics_by_name[topic.name.lower()] = topic
    return topic


async def _insert_prompts_returning(
    session: AsyncSession,
    *,
    prompt_set: PromptSet,
    topic: Topic,
    prompts: list[SuggestedPrompt],
    brand_context: dict[str, Any],
    evidence_base: dict[str, Any],
) -> tuple[list[uuid.UUID], int]:
    """Conflict-safe multi-row insert for one topic batch as ``proposed``.

    The parse step already de-duplicated texts across the whole response, so
    rows within a batch can never conflict with each other — only with
    pre-existing prompts, which ``on_conflict_do_nothing`` silently skips.
    Returns ``(inserted_ids, dropped_count)`` where dropped = rows submitted
    minus ids the DB actually returned (in submitted order).
    """
    submitted_ids: list[uuid.UUID] = [uuid.uuid4() for _ in prompts]
    rows = [
        {
            "id": submitted_ids[idx],
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
        for idx, prompt in enumerate(prompts)
    ]
    stmt = (
        pg_insert(Prompt)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_prompt_set_normalized_text")
        .returning(Prompt.id)
    )
    returned = set((await session.execute(stmt)).scalars().all())
    # Preserve deterministic response order: keep submitted order, drop the
    # ids the DB rejected as conflicts.
    inserted_ids = [pid for pid in submitted_ids if pid in returned]
    return inserted_ids, len(rows) - len(inserted_ids)


async def _activate_first_n(
    session: AsyncSession,
    *,
    prompt_set_id: uuid.UUID,
    ordered_new_ids: list[uuid.UUID],
) -> None:
    """Promote enough newly-inserted prompts to fill the set-wide active pool.

    Assumes the caller already holds the prompt-set writer lock. Counts the
    prompts already ``active`` in the set (existing manual/active rows count
    toward the cap; archived rows are never auto-reactivated), then activates
    only the earliest ``ordered_new_ids`` needed to reach
    ``active_threshold``. Everything beyond the pool stays ``proposed`` until a
    human promotes it.
    """
    if not ordered_new_ids:
        return
    threshold = prompt_generation_settings.active_threshold
    active_count = (
        await session.execute(
            select(func.count())
            .select_from(Prompt)
            .where(
                Prompt.prompt_set_id == prompt_set_id,
                Prompt.status == PROMPT_STATUS_ACTIVE,
            )
        )
    ).scalar_one()
    remaining = threshold - active_count
    if remaining <= 0:
        return
    to_activate = ordered_new_ids[:remaining]
    await session.execute(
        update(Prompt)
        .where(Prompt.id.in_(to_activate))
        .values(status=PROMPT_STATUS_ACTIVE)
    )


async def generate_prompts(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_set_id: uuid.UUID,
    payload: Any,
    agent: DefaultAgentClient,
    prompt_set: PromptSet | None = None,
) -> tuple[list[Prompt], list[Topic], int]:
    """Generate topic-organized prompt suggestions into the set.

    Returns ``(inserted_prompts, touched_topics, dropped_duplicate_count)``.
    Caller (the API layer) resolves the agent client so configuration errors
    surface before any DB work, and monkeypatching in tests stays trivial.
    ``prompt_set`` may be passed pre-loaded (from
    ``validate_generation_request``) to avoid a second scope query; the
    payload checks always re-run here so direct service calls stay guarded.

    The earliest inserted prompts are promoted to ``active`` to fill the
    set-wide pool of ``active_threshold`` (default 20); the rest stay
    ``proposed`` until a human promotes them. ``active`` is the scheduled-run
    eligibility gate across all three AI providers. Insertion and activation
    commit together under a prompt-set writer lock so concurrent generations
    can never exceed the pool.
    """
    # 1. Scope first (404 before anything runs), then confirmation + bounds.
    if prompt_set is None:
        prompt_set = await _load_prompt_set_with_project(
            session, workspace_id=workspace_id, prompt_set_id=prompt_set_id
        )
    target_topic = _validate_generation_payload(prompt_set, payload)

    project = prompt_set.project
    project_id = project.id

    # 2. Brand evidence via the one existing serializer (invariant 2). Bound
    #    the existing-prompt context so the user message can't grow unbounded.
    brand_context = project_scoring_identity(project)
    context_limit = prompt_generation_settings.existing_prompt_context_limit
    user_message = build_generation_user_message(
        brand_context=brand_context,
        existing_topics=[t.name for t in project.topics],
        existing_prompts=[p.text for p in prompt_set.prompts][:context_limit],
        count=payload.count,
        intents=[i for i in payload.intents if i],
        target_topic=target_topic.name if target_topic is not None else "",
    )

    # 3. The only provider I/O — after all validation. End the read
    #    transaction first so no DB transaction is held across the network
    #    call (invariant 8's rule, applied to generation).
    await session.commit()
    raw = await agent.complete_json(system=GENERATION_SYSTEM_PROMPT, user=user_message)
    suggestions, intra_duplicates = parse_generation_output(raw)
    # Enforce the requested output count before any persistence.
    suggestions = _cap_suggestions_to_count(suggestions, payload.count)

    # 4. Re-open the write transaction. The objects loaded before the provider
    #    call are now stale (the set/project/topic could have been renamed or
    #    deleted mid-request), so acquire the SHARED prompt-set advisory lock
    #    (the same one the delete paths take) and then re-resolve everything
    #    fresh, row-locking the set. Deletes block on the advisory lock until we
    #    commit, so nothing can vanish between re-resolution and insertion. A
    #    disappearance that slipped in before we took the lock maps to the same
    #    scoped domain errors the endpoint already handles (404 / 422) — and an
    #    FK violation at insert (belt-and-suspenders) is mapped the same way,
    #    never an unhandled 500.
    #
    #    Lock order is fixed everywhere to preclude deadlock: PROJECT lock
    #    first (serializes topic deletes), then the PROMPT-SET lock.
    await acquire_project_lock(session, project_id)
    await acquire_prompt_set_lock(session, prompt_set_id)
    # Drop every identity-map instance loaded in the pre-provider transaction so
    # the re-resolution below reads committed state from the DB. Without this the
    # selectin-loaded ``project.topics`` collection can be served from the stale
    # identity map, letting a topic deleted mid-request appear to still exist.
    session.expire_all()
    prompt_set = await _load_prompt_set_with_project(
        session,
        workspace_id=workspace_id,
        prompt_set_id=prompt_set_id,
        for_update=True,
    )
    target_topic = _resolve_target_topic(prompt_set, payload)
    project = prompt_set.project
    # ``lower()`` (not ``casefold()``) so the in-memory match uses the same
    # canonical form as the DB's unique index on ``lower(name)``.
    topics_by_name = {topic.name.lower(): topic for topic in project.topics}

    # 5. Persist: get-or-create topics by name, then conflict-safe inserts.
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

    try:
        touched_topics: list[Topic] = []
        inserted_ids: list[uuid.UUID] = []
        dropped = intra_duplicates
        for suggestion in suggestions:
            if target_topic is not None:
                # Scoped generation: everything lands in the requested topic.
                topic = target_topic
            else:
                topic = await _get_or_create_topic(
                    session,
                    project=project,
                    name=suggestion.name,
                    topics_by_name=topics_by_name,
                )
            if topic not in touched_topics:
                touched_topics.append(topic)

            batch_ids, batch_dropped = await _insert_prompts_returning(
                session,
                prompt_set=prompt_set,
                topic=topic,
                prompts=suggestion.prompts,
                brand_context=brand_context,
                evidence_base=evidence_base,
            )
            inserted_ids.extend(batch_ids)
            dropped += batch_dropped

        # Fill the set-wide active pool from the earliest inserted rows.
        await _activate_first_n(
            session, prompt_set_id=prompt_set.id, ordered_new_ids=inserted_ids
        )

        # Hydrate the response BEFORE commit so nothing has to be refreshed
        # afterward (a post-commit refresh could itself race a delete). With
        # ``expire_on_commit=False`` these instances stay usable to the caller.
        inserted = await _hydrate_inserted(session, inserted_ids)
        for topic in touched_topics:
            await session.refresh(topic)

        await session.commit()
    except IntegrityError as exc:
        # A referenced set/topic may have disappeared despite the advisory
        # lock (e.g. lock skipped on a non-PostgreSQL dialect). Rather than
        # blindly mapping EVERY integrity error to a 404 — which would mask
        # genuine constraint bugs (unique/check/unrelated FK violations) as a
        # phantom "prompt set not found" — roll back and re-check ONLY the
        # scoped entities this request depends on. A disappeared set maps to a
        # scoped 404; a disappeared target topic maps to a scoped 422; any
        # other integrity error is unrelated and re-raised unchanged (500).
        await session.rollback()
        await _reraise_scoped_integrity_error(
            session,
            workspace_id=workspace_id,
            prompt_set_id=prompt_set_id,
            topic_id=payload.topic_id,
            exc=exc,
        )

    return inserted, touched_topics, dropped


async def _reraise_scoped_integrity_error(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_set_id: uuid.UUID,
    topic_id: uuid.UUID | None,
    exc: IntegrityError,
) -> None:
    """Map an insert-time ``IntegrityError`` to a scoped domain error.

    Called after ``session.rollback()``. Re-reads committed state to decide
    which referenced entity (if any) actually vanished:

    - prompt set gone (in this workspace) -> ``PromptSetNotFoundError`` (404);
    - scoped ``topic_id`` gone from the set's project ->
      ``GenerationValidationError`` (422);
    - neither missing -> the error is unrelated (unique/check/other FK), so
      re-raise it unchanged so a real bug surfaces as a 500, never a phantom
      not-found.

    This never returns normally: it always raises.
    """
    set_exists = (
        await session.execute(
            select(PromptSet.id)
            .join(Project, Project.id == PromptSet.project_id)
            .where(
                PromptSet.id == prompt_set_id,
                Project.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if set_exists is None:
        raise PromptSetNotFoundError("Prompt set not found") from exc

    if topic_id is not None:
        topic_exists = (
            await session.execute(
                select(Topic.id)
                .join(Project, Project.id == Topic.project_id)
                .join(PromptSet, PromptSet.project_id == Project.id)
                .where(
                    Topic.id == topic_id,
                    PromptSet.id == prompt_set_id,
                    Project.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if topic_exists is None:
            raise GenerationValidationError(
                "topic_id is not a topic of this project"
            ) from exc

    # The scoped set/topic are both intact, so the integrity error is
    # unrelated to a lost FK reference. Re-raise it so it isn't masked.
    raise exc


async def _hydrate_inserted(
    session: AsyncSession, inserted_ids: list[uuid.UUID]
) -> list[Prompt]:
    """Load the freshly inserted prompts in deterministic response order."""
    if not inserted_ids:
        return []
    by_id = {
        prompt.id: prompt
        for prompt in (
            await session.execute(select(Prompt).where(Prompt.id.in_(inserted_ids)))
        )
        .scalars()
        .all()
    }
    return [by_id[pid] for pid in inserted_ids if pid in by_id]
