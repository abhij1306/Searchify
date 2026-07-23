"""referral_retention_sweep executor (A6): hard-delete expired referral data.

Proves the workspace-scoped sweep deletes every ReferralEvent past
``REFERRAL_RETENTION_DAYS`` together with its ReferralClassification (FK
order) in bounded committed batches, keeps young rows untouched, stays
idempotent on re-run, honors cooperative cancel at the batch boundary, and
that the worker's default dispatch table routes the kind to the real
executor. Requires a real Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.analytics import (
    ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP,
    REFERRAL_RETENTION_DAYS,
)
from app.core.config.task_queue import TASK_STATUS_CANCELLED, TASK_STATUS_SUCCEEDED
from app.domain.analytics import tasks as analytics_tasks
from app.domain.analytics.enqueue import enqueue_referral_retention_sweep
from app.domain.analytics.tasks import (
    TaskCancelledError,
    run_referral_retention_sweep,
)
from app.models.analytics import (
    AnalyticsTask,
    ReferralClassification,
    ReferralEvent,
)
from app.workers.analytics_worker import AnalyticsWorker
from tests.component.analytics_helpers import (
    seed_ga4_import,
    seed_referral_classification,
    seed_referral_event,
    seed_workspace_project,
)

_NOW = datetime.now(UTC)
_EXPIRED = _NOW - timedelta(days=REFERRAL_RETENTION_DAYS + 30)
_YOUNG = _NOW - timedelta(days=10)


def _sweep_task(
    workspace_id: uuid.UUID, sweep_key: str = "2026-07-23"
) -> AnalyticsTask:
    """Fabricate the claimed queue row the executor receives (not persisted)."""
    return AnalyticsTask(
        workspace_id=workspace_id,
        project_id=None,  # the sweep is workspace-scoped
        task_kind=ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP,
        payload={"sweep_key": sweep_key},
        idempotency_key=uuid.uuid4().hex,
    )


async def _counts(session: AsyncSession) -> tuple[int, int]:
    events = await session.scalar(select(func.count(ReferralEvent.id)))
    classifications = await session.scalar(
        select(func.count(ReferralClassification.id))
    )
    return events or 0, classifications or 0


async def _seed_old_and_young(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """Three expired events (two classified) + two young (one classified)."""
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
    async with session_factory() as session:
        seed = await seed_ga4_import(
            session, workspace_id=workspace_id, project_id=project_id
        )
        old_classified_a = await seed_referral_event(
            session, seed=seed, occurred_at=_EXPIRED, referrer_host="chatgpt.com"
        )
        await seed_referral_classification(
            session, event=old_classified_a, is_ai_referral=True, ai_source="chatgpt"
        )
        old_classified_b = await seed_referral_event(
            session, seed=seed, occurred_at=_EXPIRED + timedelta(days=1)
        )
        await seed_referral_classification(session, event=old_classified_b)
        old_unclassified = await seed_referral_event(
            session, seed=seed, occurred_at=_EXPIRED + timedelta(days=2)
        )
        young_classified = await seed_referral_event(
            session, seed=seed, occurred_at=_YOUNG, referrer_host="perplexity.ai"
        )
        await seed_referral_classification(
            session,
            event=young_classified,
            is_ai_referral=True,
            ai_source="perplexity",
        )
        young_unclassified = await seed_referral_event(
            session, seed=seed, occurred_at=_YOUNG + timedelta(days=1)
        )
        await session.commit()
        ids = {
            "old_a": old_classified_a.id,
            "old_b": old_classified_b.id,
            "old_c": old_unclassified.id,
            "young_a": young_classified.id,
            "young_b": young_unclassified.id,
        }
    return workspace_id, ids


@pytest.mark.asyncio
async def test_sweep_deletes_expired_rows_in_batches_keeps_young(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, ids = await _seed_old_and_young(session_factory)
    # Force multiple batches (3 expired rows, 2 per batch).
    monkeypatch.setattr(analytics_tasks, "_RETENTION_DELETE_BATCH_SIZE", 2)

    await run_referral_retention_sweep(session_factory, _sweep_task(workspace_id))

    async with session_factory() as session:
        remaining_events = set(
            (await session.scalars(select(ReferralEvent.id))).all()
        )
        # Every expired row (classified or not) is gone; young rows stay.
        assert remaining_events == {ids["young_a"], ids["young_b"]}
        # Classifications were deleted WITH their events; the young row's
        # classification survives.
        remaining_classifications = list(
            (
                await session.scalars(
                    select(ReferralClassification.referral_event_id)
                )
            ).all()
        )
        assert remaining_classifications == [ids["young_a"]]

    # Idempotent re-run: nothing left past the horizon.
    await run_referral_retention_sweep(session_factory, _sweep_task(workspace_id))
    async with session_factory() as session:
        assert await _counts(session) == (2, 1)


@pytest.mark.asyncio
async def test_sweep_honors_cooperative_cancel_at_batch_boundary(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, _ids = await _seed_old_and_young(session_factory)
    async with session_factory() as session:
        task_id = await enqueue_referral_retention_sweep(
            session, workspace_id=workspace_id, sweep_key="2026-07-23"
        )
        await session.commit()
    assert task_id is not None
    async with session_factory() as session:
        task = await session.get(AnalyticsTask, task_id)
    assert task is not None

    monkeypatch.setattr(analytics_tasks, "_RETENTION_DELETE_BATCH_SIZE", 1)
    real_check = analytics_tasks._raise_if_task_terminal
    checks = 0

    async def _cancel_on_second_check(
        factory: async_sessionmaker[AsyncSession], row_id: uuid.UUID
    ) -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            async with factory() as session:
                row = await session.get(AnalyticsTask, row_id)
                assert row is not None
                row.status = TASK_STATUS_CANCELLED
                await session.commit()
        await real_check(factory, row_id)

    monkeypatch.setattr(
        analytics_tasks, "_raise_if_task_terminal", _cancel_on_second_check
    )

    with pytest.raises(TaskCancelledError):
        await run_referral_retention_sweep(session_factory, task)

    async with session_factory() as session:
        # Exactly one batch committed before the stop: one expired event
        # (and its classification) deleted, two expired events remain.
        assert await _counts(session) == (4, 2)


@pytest.mark.asyncio
async def test_sweep_missing_sweep_key_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, _project_id = await seed_workspace_project(session)
    task = _sweep_task(workspace_id)
    task.payload = {}
    with pytest.raises(ValueError, match="missing sweep_key"):
        await run_referral_retention_sweep(session_factory, task)


@pytest.mark.asyncio
async def test_worker_dispatch_runs_registered_retention_executor(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The worker's DEFAULT dispatch table routes referral_retention_sweep."""
    workspace_id, _ids = await _seed_old_and_young(session_factory)
    async with session_factory() as session:
        task_id = await enqueue_referral_retention_sweep(
            session, workspace_id=workspace_id, sweep_key="2026-07-23"
        )
        await session.commit()
    assert task_id is not None

    worker = AnalyticsWorker(session_factory=session_factory, owner="analytics-test")
    assert await worker.run_until_idle() == 1

    async with session_factory() as session:
        row = await session.get(AnalyticsTask, task_id)
        assert row is not None
        assert row.status == TASK_STATUS_SUCCEEDED
        assert row.attempt_count == 1
        assert await _counts(session) == (2, 1)
