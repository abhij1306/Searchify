"""PostgresTaskQueue: SKIP LOCKED no-double-claim + lease sweeper (invariant 8).

Requires a real Postgres (the queue relies on ``FOR UPDATE SKIP LOCKED``, which
SQLite cannot emulate). Two workers claiming concurrently must partition the
tasks with no overlap; the sweeper must reclaim an expired lease.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.audits import (
    AUDIT_QUEUE_SPEC,
    TASK_STATUS_FAILED,
    TASK_STATUS_LEASED,
    TASK_STATUS_RETRY_WAIT,
)
from app.domain.audits.planner import create_audit
from app.models.audit import AuditTask
from app.orchestration.postgres_task_queue import PostgresTaskQueue
from tests.component.audit_helpers import seed_audit_fixtures


async def _make_queued_audit(
    session_factory: async_sessionmaker[AsyncSession], *, prompts: int, reps: int
):
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=prompts)
    async with session_factory() as session:
        audit = await create_audit(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            engines=seed.engines,
            prompt_set_id=seed.prompt_set_id,
            repetitions=reps,
            random_seed="1",
        )
        return audit


@pytest.mark.asyncio
async def test_concurrent_claims_never_double_claim(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _make_queued_audit(session_factory, prompts=6, reps=2)  # 12
    queue = PostgresTaskQueue(session_factory, AUDIT_QUEUE_SPEC)

    # Two workers claim the whole queue concurrently. SKIP LOCKED must partition
    # the rows so no task is handed to both.
    results = await asyncio.gather(
        queue.claim(owner="worker-a", limit=12),
        queue.claim(owner="worker-b", limit=12),
    )
    claimed_a = {t.id for t in results[0]}
    claimed_b = {t.id for t in results[1]}

    assert claimed_a.isdisjoint(claimed_b)
    assert len(claimed_a) + len(claimed_b) == 12
    assert all(t.status == TASK_STATUS_LEASED for r in results for t in r)
    assert all(t.lease_owner in ("worker-a", "worker-b") for r in results for t in r)


@pytest.mark.asyncio
async def test_sweeper_reclaims_expired_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _make_queued_audit(session_factory, prompts=1, reps=1)  # 1
    queue = PostgresTaskQueue(session_factory, AUDIT_QUEUE_SPEC)

    claimed = await queue.claim(owner="dead-worker", limit=1)
    assert len(claimed) == 1
    task_id = claimed[0].id

    # Force the lease into the past to simulate a crashed worker.
    async with session_factory() as session:
        task = await session.get(AuditTask, task_id)
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    reclaimed = await queue.release_expired()
    assert reclaimed == 1

    async with session_factory() as session:
        task = await session.get(AuditTask, task_id)
        # Attempts remain -> returned to retry_wait, available immediately.
        assert task.status == TASK_STATUS_RETRY_WAIT
        assert task.lease_owner is None

    # Now it is claimable again by a live worker.
    reclaimed_tasks = await queue.claim(owner="new-worker", limit=1)
    assert len(reclaimed_tasks) == 1
    assert reclaimed_tasks[0].id == task_id


@pytest.mark.asyncio
async def test_sweeper_fails_task_when_attempts_exhausted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _make_queued_audit(session_factory, prompts=1, reps=1)
    queue = PostgresTaskQueue(session_factory, AUDIT_QUEUE_SPEC)

    claimed = await queue.claim(owner="dead-worker", limit=1)
    task_id = claimed[0].id

    async with session_factory() as session:
        task = await session.get(AuditTask, task_id)
        task.attempt_count = task.max_attempts  # budget spent
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    reclaimed = await queue.release_expired()
    assert reclaimed == 1

    async with session_factory() as session:
        task = await session.get(AuditTask, task_id)
        assert task.status == TASK_STATUS_FAILED
        assert task.completed_at is not None


@pytest.mark.asyncio
async def test_succeed_and_retry_lifecycle(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    audit = await _make_queued_audit(session_factory, prompts=2, reps=1)  # 2
    queue = PostgresTaskQueue(session_factory, AUDIT_QUEUE_SPEC)

    claimed = await queue.claim(owner="w1", limit=2)
    assert len(claimed) == 2
    first, second = claimed[0].id, claimed[1].id

    assert await queue.mark_running(task_id=first, owner="w1")
    assert await queue.succeed(task_id=first, owner="w1")
    # A retry reschedules into the future and releases the lease.
    assert await queue.retry(
        task_id=second,
        owner="w1",
        delay_seconds=60,
        error_code="rate_limit",
    )

    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(AuditTask).where(AuditTask.audit_id == audit.id)
            )
        ).all()
        by_id = {r.id: r for r in rows}
        assert by_id[first].status == "succeeded"
        assert by_id[second].status == TASK_STATUS_RETRY_WAIT
        assert by_id[second].available_at > datetime.now(UTC)

    # A different owner cannot finalize a task it does not hold.
    assert not await queue.succeed(task_id=second, owner="someone-else")
