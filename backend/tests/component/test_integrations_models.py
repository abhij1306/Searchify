"""Integration model constraints + queue wiring (I2).

Verifies the persistence contract the sync pipeline depends on: the
active-window partial unique index (in-flight dedup) vs the full
``(…, resync_seq)`` re-sync identity, one-active-owner property mappings,
metric-row identity with retained old revisions, same-workspace composite
FKs, the grant find-or-create uniqueness, OAuth-state jti uniqueness,
disconnect-safe events, and that ``INTEGRATION_QUEUE_SPEC`` claims
``IntegrationSyncRun`` rows through the shared generic queue without
double-claim. Requires a real Postgres (partial index semantics).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.integrations import (
    DATASET_GSC_PAGE_DAILY,
    GRANT_STATUS_CONNECTED,
    INTEGRATION_QUEUE_SPEC,
    MAPPING_STATUS_DISABLED,
    SYNC_KIND_ON_DEMAND,
    SYNC_KIND_SCHEDULED,
)
from app.core.config.task_queue import (
    TASK_STATUS_LEASED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_SUCCEEDED,
)
from app.models.integrations import (
    IntegrationConnection,
    IntegrationEvent,
    IntegrationImportArtifact,
    IntegrationMetricRow,
    IntegrationOAuthGrant,
    IntegrationOAuthState,
    IntegrationPropertyMapping,
    IntegrationSyncRun,
)
from app.models.project import Project
from app.models.user import User
from app.models.workspace import Workspace
from app.orchestration.postgres_task_queue import PostgresTaskQueue

_WINDOW = (date(2026, 7, 20), date(2026, 7, 22))


async def _seed_workspace(session: AsyncSession, name: str = "Integrations WS"):
    workspace = Workspace(name=name)
    session.add(workspace)
    await session.flush()
    return workspace


def _grant(workspace_id: uuid.UUID, transport: str = "google_oauth"):
    return IntegrationOAuthGrant(
        workspace_id=workspace_id,
        transport=transport,
        access_token_encrypted="fernet-access",
        refresh_token_encrypted="fernet-refresh",
        granted_scopes=["scope-a"],
        status=GRANT_STATUS_CONNECTED,
    )


def _connection(
    workspace_id: uuid.UUID, grant_id: uuid.UUID, provider: str = "gsc"
) -> IntegrationConnection:
    return IntegrationConnection(
        workspace_id=workspace_id,
        grant_id=grant_id,
        provider=provider,
        label=f"{provider} connection",
        account_ref=f"{provider}-account-1",
    )


def _run(
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    window: tuple[date, date] = _WINDOW,
    resync_seq: int = 0,
    sync_kind: str = SYNC_KIND_ON_DEMAND,
    status: str = TASK_STATUS_QUEUED,
    idempotency_key: str | None = None,
) -> IntegrationSyncRun:
    return IntegrationSyncRun(
        workspace_id=workspace_id,
        connection_id=connection_id,
        sync_kind=sync_kind,
        window_start=window[0],
        window_end=window[1],
        resync_seq=resync_seq,
        status=status,
        idempotency_key=idempotency_key or uuid.uuid4().hex,
    )


async def _seed_connection(
    session: AsyncSession, provider: str = "gsc"
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    workspace = await _seed_workspace(session)
    grant = _grant(workspace.id)
    session.add(grant)
    await session.flush()
    connection = _connection(workspace.id, grant.id, provider)
    session.add(connection)
    await session.flush()
    return workspace.id, grant.id, connection.id


@pytest.mark.asyncio
async def test_active_window_partial_index_dedupes_inflight_runs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ws_id, _, connection_id = await _seed_connection(session)
        session.add(_run(ws_id, connection_id, resync_seq=0))
        await session.commit()

    # A second ACTIVE run for the same (connection, kind, window) collides on
    # the partial unique index — even at a free resync_seq.
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_run(ws_id, connection_id, resync_seq=1))
            await session.commit()

    # A different kind or a different window is a different slot: allowed.
    async with session_factory() as session:
        session.add(
            _run(ws_id, connection_id, resync_seq=0, sync_kind=SYNC_KIND_SCHEDULED)
        )
        session.add(
            _run(
                ws_id,
                connection_id,
                window=(date(2026, 7, 17), date(2026, 7, 19)),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_completed_window_frees_slot_and_resync_bumps_seq(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ws_id, _, connection_id = await _seed_connection(session)
        first = _run(ws_id, connection_id, resync_seq=0)
        session.add(first)
        await session.flush()
        first.status = TASK_STATUS_SUCCEEDED
        first.completed_at = datetime.now(UTC)
        await session.commit()

    # The completed run still owns the full (…, resync_seq=0) identity.
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_run(ws_id, connection_id, resync_seq=0))
            await session.commit()

    # Re-syncing the completed window with a bumped resync_seq is allowed —
    # the first run is terminal, so the active-window index does not fire.
    async with session_factory() as session:
        session.add(_run(ws_id, connection_id, resync_seq=1))
        await session.commit()

    # But while that re-sync is in flight, the window is deduped again.
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_run(ws_id, connection_id, resync_seq=2))
            await session.commit()


@pytest.mark.asyncio
async def test_sync_run_idempotency_key_unique(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ws_id, _, connection_id = await _seed_connection(session)
        session.add(_run(ws_id, connection_id, idempotency_key="dup-key"))
        await session.commit()
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(
                _run(
                    ws_id,
                    connection_id,
                    window=(date(2026, 7, 17), date(2026, 7, 19)),
                    idempotency_key="dup-key",
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_grant_one_per_transport_per_workspace(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace = await _seed_workspace(session)
        session.add(_grant(workspace.id))
        await session.commit()
        ws_id = workspace.id
    # Second google_oauth grant in the same workspace: rejected (the
    # find-or-create contract keeps tokens stored once per transport).
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_grant(ws_id))
            await session.commit()
    # A different transport is a different grant: allowed.
    async with session_factory() as session:
        session.add(_grant(ws_id, transport="microsoft_oauth"))
        await session.commit()


@pytest.mark.asyncio
async def test_connection_one_per_provider_per_grant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ws_id, grant_id, _ = await _seed_connection(session)
        await session.commit()
    # One Google consent attaches exactly one gsc + one ga4 row.
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_connection(ws_id, grant_id, "gsc"))
            await session.commit()
    async with session_factory() as session:
        session.add(_connection(ws_id, grant_id, "ga4"))
        await session.commit()


@pytest.mark.asyncio
async def test_one_active_mapping_per_property_across_connections(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ws_id, grant_id, gsc_id = await _seed_connection(session)
        ga4 = _connection(ws_id, grant_id, "ga4")
        session.add(ga4)
        await session.flush()
        project = Project(workspace_id=ws_id, name="Mapped project")
        session.add(project)
        await session.flush()
        ga4_id = ga4.id
        project_id = project.id
        session.add(
            IntegrationPropertyMapping(
                workspace_id=ws_id,
                connection_id=gsc_id,
                provider="gsc",
                property_ref="sc-domain:example.com",
                project_id=project_id,
            )
        )
        await session.commit()

    def _mapping(connection_id: uuid.UUID, provider: str, status: str = "active"):
        return IntegrationPropertyMapping(
            workspace_id=ws_id,
            connection_id=connection_id,
            provider=provider,
            property_ref="sc-domain:example.com",
            project_id=project_id,
            status=status,
        )

    # A second ACTIVE owner for the same property: rejected by the partial
    # index (even on a different connection of the same provider).
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_mapping(gsc_id, "gsc"))
            await session.commit()
    # A disabled mapping coexists with the active owner.
    async with session_factory() as session:
        session.add(_mapping(gsc_id, "gsc", status=MAPPING_STATUS_DISABLED))
        await session.commit()
    # The same property_ref under another provider is a different property.
    async with session_factory() as session:
        session.add(_mapping(ga4_id, "ga4"))
        await session.commit()


@pytest.mark.asyncio
async def test_metric_row_identity_and_resync_retention(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ws_id, _, connection_id = await _seed_connection(session)
        project = Project(workspace_id=ws_id, name="Metric project")
        session.add(project)
        await session.flush()
        run = _run(ws_id, connection_id)
        session.add(run)
        await session.flush()
        artifact = IntegrationImportArtifact(
            workspace_id=ws_id,
            sync_run_id=run.id,
            connection_id=connection_id,
            provider="gsc",
            dataset=DATASET_GSC_PAGE_DAILY,
            query_snapshot={"dimensions": ["page", "date"]},
            payload_hash="a" * 64,
            row_count=1,
            payload={"rows": []},
        )
        session.add(artifact)
        await session.flush()
        project_id, artifact_id = project.id, artifact.id
        await session.commit()

    def _row(resync_seq: int, artifact_id: uuid.UUID = artifact_id):
        return IntegrationMetricRow(
            workspace_id=ws_id,
            project_id=project_id,
            property_ref="sc-domain:example.com",
            provider="gsc",
            dataset=DATASET_GSC_PAGE_DAILY,
            date=date(2026, 7, 21),
            dimension_key="https://example.com/page | 20260721",
            metrics={"clicks": 3, "impressions": 40},
            source_artifact_id=artifact_id,
            resync_seq=resync_seq,
        )

    async with session_factory() as session:
        session.add(_row(resync_seq=0))
        await session.commit()
    # Same identity at the same resync_seq: rejected.
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_row(resync_seq=0))
            await session.commit()
    # A re-sync writes a NEW row at a higher resync_seq; the old row stays.
    async with session_factory() as session:
        session.add(_row(resync_seq=1))
        await session.commit()
    async with session_factory() as session:
        rows = (
            await session.scalars(
                select(IntegrationMetricRow).where(
                    IntegrationMetricRow.project_id == project_id
                )
            )
        ).all()
    assert sorted(row.resync_seq for row in rows) == [0, 1]
    assert all(row.source_artifact_id == artifact_id for row in rows)
    assert all(row.importer_version for row in rows)


@pytest.mark.asyncio
async def test_same_workspace_composite_fks_reject_cross_workspace_refs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ws_id, grant_id, connection_id = await _seed_connection(session)
        other = await _seed_workspace(session, name="Other WS")
        other_id = other.id
        await session.commit()

    # A connection cannot point at a grant in another workspace.
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_connection(other_id, grant_id, "gsc"))
            await session.commit()
    # A sync run cannot point at a connection in another workspace.
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_run(other_id, connection_id))
            await session.commit()
    # A mapping cannot point at a connection in another workspace.
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            project = Project(workspace_id=other_id, name="Other project")
            session.add(project)
            await session.flush()
            session.add(
                IntegrationPropertyMapping(
                    workspace_id=other_id,
                    connection_id=connection_id,
                    provider="gsc",
                    property_ref="sc-domain:example.com",
                    project_id=project.id,
                )
            )
            await session.commit()
    # An artifact cannot point at a sync run in another workspace.
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            run = _run(ws_id, connection_id)
            session.add(run)
            await session.flush()
            session.add(
                IntegrationImportArtifact(
                    workspace_id=other_id,
                    sync_run_id=run.id,
                    connection_id=connection_id,
                    provider="gsc",
                    dataset=DATASET_GSC_PAGE_DAILY,
                    payload_hash="b" * 64,
                )
            )
            await session.commit()


@pytest.mark.asyncio
async def test_oauth_state_jti_unique(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace = await _seed_workspace(session)
        user = User(email=f"{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
        session.add(user)
        await session.flush()

        def _state(jti: str) -> IntegrationOAuthState:
            return IntegrationOAuthState(
                jti=jti,
                workspace_id=workspace.id,
                user_id=user.id,
                provider="gsc",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )

        session.add(_state("jti-1"))
        await session.commit()
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_state("jti-1"))
            await session.commit()


@pytest.mark.asyncio
async def test_event_survives_connection_delete_with_set_null(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ws_id, grant_id, connection_id = await _seed_connection(session)
        event = IntegrationEvent(
            workspace_id=ws_id,
            connection_id=connection_id,
            grant_id=grant_id,
            event_type="integration.connected",
            message="connected",
            payload={"provider": "gsc"},
        )
        session.add(event)
        await session.commit()
        event_id = event.id

    async with session_factory() as session:
        connection = await session.get(IntegrationConnection, connection_id)
        assert connection is not None
        await session.delete(connection)
        await session.commit()

    async with session_factory() as session:
        preserved = await session.get(IntegrationEvent, event_id)
    assert preserved is not None
    assert preserved.connection_id is None
    assert preserved.grant_id == grant_id
    assert preserved.event_type == "integration.connected"


@pytest.mark.asyncio
async def test_workspace_delete_cascades_graph(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ws_id, grant_id, connection_id = await _seed_connection(session)
        run = _run(ws_id, connection_id)
        session.add(run)
        await session.flush()
        session.add(
            IntegrationImportArtifact(
                workspace_id=ws_id,
                sync_run_id=run.id,
                connection_id=connection_id,
                provider="gsc",
                dataset=DATASET_GSC_PAGE_DAILY,
                payload_hash="c" * 64,
            )
        )
        await session.commit()

    async with session_factory() as session:
        workspace = await session.get(Workspace, ws_id)
        assert workspace is not None
        await session.delete(workspace)
        await session.commit()

    async with session_factory() as session:
        for model in (
            IntegrationOAuthGrant,
            IntegrationConnection,
            IntegrationSyncRun,
            IntegrationImportArtifact,
        ):
            remaining = (
                await session.scalars(select(model).where(model.workspace_id == ws_id))
            ).all()
            assert remaining == [], model.__tablename__


@pytest.mark.asyncio
async def test_integration_queue_claims_without_double_claim(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        ws_id, _, connection_id = await _seed_connection(session)
        ids = []
        for offset in range(6):
            # Distinct windows so the active-window index is not exercised.
            row = _run(
                ws_id,
                connection_id,
                window=(
                    date(2026, 7, 1) + timedelta(days=offset * 3),
                    date(2026, 7, 2) + timedelta(days=offset * 3),
                ),
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)
        await session.commit()

    queue = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)
    results = await asyncio.gather(
        queue.claim(owner="integration-a", limit=6),
        queue.claim(owner="integration-b", limit=6),
    )
    claimed_a = {t.id for t in results[0]}
    claimed_b = {t.id for t in results[1]}
    assert claimed_a.isdisjoint(claimed_b)
    assert claimed_a | claimed_b == set(ids)
    assert all(t.status == TASK_STATUS_LEASED for r in results for t in r)

    first = results[0][0]
    assert await queue.mark_running(task_id=first.id, owner="integration-a")
    assert await queue.heartbeat(task_id=first.id, owner="integration-a")
    # A stranger's heartbeat never extends an owned lease.
    assert not await queue.heartbeat(task_id=first.id, owner="integration-b")
    assert await queue.succeed(task_id=first.id, owner="integration-a")
    async with session_factory() as session:
        refreshed = await session.get(IntegrationSyncRun, first.id)
    assert refreshed is not None
    assert refreshed.status == TASK_STATUS_SUCCEEDED
