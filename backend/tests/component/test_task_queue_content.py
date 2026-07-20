"""Content queue rows through the generic PostgresTaskQueue.

Proves the ONE generic queue — parameterized by ``CONTENT_QUEUE_SPEC`` —
claims/heartbeats/retries/fails/cancels ``ContentGeneration`` rows with the
same ``FOR UPDATE SKIP LOCKED`` semantics, and that the composite
``(workspace_id, idempotency_key)`` constraint allows the same key across
workspaces while rejecting a duplicate within one. Requires a real Postgres.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.content import CONTENT_QUEUE_SPEC
from app.core.config.task_queue import (
    TASK_CLAIMABLE_STATUSES,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_LEASED,
    TASK_STATUS_RETRY_WAIT,
    TASK_STATUS_RUNNING,
)
from app.models.content import ContentGeneration
from app.models.project import Project
from app.models.workspace import Workspace
from app.orchestration.postgres_task_queue import PostgresTaskQueue


async def _seed_workspace_project(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID]:
    workspace = Workspace(name="Content WS")
    session.add(workspace)
    await session.flush()
    project = Project(workspace_id=workspace.id, name="Content Project")
    session.add(project)
    await session.flush()
    await session.commit()
    return workspace.id, project.id


def _generation(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    idempotency_key: str | None = None,
) -> ContentGeneration:
    return ContentGeneration(
        workspace_id=workspace_id,
        project_id=project_id,
        prompt="Write a landing page about testing.",
        output_type="website_page",
        website_context_status="disabled",
        request_fingerprint=uuid.uuid4().hex,
        idempotency_key=idempotency_key or str(uuid.uuid4()),
        provider="mistral",
        requested_model="mistral-small-latest",
    )


@pytest.mark.asyncio
async def test_content_queue_claims_without_double_claim(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
        ids = []
        for _ in range(8):
            row = _generation(workspace_id, project_id)
            session.add(row)
            await session.flush()
            ids.append(row.id)
        await session.commit()

    queue = PostgresTaskQueue(session_factory, CONTENT_QUEUE_SPEC)
    results = await asyncio.gather(
        queue.claim(owner="content-a", limit=8),
        queue.claim(owner="content-b", limit=8),
    )
    claimed_a = {t.id for t in results[0]}
    claimed_b = {t.id for t in results[1]}
    assert claimed_a.isdisjoint(claimed_b)
    assert claimed_a | claimed_b == set(ids)
    assert all(t.status == TASK_STATUS_LEASED for r in results for t in r)


@pytest.mark.asyncio
async def test_content_queue_lifecycle_heartbeat_retry_fail_cancel(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
        rows = [_generation(workspace_id, project_id) for _ in range(3)]
        session.add_all(rows)
        await session.commit()
        row_ids = [r.id for r in rows]

    queue = PostgresTaskQueue(session_factory, CONTENT_QUEUE_SPEC)
    claimed = await queue.claim(owner="content-w", limit=3)
    assert len(claimed) == 3

    first, second, third = claimed
    assert await queue.mark_running(task_id=first.id, owner="content-w")
    assert await queue.heartbeat(task_id=first.id, owner="content-w")
    # A stranger's heartbeat never extends an owned lease.
    assert not await queue.heartbeat(task_id=first.id, owner="intruder")

    assert await queue.retry(
        task_id=first.id,
        owner="content-w",
        delay_seconds=0.0,
        error_code="rate_limit",
    )
    assert await queue.fail(
        task_id=second.id, owner="content-w", error_code="server_error"
    )
    assert await queue.cancel(task_id=third.id)

    async with session_factory() as session:
        persisted = {
            row.id: row
            for row in (
                await session.scalars(
                    select(ContentGeneration).where(ContentGeneration.id.in_(row_ids))
                )
            ).all()
        }
    assert persisted[first.id].status == TASK_STATUS_RETRY_WAIT
    assert persisted[second.id].status == TASK_STATUS_FAILED
    assert persisted[third.id].status == TASK_STATUS_CANCELLED
    assert persisted[third.id].error_code == "cancelled"


@pytest.mark.asyncio
async def test_content_queue_sweeper_reclaims_expired_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
        row = _generation(workspace_id, project_id)
        session.add(row)
        await session.commit()
        row_id = row.id

    queue = PostgresTaskQueue(session_factory, CONTENT_QUEUE_SPEC)
    claimed = await queue.claim(owner="content-w", limit=1)
    assert len(claimed) == 1

    async with session_factory() as session:
        await session.execute(
            update(ContentGeneration)
            .where(ContentGeneration.id == row_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(minutes=5))
        )
        await session.commit()

    assert await queue.release_expired() == 1
    async with session_factory() as session:
        refreshed = await session.get(ContentGeneration, row_id)
    assert refreshed is not None
    assert refreshed.status in TASK_CLAIMABLE_STATUSES
    assert refreshed.lease_owner is None


@pytest.mark.asyncio
async def test_idempotency_key_unique_per_workspace_not_globally(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ws_a, proj_a = await _seed_workspace_project(session)
    async with session_factory() as session:
        ws_b, proj_b = await _seed_workspace_project(session)

    shared_key = "client-key-1"
    # Same key in two different workspaces: allowed.
    async with session_factory() as session:
        session.add(_generation(ws_a, proj_a, idempotency_key=shared_key))
        session.add(_generation(ws_b, proj_b, idempotency_key=shared_key))
        await session.commit()

    # Duplicate key within one workspace: rejected by the composite constraint.
    async with session_factory() as session:
        session.add(_generation(ws_a, proj_a, idempotency_key=shared_key))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_running_status_transition(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
        row = _generation(workspace_id, project_id)
        session.add(row)
        await session.commit()
        row_id = row.id

    queue = PostgresTaskQueue(session_factory, CONTENT_QUEUE_SPEC)
    await queue.claim(owner="content-w", limit=1)
    assert await queue.mark_running(task_id=row_id, owner="content-w")
    async with session_factory() as session:
        refreshed = await session.get(ContentGeneration, row_id)
    assert refreshed is not None and refreshed.status == TASK_STATUS_RUNNING
