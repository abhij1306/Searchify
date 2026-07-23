"""Analytics queue spine (A3): claim-by-kind, no double-claim, sweeper.

Proves the ONE generic ``PostgresTaskQueue`` — parameterized by
``ANALYTICS_QUEUE_SPEC`` — enforces the same ``FOR UPDATE SKIP LOCKED``
claim/lease/sweeper semantics on ``AnalyticsTask`` rows (kind-restricted
claims included), that the C5 hook + per-kind enqueue helpers write
deterministically-keyed dedup-safe rows, and that the skeleton
``AnalyticsWorker`` dispatches registered executors while failing not-yet-
wired kinds loud. Requires a real Postgres.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.analytics import (
    ANALYTICS_QUEUE_SPEC,
    ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH,
    ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS,
    ANALYTICS_TASK_KIND_INGEST_REFERRALS,
    ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP,
    ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH,
    ANALYTICS_TASK_KINDS,
    ERROR_EXECUTOR_NOT_WIRED,
)
from app.core.config.integrations import (
    DATASET_GA4_REFERRER_DAILY,
    GRANT_STATUS_CONNECTED,
)
from app.core.config.task_queue import (
    ERROR_MAX_ATTEMPTS,
    TASK_CLAIMABLE_STATUSES,
    TASK_STATUS_FAILED,
    TASK_STATUS_LEASED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RETRY_WAIT,
    TASK_STATUS_SUCCEEDED,
)
from app.domain.analytics.enqueue import (
    enqueue_analytics_snapshot_refresh,
    enqueue_ingest_referrals,
    enqueue_post_sync_projections,
    enqueue_referral_retention_sweep,
)
from app.models.analytics import AnalyticsTask
from app.models.integrations import (
    IntegrationConnection,
    IntegrationImportArtifact,
    IntegrationOAuthGrant,
    IntegrationSyncRun,
)
from app.models.project import Project
from app.models.workspace import Workspace
from app.orchestration.postgres_task_queue import PostgresTaskQueue
from app.workers.analytics_worker import AnalyticsWorker

_WINDOW = (date(2026, 7, 20), date(2026, 7, 22))


async def _seed_workspace_project(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID]:
    workspace = Workspace(name="Analytics WS")
    session.add(workspace)
    await session.flush()
    project = Project(workspace_id=workspace.id, name="Analytics Project")
    session.add(project)
    await session.flush()
    await session.commit()
    return workspace.id, project.id


def _task(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID | None,
    *,
    task_kind: str = ANALYTICS_TASK_KIND_INGEST_REFERRALS,
    idempotency_key: str | None = None,
    payload: dict | None = None,
) -> AnalyticsTask:
    return AnalyticsTask(
        workspace_id=workspace_id,
        project_id=project_id,
        task_kind=task_kind,
        payload=payload if payload is not None else {},
        idempotency_key=idempotency_key or uuid.uuid4().hex,
    )


async def _seed_artifacts(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    artifact_count: int = 2,
    window: tuple[date, date] = _WINDOW,
) -> list[uuid.UUID]:
    """Seed one grant + connection + sync run + import artifacts."""
    grant = IntegrationOAuthGrant(
        workspace_id=workspace_id,
        transport="google_oauth",
        access_token_encrypted="fernet-access",
        refresh_token_encrypted="fernet-refresh",
        granted_scopes=["scope-a"],
        status=GRANT_STATUS_CONNECTED,
    )
    session.add(grant)
    await session.flush()
    connection = IntegrationConnection(
        workspace_id=workspace_id,
        grant_id=grant.id,
        provider="ga4",
        label="ga4 connection",
        account_ref="ga4-account-1",
    )
    session.add(connection)
    await session.flush()
    run = IntegrationSyncRun(
        workspace_id=workspace_id,
        connection_id=connection.id,
        window_start=window[0],
        window_end=window[1],
        idempotency_key=uuid.uuid4().hex,
    )
    session.add(run)
    await session.flush()
    artifact_ids = []
    for index in range(artifact_count):
        artifact = IntegrationImportArtifact(
            workspace_id=workspace_id,
            sync_run_id=run.id,
            connection_id=connection.id,
            provider="ga4",
            dataset=DATASET_GA4_REFERRER_DAILY,
            query_snapshot={"dimensions": ["fullReferrer", "date"]},
            payload_hash=f"{index}" * 64,
            row_count=1,
            payload={"rows": []},
        )
        session.add(artifact)
        await session.flush()
        artifact_ids.append(artifact.id)
    return artifact_ids


# --- Queue mechanics ----------------------------------------------------------


@pytest.mark.asyncio
async def test_analytics_queue_claims_by_task_kind(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
        seeded: dict[str, list[uuid.UUID]] = {kind: [] for kind in ANALYTICS_TASK_KINDS}
        for kind in sorted(ANALYTICS_TASK_KINDS):
            for _ in range(2):
                row = _task(workspace_id, project_id, task_kind=kind)
                session.add(row)
                await session.flush()
                seeded[kind].append(row.id)
        await session.commit()

    queue = PostgresTaskQueue(session_factory, ANALYTICS_QUEUE_SPEC)
    ingest = await queue.claim(
        owner="analytics-a", limit=10, kinds=[ANALYTICS_TASK_KIND_INGEST_REFERRALS]
    )
    assert {t.id for t in ingest} == set(
        seeded[ANALYTICS_TASK_KIND_INGEST_REFERRALS]
    )
    assert all(
        t.task_kind == ANALYTICS_TASK_KIND_INGEST_REFERRALS for t in ingest
    )
    assert all(t.status == TASK_STATUS_LEASED for t in ingest)

    chain = await queue.claim(
        owner="analytics-a",
        limit=10,
        kinds=[
            ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS,
            ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP,
        ],
    )
    assert {t.id for t in chain} == set(
        seeded[ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS]
        + seeded[ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP]
    )

    # An unrestricted claim picks up whatever kinds remain.
    rest = await queue.claim(owner="analytics-a", limit=10)
    assert {t.id for t in rest} == set(
        seeded[ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH]
        + seeded[ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH]
    )


@pytest.mark.asyncio
async def test_analytics_queue_concurrent_claims_never_double_claim(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
        ids = []
        for kind in sorted(ANALYTICS_TASK_KINDS):
            row = _task(workspace_id, project_id, task_kind=kind)
            session.add(row)
            await session.flush()
            ids.append(row.id)
        await session.commit()

    queue = PostgresTaskQueue(session_factory, ANALYTICS_QUEUE_SPEC)
    results = await asyncio.gather(
        queue.claim(owner="analytics-a", limit=10),
        queue.claim(owner="analytics-b", limit=10),
    )
    claimed_a = {t.id for t in results[0]}
    claimed_b = {t.id for t in results[1]}
    assert claimed_a.isdisjoint(claimed_b)
    assert claimed_a | claimed_b == set(ids)
    assert all(t.status == TASK_STATUS_LEASED for r in results for t in r)


@pytest.mark.asyncio
async def test_analytics_queue_sweeper_reclaims_expired_lease(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
        active = _task(workspace_id, project_id)
        session.add(active)
        exhausted = _task(
            workspace_id,
            project_id,
            task_kind=ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS,
        )
        session.add(exhausted)
        await session.commit()
        active_id, exhausted_id = active.id, exhausted.id

    queue = PostgresTaskQueue(session_factory, ANALYTICS_QUEUE_SPEC)
    claimed = await queue.claim(owner="analytics-w", limit=2)
    assert len(claimed) == 2

    # Force both leases to have already expired; one row has also spent its
    # attempt budget.
    async with session_factory() as session:
        await session.execute(
            update(AnalyticsTask)
            .where(AnalyticsTask.id.in_([active_id, exhausted_id]))
            .values(lease_expires_at=datetime.now(UTC) - timedelta(minutes=5))
        )
        await session.execute(
            update(AnalyticsTask)
            .where(AnalyticsTask.id == exhausted_id)
            .values(attempt_count=exhausted.max_attempts)
        )
        await session.commit()

    assert await queue.release_expired() == 2
    async with session_factory() as session:
        refreshed = {
            row.id: row
            for row in (
                await session.scalars(
                    select(AnalyticsTask).where(
                        AnalyticsTask.id.in_([active_id, exhausted_id])
                    )
                )
            ).all()
        }
    # Attempts remain: back to claimable with the lease cleared.
    assert refreshed[active_id].status in TASK_CLAIMABLE_STATUSES
    assert refreshed[active_id].lease_owner is None
    assert refreshed[active_id].lease_expires_at is None
    # Budget spent: terminal failure stamped by the sweeper.
    assert refreshed[exhausted_id].status == TASK_STATUS_FAILED
    assert refreshed[exhausted_id].error_code == ERROR_MAX_ATTEMPTS
    assert refreshed[exhausted_id].completed_at is not None


@pytest.mark.asyncio
async def test_analytics_queue_idempotency_key_unique(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
    async with session_factory() as session:
        session.add(
            _task(workspace_id, project_id, idempotency_key="shared-key")
        )
        await session.commit()
    async with session_factory() as session:
        session.add(
            _task(workspace_id, project_id, idempotency_key="shared-key")
        )
        with pytest.raises(IntegrityError):
            await session.commit()


# --- Enqueue helpers + the C5 hook ---------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_post_sync_projections_enqueues_chain_tasks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
    async with session_factory() as session:
        artifact_ids = await _seed_artifacts(session, workspace_id)
        await session.commit()

    async with session_factory() as session:
        enqueued = await enqueue_post_sync_projections(
            session, project_id=project_id, import_artifact_ids=artifact_ids
        )
        await session.commit()
    # One ingest_referrals per artifact + one traffic_snapshot_refresh for
    # the single distinct sync window.
    assert len(enqueued) == len(artifact_ids) + 1

    async with session_factory() as session:
        rows = list((await session.scalars(select(AnalyticsTask))).all())
    by_kind: dict[str, list[AnalyticsTask]] = {}
    for row in rows:
        by_kind.setdefault(row.task_kind, []).append(row)

    ingest_rows = by_kind[ANALYTICS_TASK_KIND_INGEST_REFERRALS]
    assert len(ingest_rows) == len(artifact_ids)
    assert {row.payload["import_artifact_id"] for row in ingest_rows} == {
        str(a) for a in artifact_ids
    }
    refresh_rows = by_kind[ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH]
    assert len(refresh_rows) == 1
    assert refresh_rows[0].payload == {
        "window_start": _WINDOW[0].isoformat(),
        "window_end": _WINDOW[1].isoformat(),
    }
    for row in rows:
        assert row.workspace_id == workspace_id
        assert row.project_id == project_id
        assert row.status == TASK_STATUS_QUEUED
        assert row.idempotency_key.startswith(f"analytics:{row.task_kind}:")

    # A repeated hook call is a dedup no-op (deterministic idempotency keys).
    async with session_factory() as session:
        again = await enqueue_post_sync_projections(
            session, project_id=project_id, import_artifact_ids=artifact_ids
        )
        await session.commit()
    assert again == []
    async with session_factory() as session:
        count = await session.scalar(select(func.count(AnalyticsTask.id)))
    assert count == len(artifact_ids) + 1


@pytest.mark.asyncio
async def test_enqueue_post_sync_projections_skips_foreign_artifacts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # An artifact from ANOTHER workspace must never be enqueued into this
    # project's chain (invariant 5).
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
    async with session_factory() as session:
        foreign_workspace = Workspace(name="Foreign WS")
        session.add(foreign_workspace)
        await session.flush()
        foreign_ids = await _seed_artifacts(
            session, foreign_workspace.id, artifact_count=1
        )
        own_ids = await _seed_artifacts(session, workspace_id, artifact_count=1)
        await session.commit()

    async with session_factory() as session:
        enqueued = await enqueue_post_sync_projections(
            session,
            project_id=project_id,
            import_artifact_ids=[*own_ids, *foreign_ids],
        )
        await session.commit()
    # Only the own-workspace artifact produced tasks (ingest + one refresh).
    assert len(enqueued) == 2
    async with session_factory() as session:
        rows = list((await session.scalars(select(AnalyticsTask))).all())
    assert all(row.workspace_id == workspace_id for row in rows)
    assert str(foreign_ids[0]) not in {
        row.payload.get("import_artifact_id") for row in rows
    }


@pytest.mark.asyncio
async def test_enqueue_helpers_have_deterministic_idempotency_keys(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
    artifact_id = uuid.uuid4()

    async with session_factory() as session:
        first = await enqueue_ingest_referrals(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            import_artifact_id=artifact_id,
        )
        duplicate = await enqueue_ingest_referrals(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            import_artifact_id=artifact_id,
        )
        sweep = await enqueue_referral_retention_sweep(
            session, workspace_id=workspace_id, sweep_key="2026-07-23"
        )
        await session.commit()
    assert first is not None
    assert duplicate is None  # same logical task: deduped, not duplicated
    assert sweep is not None

    async with session_factory() as session:
        rows = list((await session.scalars(select(AnalyticsTask))).all())
    assert len(rows) == 2
    sweep_row = next(
        row
        for row in rows
        if row.task_kind == ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP
    )
    # The retention sweep is workspace-scoped: no project.
    assert sweep_row.project_id is None
    assert sweep_row.payload == {"sweep_key": "2026-07-23"}


# --- Worker skeleton -------------------------------------------------------------


@pytest.mark.asyncio
async def test_analytics_worker_runs_registered_executor(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
    artifact_id = uuid.uuid4()
    async with session_factory() as session:
        task_id = await enqueue_ingest_referrals(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            import_artifact_id=artifact_id,
        )
        await session.commit()
    assert task_id is not None

    seen: list[tuple[str, dict]] = []

    async def _fake_executor(
        session_factory: async_sessionmaker[AsyncSession], task: AnalyticsTask
    ) -> None:
        seen.append((task.task_kind, dict(task.payload or {})))

    worker = AnalyticsWorker(
        session_factory=session_factory,
        owner="analytics-test",
        executors={ANALYTICS_TASK_KIND_INGEST_REFERRALS: _fake_executor},
    )
    assert await worker.run_until_idle() == 1

    assert seen == [
        (
            ANALYTICS_TASK_KIND_INGEST_REFERRALS,
            {"import_artifact_id": str(artifact_id)},
        )
    ]
    async with session_factory() as session:
        row = await session.get(AnalyticsTask, task_id)
    assert row is not None
    assert row.status == TASK_STATUS_SUCCEEDED
    assert row.attempt_count == 1
    assert row.completed_at is not None
    assert row.lease_owner is None


@pytest.mark.asyncio
async def test_analytics_worker_unwired_kind_fails_loud(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
    async with session_factory() as session:
        task_id = await enqueue_analytics_snapshot_refresh(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            window_start=date(2026, 7, 20),
            window_end=date(2026, 7, 22),
        )
        await session.commit()
    assert task_id is not None

    # A kind whose executor has not landed yet (analytics_snapshot_refresh
    # lands in A8) maps to the not-wired stub in the dispatch table.
    worker = AnalyticsWorker(session_factory=session_factory, owner="analytics-test")
    assert await worker.run_until_idle() == 1

    async with session_factory() as session:
        row = await session.get(AnalyticsTask, task_id)
    assert row is not None
    assert row.status == TASK_STATUS_FAILED
    assert row.error_code == ERROR_EXECUTOR_NOT_WIRED
    assert "no registered executor" in row.error_detail
    assert row.lease_owner is None


@pytest.mark.asyncio
async def test_analytics_worker_retryable_error_reschedules(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await _seed_workspace_project(session)
        row = _task(workspace_id, project_id)
        session.add(row)
        await session.commit()
        task_id = row.id

    async def _crashing_executor(
        session_factory: async_sessionmaker[AsyncSession], task: AnalyticsTask
    ) -> None:
        raise RuntimeError("transient projection failure")

    worker = AnalyticsWorker(
        session_factory=session_factory,
        owner="analytics-test",
        executors={ANALYTICS_TASK_KIND_INGEST_REFERRALS: _crashing_executor},
    )
    assert await worker.run_once() == 1

    async with session_factory() as session:
        row = await session.get(AnalyticsTask, task_id)
    assert row is not None
    assert row.status == TASK_STATUS_RETRY_WAIT
    assert row.attempt_count == 1
    assert row.available_at > datetime.now(UTC)
    assert row.lease_owner is None
