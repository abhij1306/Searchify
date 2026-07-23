"""Component tests for the sync-run enqueue service (I5).

Covers window computation (config default trailing window; explicit window
clamped to ``sync_backfill_max_days``; inverted/half windows rejected), the
deterministic idempotency key, and the atomic ``resync_seq`` allocation:
duplicate ACTIVE window rejected (partial-index IntegrityError →
``ActiveWindowConflictError``), completed window re-syncs with a bumped seq,
and concurrent allocators never pick the same value or break monotonicity.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config.integrations import (
    SYNC_KIND_BACKFILL,
    SYNC_KIND_ON_DEMAND,
    integration_settings,
)
from app.core.config.task_queue import (
    TASK_STATUS_QUEUED,
    TASK_STATUS_SUCCEEDED,
)
from app.domain.integrations.service import IntegrationConnectionNotFoundError
from app.domain.integrations.sync import (
    ActiveWindowConflictError,
    SyncWindowInvalidError,
    build_sync_idempotency_key,
    clamp_sync_window,
    default_sync_window,
    enqueue_sync_run,
    resolve_sync_window,
)
from app.models.integrations import (
    IntegrationConnection,
    IntegrationOAuthGrant,
    IntegrationSyncRun,
)
from app.models.workspace import Workspace

_WINDOW = (date(2026, 7, 1), date(2026, 7, 3))


async def _seed_connection(
    db_session, *, provider: str = "gsc"
) -> tuple[uuid.UUID, IntegrationConnection]:
    workspace = Workspace(name="Acme")
    db_session.add(workspace)
    await db_session.flush()
    grant = IntegrationOAuthGrant(
        workspace_id=workspace.id, transport="google_oauth", status="connected"
    )
    db_session.add(grant)
    await db_session.flush()
    connection = IntegrationConnection(
        workspace_id=workspace.id,
        grant_id=grant.id,
        provider=provider,
        label=f"{provider} label",
        account_ref=f"{provider}-account-ref",
    )
    db_session.add(connection)
    await db_session.commit()
    return workspace.id, connection


async def _complete(db_session, run_id: uuid.UUID) -> None:
    run = await db_session.get(IntegrationSyncRun, run_id)
    run.status = TASK_STATUS_SUCCEEDED
    run.completed_at = datetime.now(UTC)
    await db_session.commit()


async def _runs(db_session, connection_id: uuid.UUID) -> list[IntegrationSyncRun]:
    result = await db_session.execute(
        select(IntegrationSyncRun)
        .where(IntegrationSyncRun.connection_id == connection_id)
        .order_by(IntegrationSyncRun.resync_seq.asc())
    )
    return list(result.scalars())


@pytest.mark.asyncio
async def test_default_window_uses_config_trailing_days(db_session) -> None:
    workspace_id, connection = await _seed_connection(db_session)

    run = await enqueue_sync_run(
        db_session, workspace_id=workspace_id, connection_id=connection.id
    )

    expected_start, expected_end = default_sync_window()
    # Trailing ``sync_default_window_days`` complete days ending yesterday.
    assert expected_end == datetime.now(UTC).date() - timedelta(days=1)
    assert (expected_end - expected_start).days + 1 == (
        integration_settings.sync_default_window_days
    )
    assert (run.window_start, run.window_end) == (expected_start, expected_end)
    assert run.sync_kind == SYNC_KIND_ON_DEMAND
    assert run.status == TASK_STATUS_QUEUED
    assert run.resync_seq == 0
    assert run.max_attempts == integration_settings.sync_max_attempts
    assert run.idempotency_key == build_sync_idempotency_key(
        connection_id=connection.id,
        sync_kind=SYNC_KIND_ON_DEMAND,
        window_start=expected_start,
        window_end=expected_end,
        resync_seq=0,
    )
    assert len(run.idempotency_key) <= 160  # String(160) column


@pytest.mark.asyncio
async def test_explicit_window_clamped_to_backfill_max(db_session) -> None:
    workspace_id, connection = await _seed_connection(db_session)
    window_end = date(2026, 1, 1)
    window_start = window_end - timedelta(
        days=integration_settings.sync_backfill_max_days + 100
    )

    run = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection.id,
        window_start=window_start,
        window_end=window_end,
    )

    assert run.window_end == window_end  # end preserved; start pulled forward
    assert (run.window_end - run.window_start).days + 1 == (
        integration_settings.sync_backfill_max_days
    )


def test_window_helpers_pure() -> None:
    # Inverted range rejected; exact-max span untouched; over-max clamped.
    with pytest.raises(SyncWindowInvalidError):
        clamp_sync_window(date(2026, 7, 3), date(2026, 7, 1))
    max_span = integration_settings.sync_backfill_max_days
    end = date(2026, 7, 3)
    start = end - timedelta(days=max_span - 1)
    assert clamp_sync_window(start, end) == (start, end)
    # Half-specified windows rejected; both-absent resolves to the default.
    with pytest.raises(SyncWindowInvalidError):
        resolve_sync_window(date(2026, 7, 1), None)
    with pytest.raises(SyncWindowInvalidError):
        resolve_sync_window(None, date(2026, 7, 1))
    assert resolve_sync_window(None, None) == default_sync_window()
    # The key builder is deterministic.
    connection_id = uuid.uuid4()
    key_a = build_sync_idempotency_key(
        connection_id=connection_id,
        sync_kind=SYNC_KIND_ON_DEMAND,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
        resync_seq=2,
    )
    key_b = build_sync_idempotency_key(
        connection_id=connection_id,
        sync_kind=SYNC_KIND_ON_DEMAND,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
        resync_seq=2,
    )
    assert key_a == key_b


@pytest.mark.asyncio
async def test_duplicate_active_window_rejected(db_session) -> None:
    workspace_id, connection = await _seed_connection(db_session)
    connection_id = connection.id  # capture now: the conflict path rolls back
    first = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection_id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )
    first_id = first.id

    with pytest.raises(ActiveWindowConflictError):
        await enqueue_sync_run(
            db_session,
            workspace_id=workspace_id,
            connection_id=connection_id,
            window_start=_WINDOW[0],
            window_end=_WINDOW[1],
        )

    runs = await _runs(db_session, connection_id)
    assert [row.id for row in runs] == [first_id]
    assert runs[0].status == TASK_STATUS_QUEUED  # still occupies the slot


@pytest.mark.asyncio
async def test_completed_window_resyncs_with_bumped_seq(db_session) -> None:
    workspace_id, connection = await _seed_connection(db_session)
    run0 = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection.id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )
    await _complete(db_session, run0.id)
    run1 = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection.id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )
    await _complete(db_session, run1.id)
    run2 = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection.id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )

    assert (run0.resync_seq, run1.resync_seq, run2.resync_seq) == (0, 1, 2)
    # Every re-sync is a NEW run identity; prior rows are retained (inv. 3).
    assert len({run0.id, run1.id, run2.id}) == 3
    assert len({run0.idempotency_key, run1.idempotency_key, run2.idempotency_key}) == 3
    runs = await _runs(db_session, connection.id)
    assert [run.resync_seq for run in runs] == [0, 1, 2]
    assert [run.status for run in runs] == [
        TASK_STATUS_SUCCEEDED,
        TASK_STATUS_SUCCEEDED,
        TASK_STATUS_QUEUED,
    ]


@pytest.mark.asyncio
async def test_concurrent_allocators_get_distinct_monotonic_seqs(
    session_factory, db_session
) -> None:
    workspace_id, connection = await _seed_connection(db_session)

    async def _enqueue() -> IntegrationSyncRun:
        async with session_factory() as session:
            return await enqueue_sync_run(
                session,
                workspace_id=workspace_id,
                connection_id=connection.id,
                window_start=_WINDOW[0],
                window_end=_WINDOW[1],
            )

    async def _complete_fresh(run_id: uuid.UUID) -> None:
        async with session_factory() as session:
            await _complete(session, run_id)

    # Round 1: two racing enqueues — exactly one wins the active slot at
    # seq 0; the loser is rejected by the partial active-window index, never
    # allocated the same seq.
    outcomes = await asyncio.gather(_enqueue(), _enqueue(), return_exceptions=True)
    winner1 = next(o for o in outcomes if isinstance(o, IntegrationSyncRun))
    assert any(isinstance(o, ActiveWindowConflictError) for o in outcomes)
    assert winner1.resync_seq == 0

    await _complete_fresh(winner1.id)

    # Round 2: the next generation allocates seq 1 — distinct + monotonic.
    outcomes = await asyncio.gather(_enqueue(), _enqueue(), return_exceptions=True)
    winner2 = next(o for o in outcomes if isinstance(o, IntegrationSyncRun))
    assert any(isinstance(o, ActiveWindowConflictError) for o in outcomes)
    assert winner2.resync_seq == 1
    assert winner2.id != winner1.id
    assert winner2.resync_seq > winner1.resync_seq

    async with session_factory() as session:
        runs = await _runs(session, connection.id)
    assert [run.resync_seq for run in runs] == [0, 1]


@pytest.mark.asyncio
async def test_window_kind_groups_allocate_independently(db_session) -> None:
    """The window-group identity includes sync_kind (spec §3)."""
    workspace_id, connection = await _seed_connection(db_session)
    on_demand = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection.id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )
    backfill = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection.id,
        sync_kind=SYNC_KIND_BACKFILL,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )
    # Same window, different kind: a distinct active slot + its own seq 0.
    assert (on_demand.resync_seq, backfill.resync_seq) == (0, 0)
    assert on_demand.sync_kind != backfill.sync_kind


@pytest.mark.asyncio
async def test_cross_workspace_connection_rejected(db_session) -> None:
    _workspace_id, connection = await _seed_connection(db_session)
    with pytest.raises(IntegrationConnectionNotFoundError):
        await enqueue_sync_run(
            db_session, workspace_id=uuid.uuid4(), connection_id=connection.id
        )


@pytest.mark.asyncio
async def test_unknown_sync_kind_rejected(db_session) -> None:
    workspace_id, connection = await _seed_connection(db_session)
    with pytest.raises(ValueError, match="unknown integration sync kind"):
        await enqueue_sync_run(
            db_session,
            workspace_id=workspace_id,
            connection_id=connection.id,
            sync_kind="bogus",
        )
