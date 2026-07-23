"""Component tests for the integration dispatcher (I10).

Drives the real ``IntegrationDispatcher`` tick against a live Postgres
schema. Covers:

  - One ``scheduled`` run per ACTIVE connection per tick for the default
    trailing window (connections on non-connected grants are skipped).
  - Dedup: a second tick while a run is active enqueues NOTHING
    (``ActiveWindowConflictError`` -> skip); once the window's run is
    terminal the next tick re-syncs it with a bumped ``resync_seq``.
  - Late-data revision: with a distinct ``sync_late_data_revision_days``
    window, the re-sync window is enqueued alongside the trailing window
    and re-syncs at a bumped seq once terminal.
  - ``pending_revocation`` retries: Google remote-revoke success resolves
    the grant (tokens dropped); failure keeps tokens + status and appends
    ``revoke_failed``; a Microsoft grant (no revoke URL in config)
    resolves locally with NO remote call.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config.integrations import (
    EVENT_INTEGRATION_REVOKE_FAILED,
    EVENT_INTEGRATION_REVOKED,
    GRANT_STATUS_CONNECTED,
    GRANT_STATUS_NEEDS_REAUTH,
    GRANT_STATUS_PENDING_REVOCATION,
    GRANT_STATUS_REVOKED,
    INTEGRATION_PROVIDER_GSC,
    INTEGRATION_TRANSPORT_GOOGLE,
    INTEGRATION_TRANSPORT_MICROSOFT,
    SYNC_KIND_SCHEDULED,
    integration_settings,
)
from app.core.config.task_queue import (
    TASK_STATUS_QUEUED,
    TASK_STATUS_SUCCEEDED,
)
from app.core.security import encrypt_secret
from app.domain.integrations.sync import default_sync_window
from app.models.integrations import (
    IntegrationConnection,
    IntegrationEvent,
    IntegrationOAuthGrant,
    IntegrationSyncRun,
)
from app.models.workspace import Workspace
from app.workers.integration_dispatcher import IntegrationDispatcher


class _Seed:
    def __init__(self, *, workspace_id: uuid.UUID, connection_id: uuid.UUID) -> None:
        self.workspace_id = workspace_id
        self.connection_id = connection_id


async def _seed_connection(
    db_session,
    *,
    grant_status: str = GRANT_STATUS_CONNECTED,
    transport: str = INTEGRATION_TRANSPORT_GOOGLE,
    workspace_name: str = "Acme",
) -> tuple[_Seed, IntegrationOAuthGrant]:
    workspace = Workspace(name=workspace_name)
    db_session.add(workspace)
    await db_session.flush()
    grant = IntegrationOAuthGrant(
        workspace_id=workspace.id,
        transport=transport,
        access_token_encrypted=encrypt_secret("access-token-1"),
        refresh_token_encrypted=encrypt_secret("refresh-token-1"),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        status=grant_status,
    )
    db_session.add(grant)
    await db_session.flush()
    connection = IntegrationConnection(
        workspace_id=workspace.id,
        grant_id=grant.id,
        provider=INTEGRATION_PROVIDER_GSC,
        label="gsc connection",
        account_ref="https://example.com",
    )
    db_session.add(connection)
    await db_session.commit()
    return (
        _Seed(workspace_id=workspace.id, connection_id=connection.id),
        grant,
    )


def _dispatcher(session_factory, transport: httpx.AsyncBaseTransport | None = None):
    return IntegrationDispatcher(
        session_factory=session_factory,
        owner="dispatcher-test",
        transport=transport,
    )


async def _runs(db_session, connection_id: uuid.UUID) -> list[IntegrationSyncRun]:
    result = await db_session.scalars(
        select(IntegrationSyncRun)
        .where(IntegrationSyncRun.connection_id == connection_id)
        .order_by(
            IntegrationSyncRun.window_start.asc(),
            IntegrationSyncRun.resync_seq.asc(),
        )
    )
    return list(result)


async def _complete_all(db_session, connection_id: uuid.UUID) -> None:
    runs = await _runs(db_session, connection_id)
    for run in runs:
        run.status = TASK_STATUS_SUCCEEDED
        run.completed_at = datetime.now(UTC)
        run.lease_owner = None
    await db_session.commit()


@pytest.mark.asyncio
async def test_tick_enqueues_scheduled_run_per_active_connection(
    session_factory, db_session
) -> None:
    active, _grant = await _seed_connection(db_session)
    inactive, _grant2 = await _seed_connection(
        db_session, grant_status=GRANT_STATUS_NEEDS_REAUTH, workspace_name="Other"
    )

    ticked = await _dispatcher(session_factory).run_once()

    assert ticked == 1  # one run; the late window equals the trailing window
    active_runs = await _runs(db_session, active.connection_id)
    assert len(active_runs) == 1
    run = active_runs[0]
    assert run.sync_kind == SYNC_KIND_SCHEDULED
    assert run.status == TASK_STATUS_QUEUED
    assert run.resync_seq == 0
    assert (run.window_start, run.window_end) == default_sync_window()
    # A connection whose grant is not connected is never scheduled.
    assert await _runs(db_session, inactive.connection_id) == []


@pytest.mark.asyncio
async def test_tick_dedups_on_active_window_conflict(
    session_factory, db_session
) -> None:
    seed, _grant = await _seed_connection(db_session)
    dispatcher = _dispatcher(session_factory)

    assert await dispatcher.run_once() == 1
    # Second tick while the run is active: conflict -> skip, no duplicate.
    assert await dispatcher.run_once() == 0
    assert len(await _runs(db_session, seed.connection_id)) == 1

    # Once terminal, the same window re-syncs with a bumped resync_seq.
    await _complete_all(db_session, seed.connection_id)
    assert await dispatcher.run_once() == 1
    runs = await _runs(db_session, seed.connection_id)
    assert [run.resync_seq for run in runs] == [0, 1]
    assert {run.window_start for run in runs} == {default_sync_window()[0]}


@pytest.mark.asyncio
async def test_late_data_revision_window_resyncs_with_bumped_seq(
    session_factory, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A late-data window distinct from the default trailing window.
    monkeypatch.setattr(integration_settings, "sync_late_data_revision_days", 2)
    assert integration_settings.sync_default_window_days != 2
    seed, _grant = await _seed_connection(db_session)
    dispatcher = _dispatcher(session_factory)

    assert await dispatcher.run_once() == 2  # trailing + late-data windows
    runs = await _runs(db_session, seed.connection_id)
    windows = {(run.window_start, run.window_end): run.resync_seq for run in runs}
    trailing = default_sync_window()
    late_end = datetime.now(UTC).date() - timedelta(days=1)
    late = (late_end - timedelta(days=1), late_end)
    assert windows == {trailing: 0, late: 0}

    await _complete_all(db_session, seed.connection_id)
    assert await dispatcher.run_once() == 2
    runs = await _runs(db_session, seed.connection_id)
    by_window: dict[tuple[date, date], list[int]] = {}
    for run in runs:
        by_window.setdefault((run.window_start, run.window_end), []).append(
            run.resync_seq
        )
    assert by_window[trailing] == [0, 1]
    assert by_window[late] == [0, 1]


# --- pending_revocation retries ----------------------------------------------


@pytest.mark.asyncio
async def test_pending_revocation_remote_retry_success(
    session_factory, db_session
) -> None:
    seed, grant = await _seed_connection(
        db_session, grant_status=GRANT_STATUS_PENDING_REVOCATION
    )
    revoke_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        revoke_calls.append(request)
        return httpx.Response(200)

    resolved = await _dispatcher(
        session_factory, transport=httpx.MockTransport(handler)
    ).run_once()

    assert resolved == 1
    assert len(revoke_calls) == 1
    await db_session.refresh(grant)
    assert grant.status == GRANT_STATUS_REVOKED
    assert grant.access_token_encrypted == ""
    assert grant.refresh_token_encrypted == ""
    assert grant.token_expires_at is None
    events = list(
        (
            await db_session.scalars(
                select(IntegrationEvent).where(
                    IntegrationEvent.workspace_id == seed.workspace_id
                )
            )
        ).all()
    )
    assert [event.event_type for event in events] == [EVENT_INTEGRATION_REVOKED]


@pytest.mark.asyncio
async def test_pending_revocation_remote_retry_failure_retains_tokens(
    session_factory, db_session
) -> None:
    seed, grant = await _seed_connection(
        db_session, grant_status=GRANT_STATUS_PENDING_REVOCATION
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    resolved = await _dispatcher(
        session_factory, transport=httpx.MockTransport(handler)
    ).run_once()

    assert resolved == 0
    await db_session.refresh(grant)
    assert grant.status == GRANT_STATUS_PENDING_REVOCATION
    assert grant.access_token_encrypted != ""  # retained for the next retry
    assert grant.refresh_token_encrypted != ""
    events = list(
        (
            await db_session.scalars(
                select(IntegrationEvent).where(
                    IntegrationEvent.workspace_id == seed.workspace_id
                )
            )
        ).all()
    )
    assert [event.event_type for event in events] == [
        EVENT_INTEGRATION_REVOKE_FAILED
    ]


@pytest.mark.asyncio
async def test_pending_revocation_microsoft_resolves_locally(
    session_factory, db_session
) -> None:
    seed, grant = await _seed_connection(
        db_session,
        grant_status=GRANT_STATUS_PENDING_REVOCATION,
        transport=INTEGRATION_TRANSPORT_MICROSOFT,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Microsoft grants must never call a revoke URL")

    resolved = await _dispatcher(
        session_factory, transport=httpx.MockTransport(handler)
    ).run_once()

    assert resolved == 1
    await db_session.refresh(grant)
    assert grant.status == GRANT_STATUS_REVOKED
    assert grant.access_token_encrypted == ""
    assert grant.refresh_token_encrypted == ""
    events = list(
        (
            await db_session.scalars(
                select(IntegrationEvent).where(
                    IntegrationEvent.workspace_id == seed.workspace_id
                )
            )
        ).all()
    )
    assert [event.event_type for event in events] == [EVENT_INTEGRATION_REVOKED]
    assert events[0].payload["remote_revoke"] is False
