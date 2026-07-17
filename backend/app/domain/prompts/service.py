# Prompt-set + prompt service (workspace-scoped through the project).
#
# A prompt set belongs to a project, which is workspace-scoped, so every query
# joins through ``Project`` and filters by ``workspace_id`` (invariant 5). The
# ``/generate`` endpoint is a roadmap stub (B-4) and lives in the router; the
# service owns manual create + CSV bulk import.
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config.projects import (
    PROMPT_ORIGIN_IMPORTED,
    PROMPT_ORIGIN_MANUAL,
)
from app.domain.projects.normalization import normalize_intent
from app.models.project import Project
from app.models.prompt import Prompt, PromptSet


class PromptSetNotFoundError(LookupError):
    """Raised when a prompt set is missing or not in the caller's workspace."""


class PromptNotFoundError(LookupError):
    """Raised when a prompt is missing or not in the caller's workspace."""


async def _project_in_workspace(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    result = await session.execute(
        select(Project).where(
            Project.id == project_id, Project.workspace_id == workspace_id
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise PromptSetNotFoundError("Project not found")
    return project


async def _get_prompt_set(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_set_id: uuid.UUID,
) -> PromptSet:
    """Resolve a prompt set (+ its prompts), enforcing workspace scope."""
    result = await session.execute(
        select(PromptSet)
        .join(Project, Project.id == PromptSet.project_id)
        .options(selectinload(PromptSet.prompts))
        .where(
            PromptSet.id == prompt_set_id,
            Project.workspace_id == workspace_id,
        )
    )
    prompt_set = result.scalars().unique().one_or_none()
    if prompt_set is None:
        raise PromptSetNotFoundError("Prompt set not found")
    return prompt_set


# --------------------------------------------------------------------------
# Prompt sets
# --------------------------------------------------------------------------
async def create_prompt_set(
    session: AsyncSession, *, workspace_id: uuid.UUID, payload: Any
) -> PromptSet:
    await _project_in_workspace(
        session, workspace_id=workspace_id, project_id=payload.project_id
    )
    prompt_set = PromptSet(
        project_id=payload.project_id,
        name=payload.name,
        description=payload.description,
    )
    session.add(prompt_set)
    await session.commit()
    return await _get_prompt_set(
        session, workspace_id=workspace_id, prompt_set_id=prompt_set.id
    )


async def list_prompt_sets(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
) -> list[PromptSet]:
    stmt = (
        select(PromptSet)
        .join(Project, Project.id == PromptSet.project_id)
        .options(selectinload(PromptSet.prompts))
        .where(Project.workspace_id == workspace_id)
        .order_by(PromptSet.created_at.desc())
    )
    if project_id is not None:
        stmt = stmt.where(PromptSet.project_id == project_id)
    result = await session.execute(stmt)
    return list(result.scalars().unique().all())


async def get_prompt_set(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_set_id: uuid.UUID,
) -> PromptSet:
    return await _get_prompt_set(
        session, workspace_id=workspace_id, prompt_set_id=prompt_set_id
    )


async def update_prompt_set(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_set_id: uuid.UUID,
    payload: Any,
) -> PromptSet:
    prompt_set = await _get_prompt_set(
        session, workspace_id=workspace_id, prompt_set_id=prompt_set_id
    )
    data = payload.model_dump(exclude_unset=True)
    if data.get("name") is not None:
        prompt_set.name = data["name"]
    if data.get("description") is not None:
        prompt_set.description = data["description"]
    await session.commit()
    return await _get_prompt_set(
        session, workspace_id=workspace_id, prompt_set_id=prompt_set_id
    )


async def delete_prompt_set(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_set_id: uuid.UUID,
) -> None:
    prompt_set = await _get_prompt_set(
        session, workspace_id=workspace_id, prompt_set_id=prompt_set_id
    )
    await session.delete(prompt_set)
    await session.commit()


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------
async def list_prompts(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_set_id: uuid.UUID,
) -> list[Prompt]:
    prompt_set = await _get_prompt_set(
        session, workspace_id=workspace_id, prompt_set_id=prompt_set_id
    )
    return list(prompt_set.prompts)


async def create_prompt(
    session: AsyncSession, *, workspace_id: uuid.UUID, payload: Any
) -> Prompt:
    await _get_prompt_set(
        session,
        workspace_id=workspace_id,
        prompt_set_id=payload.prompt_set_id,
    )
    prompt = Prompt(
        prompt_set_id=payload.prompt_set_id,
        text=payload.text.strip(),
        theme=payload.theme.strip(),
        intent=normalize_intent(payload.intent),
        branded=payload.branded,
        enabled=payload.enabled,
        origin=PROMPT_ORIGIN_MANUAL,
    )
    session.add(prompt)
    await session.commit()
    await session.refresh(prompt)
    return prompt


async def _get_prompt(
    session: AsyncSession, *, workspace_id: uuid.UUID, prompt_id: uuid.UUID
) -> Prompt:
    result = await session.execute(
        select(Prompt)
        .join(PromptSet, PromptSet.id == Prompt.prompt_set_id)
        .join(Project, Project.id == PromptSet.project_id)
        .where(
            Prompt.id == prompt_id,
            Project.workspace_id == workspace_id,
        )
    )
    prompt = result.scalar_one_or_none()
    if prompt is None:
        raise PromptNotFoundError("Prompt not found")
    return prompt


async def update_prompt(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_id: uuid.UUID,
    payload: Any,
) -> Prompt:
    prompt = await _get_prompt(session, workspace_id=workspace_id, prompt_id=prompt_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("text") is not None:
        prompt.text = data["text"].strip()
    if data.get("theme") is not None:
        prompt.theme = data["theme"].strip()
    if "intent" in data and data["intent"] is not None:
        prompt.intent = normalize_intent(data["intent"])
    if data.get("branded") is not None:
        prompt.branded = data["branded"]
    if data.get("enabled") is not None:
        prompt.enabled = data["enabled"]
    await session.commit()
    await session.refresh(prompt)
    return prompt


async def delete_prompt(
    session: AsyncSession, *, workspace_id: uuid.UUID, prompt_id: uuid.UUID
) -> None:
    prompt = await _get_prompt(session, workspace_id=workspace_id, prompt_id=prompt_id)
    await session.delete(prompt)
    await session.commit()


async def import_prompts(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    prompt_set_id: uuid.UUID,
    rows: list[Any],
) -> PromptSet:
    """MVP CSV bulk-create: persist already-parsed prompt rows as ``imported``.

    Rows with empty text are skipped; intents are casefolded + validated.
    Returns the refreshed prompt set (with all prompts) so the caller can
    project the whole set back — matching the frontend import contract.
    """
    await _get_prompt_set(
        session, workspace_id=workspace_id, prompt_set_id=prompt_set_id
    )
    for row in rows:
        text = str(row.text or "").strip()
        if not text:
            continue
        session.add(
            Prompt(
                prompt_set_id=prompt_set_id,
                text=text,
                theme=str(row.theme or "").strip(),
                intent=normalize_intent(row.intent),
                branded=bool(row.branded),
                enabled=bool(row.enabled),
                origin=PROMPT_ORIGIN_IMPORTED,
            )
        )
    await session.commit()
    return await _get_prompt_set(
        session, workspace_id=workspace_id, prompt_set_id=prompt_set_id
    )
