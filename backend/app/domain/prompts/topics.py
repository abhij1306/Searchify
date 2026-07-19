# Topic service (workspace-scoped through the project, invariant 5).
#
# Topics are first-class per-project categories that group prompts (the topics
# rail on /prompts). Users create them manually; AI generation get-or-creates
# them by name. Deleting a topic detaches its prompts (FK SET NULL) rather
# than deleting them.
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.prompts import (
    PROMPT_STATUS_ACTIVE,
    PROMPT_STATUS_PROPOSED,
    TOPIC_ORIGIN_MANUAL,
)
from app.domain.prompts.locks import acquire_project_lock
from app.domain.prompts.schemas import TopicCreate, TopicUpdate
from app.models.project import Project
from app.models.prompt import Prompt, Topic


class TopicNotFoundError(LookupError):
    """Raised when a topic is missing or not in the caller's workspace."""


class DuplicateTopicError(ValueError):
    """Raised when a topic name already exists in the project."""


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
        raise TopicNotFoundError("Project not found")
    return project


async def _get_topic(
    session: AsyncSession, *, workspace_id: uuid.UUID, topic_id: uuid.UUID
) -> Topic:
    result = await session.execute(
        select(Topic)
        .join(Project, Project.id == Topic.project_id)
        .where(Topic.id == topic_id, Project.workspace_id == workspace_id)
    )
    topic = result.scalar_one_or_none()
    if topic is None:
        raise TopicNotFoundError("Topic not found")
    return topic


async def topic_status_counts(
    session: AsyncSession, *, project_id: uuid.UUID
) -> dict[uuid.UUID, dict[str, int]]:
    """Per-topic active/proposed prompt counts for the topics rail."""
    result = await session.execute(
        select(Prompt.topic_id, Prompt.status, func.count(Prompt.id))
        .join(Topic, Topic.id == Prompt.topic_id)
        .where(Topic.project_id == project_id)
        .group_by(Prompt.topic_id, Prompt.status)
    )
    counts: dict[uuid.UUID, dict[str, int]] = {}
    for topic_id, status, count in result.all():
        bucket = counts.setdefault(
            topic_id, {PROMPT_STATUS_ACTIVE: 0, PROMPT_STATUS_PROPOSED: 0}
        )
        if status in bucket:
            bucket[status] = count
    return counts


async def list_topics(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> list[Topic]:
    await _project_in_workspace(
        session, workspace_id=workspace_id, project_id=project_id
    )
    result = await session.execute(
        select(Topic).where(Topic.project_id == project_id).order_by(Topic.name.asc())
    )
    return list(result.scalars().all())


async def create_topic(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: TopicCreate,
) -> Topic:
    await _project_in_workspace(
        session, workspace_id=workspace_id, project_id=project_id
    )
    topic = Topic(
        project_id=project_id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        origin=TOPIC_ORIGIN_MANUAL,
    )
    session.add(topic)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateTopicError(
            "A topic with this name already exists in the project"
        ) from exc
    await session.refresh(topic)
    return topic


async def update_topic(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    topic_id: uuid.UUID,
    payload: TopicUpdate,
) -> Topic:
    topic = await _get_topic(session, workspace_id=workspace_id, topic_id=topic_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("name") is not None:
        topic.name = data["name"].strip()
    if data.get("description") is not None:
        topic.description = data["description"].strip()
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateTopicError(
            "A topic with this name already exists in the project"
        ) from exc
    await session.refresh(topic)
    return topic


async def delete_topic(
    session: AsyncSession, *, workspace_id: uuid.UUID, topic_id: uuid.UUID
) -> None:
    topic = await _get_topic(session, workspace_id=workspace_id, topic_id=topic_id)
    # Serialize against a concurrent generation for this project (which takes
    # the project lock before touching topics), so a topic can't be deleted
    # between generation's topic re-resolution and its inserts.
    await acquire_project_lock(session, topic.project_id)
    await session.delete(topic)
    await session.commit()
