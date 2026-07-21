"""Review-first AI drafting and acceptance for the curated BrandProfile."""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.agent.client import DefaultAgentClient
from app.core.config.brand_profile import (
    BRAND_PROFILE_FIELDS,
    BRAND_PROFILE_SOURCE_AI_SUGGESTED,
    BRAND_PROFILE_SOURCE_MANUAL,
    BRAND_PROFILE_SUGGESTER_VERSION,
    BRAND_PROFILE_SUGGESTION_SYSTEM_PROMPT,
)
from app.domain.projects.brand_profile import (
    BrandProfileNotFoundError,
    brand_profile_to_response,
    clean_profile_products,
)
from app.domain.projects.knowledge_base import (
    build_brand_knowledge_context,
    build_brand_knowledge_data,
)
from app.domain.projects.schemas import (
    BrandProfileAcceptResponse,
    BrandProfileDraft,
    BrandProfileSuggestionResponse,
)
from app.domain.projects.service import get_project
from app.models.brand import BrandProfile, BrandProfileSuggestion


class BrandProfileSuggestionValidationError(ValueError):
    """The draft or acceptance request violates the review contract."""


class BrandProfileSuggestionOutputError(RuntimeError):
    """The agent returned an unusable profile draft."""


class BrandProfileSuggestionNotFoundError(LookupError):
    """The immutable suggestion is absent or outside the caller's scope."""


def validate_brand_profile_suggest_request(payload: Any) -> None:
    if not payload.confirm_send_evidence:
        raise BrandProfileSuggestionValidationError(
            "confirm_send_evidence must be true to send brand evidence to the "
            "default agent"
        )


def parse_brand_profile_draft(raw: str) -> BrandProfileDraft:
    """Parse and normalize the agent's strict JSON draft."""
    try:
        parsed = BrandProfileDraft.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise BrandProfileSuggestionOutputError(
            f"Unparseable agent output: {exc}"
        ) from exc

    try:
        draft = BrandProfileDraft(
            description=parsed.description.strip(),
            positioning=parsed.positioning.strip(),
            products_services=clean_profile_products(parsed.products_services),
            target_audience=parsed.target_audience.strip(),
        )
    except ValidationError as exc:
        raise BrandProfileSuggestionOutputError(
            f"Unparseable normalized agent output: {exc}"
        ) from exc
    if not any(draft.model_dump().values()):
        raise BrandProfileSuggestionOutputError(
            "Agent output contained no usable profile fields"
        )
    return draft


def build_brand_profile_suggestion_message(project: Any) -> str:
    return (
        f"{build_brand_knowledge_context(project)}\n"
        "Draft the four requested profile fields for human review."
    )


def brand_profile_suggestion_to_response(
    suggestion: BrandProfileSuggestion,
) -> BrandProfileSuggestionResponse:
    return BrandProfileSuggestionResponse(
        id=suggestion.id,
        workspace_id=suggestion.workspace_id,
        project_id=suggestion.project_id,
        brand_id=suggestion.brand_id,
        draft=BrandProfileDraft.model_validate(suggestion.output),
        model_identity=dict(suggestion.model_identity),
        prompt_template_version=suggestion.prompt_template_version,
        created_at=suggestion.created_at,
    )


async def suggest_brand_profile(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    agent: DefaultAgentClient,
) -> BrandProfileSuggestion:
    """Call the default agent, then persist its immutable review artifact."""
    project = await get_project(
        session, workspace_id=workspace_id, project_id=project_id
    )
    if project.brand is None:
        raise BrandProfileNotFoundError("Project brand not found")
    input_snapshot = build_brand_knowledge_data(project)
    user_message = build_brand_profile_suggestion_message(project)

    # Do not hold a database transaction open during provider I/O.
    await session.rollback()
    raw = await agent.complete_json(
        system=BRAND_PROFILE_SUGGESTION_SYSTEM_PROMPT,
        user=user_message,
    )
    draft = parse_brand_profile_draft(raw)

    # Re-authorize after the network boundary. The project may have been
    # deleted or moved out of scope while the provider call was in flight.
    project = await get_project(
        session, workspace_id=workspace_id, project_id=project_id
    )
    if project.brand is None:
        raise BrandProfileNotFoundError("Project brand not found")
    suggestion = BrandProfileSuggestion(
        workspace_id=workspace_id,
        project_id=project_id,
        brand_id=project.brand.id,
        model_identity={
            "transport_host": agent.base_url_host,
            "transport_model": agent.model,
        },
        prompt_template_version=BRAND_PROFILE_SUGGESTER_VERSION,
        input_context_snapshot=input_snapshot,
        output=draft.model_dump(),
    )
    session.add(suggestion)
    await session.commit()
    await session.refresh(suggestion)
    return suggestion


async def accept_brand_profile_suggestion(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    payload: Any,
) -> BrandProfileAcceptResponse:
    """Accept selected draft fields while preserving all manual authority."""
    project = await get_project(
        session, workspace_id=workspace_id, project_id=project_id
    )
    if project.brand is None:
        raise BrandProfileNotFoundError("Project brand not found")

    suggestion = (
        await session.execute(
            select(BrandProfileSuggestion).where(
                BrandProfileSuggestion.id == suggestion_id,
                BrandProfileSuggestion.workspace_id == workspace_id,
                BrandProfileSuggestion.project_id == project_id,
                BrandProfileSuggestion.brand_id == project.brand.id,
            )
        )
    ).scalar_one_or_none()
    if suggestion is None:
        raise BrandProfileSuggestionNotFoundError("Brand profile suggestion not found")

    profile = (
        await session.execute(
            select(BrandProfile)
            .where(
                BrandProfile.workspace_id == workspace_id,
                BrandProfile.project_id == project_id,
                BrandProfile.brand_id == project.brand.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if profile is None:
        raise BrandProfileNotFoundError("Brand profile not found")

    draft = BrandProfileDraft.model_validate(suggestion.output)
    sources = dict(profile.sources or {})
    artifact_ids = dict(profile.source_artifact_ids or {})
    manual_data = payload.manual_overrides.model_dump(
        exclude_unset=True, exclude_none=True
    )

    # Manual overrides are applied first and always win within this request.
    for field, value in manual_data.items():
        if field == "products_services":
            value = clean_profile_products(value)
        else:
            value = value.strip()
        setattr(profile, field, value)
        sources[field] = BRAND_PROFILE_SOURCE_MANUAL
        artifact_ids.pop(field, None)

    accepted: list[str] = []
    skipped_manual: list[str] = []
    requested_fields = list(dict.fromkeys(payload.accepted_fields))
    for field in requested_fields:
        if field not in BRAND_PROFILE_FIELDS:
            raise BrandProfileSuggestionValidationError(
                f"Unknown brand profile field: {field}"
            )
        if field in manual_data:
            continue
        value = getattr(draft, field)
        if not value:
            raise BrandProfileSuggestionValidationError(
                f"Suggestion has no usable value for accepted field: {field}"
            )
        if sources.get(field) == BRAND_PROFILE_SOURCE_MANUAL:
            skipped_manual.append(field)
            continue
        setattr(profile, field, value)
        sources[field] = BRAND_PROFILE_SOURCE_AI_SUGGESTED
        artifact_ids[field] = str(suggestion.id)
        accepted.append(field)

    profile.sources = sources
    profile.source_artifact_ids = artifact_ids
    await session.commit()
    await session.refresh(profile)
    return BrandProfileAcceptResponse(
        profile=brand_profile_to_response(profile),
        accepted_fields=accepted,
        skipped_manual_fields=skipped_manual,
    )
