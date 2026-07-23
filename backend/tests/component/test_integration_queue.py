"""Component tests for the integration sync-run queue mechanics (I6).

Proves the ONE generic ``PostgresTaskQueue`` — parameterized by
``INTEGRATION_QUEUE_SPEC`` — enforces the shared ``FOR UPDATE SKIP LOCKED``
claim/lease/heartbeat/sweeper semantics on ``IntegrationSyncRun`` rows:
deterministic claim order (priority desc -> available_at asc ->
randomized_position asc), no double-claim between owners, heartbeat lease
extension, and the sweeper returning expired leases to ``retry_wait`` (or
``failed`` once the attempt budget is spent). Requires a real Postgres.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import update

from app.core.config.integrations import (
    INTEGRATION_PROVIDER_GSC,
    INTEGRATION_QUEUE_SPEC,
    INTEGRATION_TRANSPORT_GOOGLE,
    integration_settings,
)
from app.core.config.task_queue import (
    ERROR_MAX_ATTEMPTS,
    TASK_STATUS_FAILED,
    TASK_STATUS_LEASED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RETRY_WAIT,
    TASK_STATUS_RUNNING,
)
from app.models.integrations import (
    IntegrationConnection,
    IntegrationOAuthGrant,
    IntegrationSyncRun,
)
from app.models.workspace import Workspace
from app.orchestration.postgres_task_queue import PostgresTaskQueue

_WINDOW = (date(2026, 7, 20), date(2026, 7, 22))


async def _seed_connection(db_session) -> tuple[uuid.UUID, IntegrationConnection]:
    workspace = Workspace(name="Acme")
    db_session.add(workspace)
    await db_session.flush()
    grant = IntegrationOAuthGrant(
        workspace_id=workspace.id,
        transport=INTEGRATION_TRANSPORT_GOOGLE,
        status="connected",
    )
    db_session.add(grant)
    await db_session.flush()
    connection = IntegrationConnection(
        workspace_id=workspace.id,
        grant_id=grant.id,
        provider=INTEGRATION_PROVIDER_GSC,
        label="gsc",
        account_ref="https://example.com",
    )
    db_session.add(connection)
    await db_session.commit()
    return workspace.id, connection


def _run_row(
    connection: IntegrationConnection,
    *,
    priority: int = 0,
    randomized_position: int = 0,
    available_at: datetime | None = None,
    status: str = TASK_STATUS_QUEUED,
    window: tuple[date, date] = _WINDOW,
    resync_seq: int = 0,
) -> IntegrationSyncRun:
    return IntegrationSyncRun(
        connection_id=connection.id,
        workspace_id=connection.workspace_id,
        sync_kind="scheduled",
        window_start=window[0],
        window_end=window[1],
        resync_seq=resync_seq,
        idempotency_key=uuid.uuid4().hex,
        status=status,
        priority=priority,
        randomized_position=randomized_position,
        available_at=available_at or datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_claim_order_priority_availability_position(
    session_factory, db_session
) -> None:
    _workspace_id, connection = await _seed_connection(db_session)
    now = datetime.now(UTC)
    low = _run_row(connection, priority=0, randomized_position=1)
    high_old = _run_row(
        connection,
        priority=5,
        randomized_position=9,
        available_at=now - timedelta(minutes=2),
        window=(date(2026, 7, 17), date(2026, 7, 19)),
    )
    high_new = _run_row(
        connection,
        priority=5,
        randomized_position=1,
        available_at=now,
        window=(date(2026, 7, 14), date(2026, 7, 16)),
    )
    db_session.add_all([low, high_old, high_new])
    await db_session.commit()

    queue = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)
    claimed = await queue.claim(owner="w1", limit=3)
    assert [row.id for row in claimed] == [high_old.id, high_new.id, low.id]
    assert all(
        row.status == TASK_STATUS_LEASED and row.lease_owner == "w1"
        for row in claimed
    )


@pytest.mark.asyncio
async def test_no_double_claim_between_owners(session_factory, db_session) -> None:
    _workspace_id, connection = await _seed_connection(db_session)
    first = _run_row(connection)
    second = _run_row(
        connection, window=(date(2026, 7, 17), date(2026, 7, 19))
    )
    db_session.add_all([first, second])
    await db_session.commit()

    queue_a = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)
    queue_b = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)
    claimed_a, claimed_b = await asyncio.gather(
        queue_a.claim(owner="worker-a", limit=1),
        queue_b.claim(owner="worker-b", limit=1),
    )
    assert len(claimed_a) == 1
    assert len(claimed_b) == 1
    assert claimed_a[0].id != claimed_b[0].id
    # A third claim finds nothing: both rows are leased out.
    queue_c = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)
    assert await queue_c.claim(owner="worker-c", limit=1) == []


@pytest.mark.asyncio
async def test_heartbeat_extends_lease(session_factory, db_session) -> None:
    _workspace_id, connection = await _seed_connection(db_session)
    db_session.add(_run_row(connection))
    await db_session.commit()

    queue = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)
    claimed = (await queue.claim(owner="w1", limit=1))[0]

    # Force the lease to look stale so the extension is observable, then
    # heartbeat: only an owned, leased/running row heartbeats.
    await db_session.execute(
        update(IntegrationSyncRun)
        .where(IntegrationSyncRun.id == claimed.id)
        .values(lease_expires_at=datetime.now(UTC) + timedelta(seconds=1))
    )
    await db_session.commit()
    assert await queue.heartbeat(task_id=claimed.id, owner="w1") is True
    row = await db_session.get(IntegrationSyncRun, claimed.id)
    ttl = integration_settings.lease_ttl_seconds
    assert row.lease_expires_at > datetime.now(UTC) + timedelta(seconds=ttl - 5)
    assert row.heartbeat_at is not None

    # Another owner cannot heartbeat the row.
    assert await queue.heartbeat(task_id=claimed.id, owner="w2") is False


@pytest.mark.asyncio
async def test_sweeper_reclaims_expired_lease(session_factory, db_session) -> None:
    _workspace_id, connection = await _seed_connection(db_session)
    expired = datetime.now(UTC) - timedelta(seconds=5)
    running = _run_row(connection, status=TASK_STATUS_RUNNING)
    running.lease_owner = "dead-worker"
    running.lease_expires_at = expired
    running.attempt_count = 1
    db_session.add(running)
    await db_session.commit()

    queue = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)
    assert await queue.release_expired() == 1

    await db_session.refresh(running)
    assert running.status == TASK_STATUS_RETRY_WAIT
    assert running.lease_owner is None
    assert running.lease_expires_at is None
    # Immediately claimable by a live worker.
    claimed = await queue.claim(owner="live-worker", limit=1)
    assert [row.id for row in claimed] == [running.id]


@pytest.mark.asyncio
async def test_sweeper_fails_after_max_attempts(session_factory, db_session) -> None:
    _workspace_id, connection = await _seed_connection(db_session)
    expired = datetime.now(UTC) - timedelta(seconds=5)
    exhausted = _run_row(connection, status=TASK_STATUS_LEASED)
    exhausted.lease_owner = "dead-worker"
    exhausted.lease_expires_at = expired
    exhausted.max_attempts = 1
    exhausted.attempt_count = 1
    db_session.add(exhausted)
    await db_session.commit()

    queue = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)
    assert await queue.release_expired() == 1

    await db_session.refresh(exhausted)
    assert exhausted.status == TASK_STATUS_FAILED
    assert exhausted.error_code == ERROR_MAX_ATTEMPTS
    assert exhausted.completed_at is not None
    assert await queue.claim(owner="live-worker", limit=1) == []


@pytest.mark.asyncio
async def test_sweeper_leaves_unexpired_leases(session_factory, db_session) -> None:
    _workspace_id, connection = await _seed_connection(db_session)
    live = _run_row(connection, status=TASK_STATUS_RUNNING)
    live.lease_owner = "live-worker"
    live.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    db_session.add(live)
    await db_session.commit()

    queue = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)
    assert await queue.release_expired() == 0
    await db_session.refresh(live)
    assert live.status == TASK_STATUS_RUNNING
    assert live.lease_owner == "live-worker"
