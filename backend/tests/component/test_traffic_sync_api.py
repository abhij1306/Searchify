"""Component tests for ``POST /projects/{id}/traffic/sync`` (A11).

The pass-through acceptance (traffic.md section 6, contract C3):
  - fan-out: ONE on-demand ``IntegrationSyncRun`` per ACTIVE mapped
    GSC/GA4 connection of the project (a connection with several mapped
    properties gets ONE run — runs are connection-scoped); disabled
    mappings, non-connected grants, Bing connections, and mappings of
    OTHER projects are all skipped;
  - no fetch here (invariant 7): the endpoint only enqueues via the
    integrations ``enqueue_sync_run`` service — the monkeypatched-fake
    test pins the exact call contract (workspace, connection, kind);
  - 202 with the contract-C3 BARE ARRAY — one
    ``{sync_run_id, connection_id, status}`` object per queued run
    (empty when nothing feeds the project), asserted as an exact key-set
    comparison (the frontend zod ``trafficSyncEnqueueResponseSchema``);
  - an active run for the same window upstream -> 409
    (``sync_active_window_conflict``);
  - invariant 5: cross-workspace project access is a 404.

Requires a real Postgres (``--test-db-url``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.integrations import (
    GRANT_STATUS_CONNECTED,
    GRANT_STATUS_NEEDS_REAUTH,
    INTEGRATION_PROVIDER_BING,
    INTEGRATION_PROVIDER_GA4,
    INTEGRATION_PROVIDER_GSC,
    INTEGRATION_TRANSPORT_GOOGLE,
    INTEGRATION_TRANSPORT_MICROSOFT,
    MAPPING_STATUS_DISABLED,
    SYNC_KIND_ON_DEMAND,
    integration_settings,
)
from app.models.integrations import (
    IntegrationConnection,
    IntegrationOAuthGrant,
    IntegrationPropertyMapping,
    IntegrationSyncRun,
)

_SYNC_ENQUEUE_KEYS = {"sync_run_id", "connection_id", "status"}


# ---------------------------------------------------------------------------
# API + seed helpers
# ---------------------------------------------------------------------------
async def _register(client: httpx.AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201


async def _create_project(client: httpx.AsyncClient) -> tuple[str, str]:
    resp = await client.post("/api/v1/projects", json={"name": "Traffic Project"})
    assert resp.status_code == 201
    body = resp.json()
    return body["id"], body["workspace_id"]


async def _seed_grant(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    transport: str = INTEGRATION_TRANSPORT_GOOGLE,
    status: str = GRANT_STATUS_CONNECTED,
) -> IntegrationOAuthGrant:
    grant = IntegrationOAuthGrant(
        workspace_id=workspace_id,
        transport=transport,
        access_token_encrypted="fernet-access",
        refresh_token_encrypted="fernet-refresh",
        granted_scopes=["scope"],
        status=status,
    )
    session.add(grant)
    await session.flush()
    return grant


async def _seed_connection(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    grant: IntegrationOAuthGrant,
    provider: str,
) -> IntegrationConnection:
    connection = IntegrationConnection(
        workspace_id=workspace_id,
        grant_id=grant.id,
        provider=provider,
        label=f"{provider} connection",
        account_ref=f"{provider}-account-1",
    )
    session.add(connection)
    await session.flush()
    return connection


async def _seed_mapping(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    connection: IntegrationConnection,
    property_ref: str,
    project_id: uuid.UUID,
    status: str | None = None,
) -> IntegrationPropertyMapping:
    mapping = IntegrationPropertyMapping(
        workspace_id=workspace_id,
        connection_id=connection.id,
        provider=connection.provider,
        property_ref=property_ref,
        project_id=project_id,
    )
    if status is not None:
        mapping.status = status
    session.add(mapping)
    await session.flush()
    return mapping


# ---------------------------------------------------------------------------
# Auth + workspace scoping (invariant 5)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sync_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.post(f"/api/v1/projects/{uuid.uuid4()}/traffic/sync")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sync_cross_workspace_project_is_404(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "traffic-sync-owner-a@example.com")
    project_id, _workspace_id = await _create_project(client)

    client.cookies.clear()
    await _register(client, "traffic-sync-owner-b@example.com")
    resp = await client.post(f"/api/v1/projects/{project_id}/traffic/sync")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Fan-out (real enqueue through the integrations service)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sync_fans_out_one_run_per_active_mapped_connection(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """GSC (with TWO mapped properties) + GA4 -> exactly two runs (C3)."""
    await _register(client, "traffic-sync-fanout@example.com")
    project_id, workspace_id = await _create_project(client)
    async with session_factory() as session:
        grant = await _seed_grant(session, workspace_id=uuid.UUID(workspace_id))
        gsc = await _seed_connection(
            session,
            workspace_id=uuid.UUID(workspace_id),
            grant=grant,
            provider=INTEGRATION_PROVIDER_GSC,
        )
        ga4 = await _seed_connection(
            session,
            workspace_id=uuid.UUID(workspace_id),
            grant=grant,
            provider=INTEGRATION_PROVIDER_GA4,
        )
        # Two ACTIVE mappings on the SAME GSC connection (different
        # properties): the run fan-out is per CONNECTION, not per mapping.
        await _seed_mapping(
            session,
            workspace_id=uuid.UUID(workspace_id),
            connection=gsc,
            property_ref="https://example.com/",
            project_id=uuid.UUID(project_id),
        )
        await _seed_mapping(
            session,
            workspace_id=uuid.UUID(workspace_id),
            connection=gsc,
            property_ref="https://blog.example.com/",
            project_id=uuid.UUID(project_id),
        )
        await _seed_mapping(
            session,
            workspace_id=uuid.UUID(workspace_id),
            connection=ga4,
            property_ref="properties/123456789",
            project_id=uuid.UUID(project_id),
        )
        await session.commit()

    resp = await client.post(f"/api/v1/projects/{project_id}/traffic/sync")
    assert resp.status_code == 202
    body = resp.json()
    # The contract-C3 bare array: strict shape, one entry per connection.
    assert isinstance(body, list)
    assert len(body) == 2
    for item in body:
        assert set(item) == _SYNC_ENQUEUE_KEYS
        assert item["status"] == "queued"
    assert {item["connection_id"] for item in body} == {str(gsc.id), str(ga4.id)}
    sync_ids = {item["sync_run_id"] for item in body}
    assert len(sync_ids) == 2

    # The runs are really queued: on-demand, workspace-scoped, default
    # trailing window (complete UTC days ending yesterday).
    async with session_factory() as session:
        runs = list((await session.scalars(select(IntegrationSyncRun))).all())
    assert len(runs) == 2
    expected_end = datetime.now(UTC).date() - timedelta(days=1)
    expected_start = expected_end - timedelta(
        days=integration_settings.sync_default_window_days - 1
    )
    for run in runs:
        assert str(run.id) in sync_ids
        assert run.workspace_id == uuid.UUID(workspace_id)
        assert run.sync_kind == SYNC_KIND_ON_DEMAND
        assert run.status == "queued"
        assert run.window_start == expected_start
        assert run.window_end == expected_end
    assert {run.connection_id for run in runs} == {gsc.id, ga4.id}


@pytest.mark.asyncio
async def test_sync_skips_ineligible_connections(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Only ACTIVE mappings of THIS project on GSC/GA4 connections of
    CONNECTED grants fan out.

    One workspace hosts at most one connection per provider on its one
    Google grant (``uq_integration_connection_grant_provider``), so the
    cases ride the two available Google connections: the GA4 connection
    maps to a DIFFERENT project (skipped), the GSC connection maps to this
    one (eligible); a Bing connection on a needs-reauth Microsoft grant is
    both a non-Traffic provider and on a non-connected grant (skipped).
    """
    await _register(client, "traffic-sync-eligible@example.com")
    project_id, workspace_id = await _create_project(client)
    other_project_resp = await client.post(
        "/api/v1/projects", json={"name": "Other Project"}
    )
    assert other_project_resp.status_code == 201
    other_project_id = other_project_resp.json()["id"]
    async with session_factory() as session:
        ws = uuid.UUID(workspace_id)
        pid = uuid.UUID(project_id)
        grant = await _seed_grant(session, workspace_id=ws)
        # Eligible: one ACTIVE mapped GSC connection on the connected grant.
        gsc = await _seed_connection(
            session, workspace_id=ws, grant=grant, provider=INTEGRATION_PROVIDER_GSC
        )
        await _seed_mapping(
            session,
            workspace_id=ws,
            connection=gsc,
            property_ref="https://example.com/",
            project_id=pid,
        )
        # Skipped: an ACTIVE mapping for a DIFFERENT project.
        ga4 = await _seed_connection(
            session, workspace_id=ws, grant=grant, provider=INTEGRATION_PROVIDER_GA4
        )
        await _seed_mapping(
            session,
            workspace_id=ws,
            connection=ga4,
            property_ref="properties/123456789",
            project_id=uuid.UUID(other_project_id),
        )
        # Skipped: a non-Traffic provider on a grant that needs reauth.
        stale_grant = await _seed_grant(
            session,
            workspace_id=ws,
            transport=INTEGRATION_TRANSPORT_MICROSOFT,
            status=GRANT_STATUS_NEEDS_REAUTH,
        )
        stale = await _seed_connection(
            session,
            workspace_id=ws,
            grant=stale_grant,
            provider=INTEGRATION_PROVIDER_BING,
        )
        await _seed_mapping(
            session,
            workspace_id=ws,
            connection=stale,
            property_ref="https://example.com/",
            project_id=pid,
        )
        await session.commit()

    resp = await client.post(f"/api/v1/projects/{project_id}/traffic/sync")
    assert resp.status_code == 202
    body = resp.json()
    assert len(body) == 1
    assert set(body[0]) == _SYNC_ENQUEUE_KEYS
    assert body[0]["connection_id"] == str(gsc.id)
    assert body[0]["status"] == "queued"


@pytest.mark.asyncio
async def test_sync_no_active_mapped_connections_returns_empty_array(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """202 with the empty C3 array when nothing feeds the project — no
    mappings at all, or only a DISABLED mapping."""
    await _register(client, "traffic-sync-empty@example.com")
    project_id, workspace_id = await _create_project(client)
    url = f"/api/v1/projects/{project_id}/traffic/sync"

    resp = await client.post(url)
    assert resp.status_code == 202
    assert resp.json() == []

    async with session_factory() as session:
        ws = uuid.UUID(workspace_id)
        grant = await _seed_grant(session, workspace_id=ws)
        gsc = await _seed_connection(
            session, workspace_id=ws, grant=grant, provider=INTEGRATION_PROVIDER_GSC
        )
        await _seed_mapping(
            session,
            workspace_id=ws,
            connection=gsc,
            property_ref="https://example.com/",
            project_id=uuid.UUID(project_id),
            status=MAPPING_STATUS_DISABLED,
        )
        await session.commit()

    disabled_only = await client.post(url)
    assert disabled_only.status_code == 202
    assert disabled_only.json() == []


@pytest.mark.asyncio
async def test_sync_active_window_conflict_is_409(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A second sync while the first is still active -> 409 (spec §6)."""
    await _register(client, "traffic-sync-conflict@example.com")
    project_id, workspace_id = await _create_project(client)
    async with session_factory() as session:
        ws = uuid.UUID(workspace_id)
        grant = await _seed_grant(session, workspace_id=ws)
        gsc = await _seed_connection(
            session, workspace_id=ws, grant=grant, provider=INTEGRATION_PROVIDER_GSC
        )
        await _seed_mapping(
            session,
            workspace_id=ws,
            connection=gsc,
            property_ref="https://example.com/",
            project_id=uuid.UUID(project_id),
        )
        await session.commit()

    url = f"/api/v1/projects/{project_id}/traffic/sync"
    first = await client.post(url)
    assert first.status_code == 202
    assert len(first.json()) == 1

    # Same default window, run still active upstream -> 409.
    second = await client.post(url)
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["error"] == "sync_active_window_conflict"
    # The single connection conflicted on its own enqueue: nothing was
    # fanned out before the conflict.
    assert detail["enqueued_connection_ids"] == []


# ---------------------------------------------------------------------------
# Pass-through seam (monkeypatched integrations enqueue)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sync_passes_through_to_integrations_enqueue(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint performs NO fetch itself: it calls the integrations
    enqueue once per fanned-out connection with the pinned call contract."""
    await _register(client, "traffic-sync-passthrough@example.com")
    project_id, workspace_id = await _create_project(client)
    async with session_factory() as session:
        ws = uuid.UUID(workspace_id)
        grant = await _seed_grant(session, workspace_id=ws)
        gsc = await _seed_connection(
            session, workspace_id=ws, grant=grant, provider=INTEGRATION_PROVIDER_GSC
        )
        ga4 = await _seed_connection(
            session, workspace_id=ws, grant=grant, provider=INTEGRATION_PROVIDER_GA4
        )
        await _seed_mapping(
            session,
            workspace_id=ws,
            connection=gsc,
            property_ref="https://example.com/",
            project_id=uuid.UUID(project_id),
        )
        await _seed_mapping(
            session,
            workspace_id=ws,
            connection=ga4,
            property_ref="properties/123456789",
            project_id=uuid.UUID(project_id),
        )
        await session.commit()

    calls: list[dict[str, object]] = []

    async def _fake_enqueue(
        session, *, workspace_id, connection_id, sync_kind, **kwargs
    ):
        calls.append(
            {
                "workspace_id": workspace_id,
                "connection_id": connection_id,
                "sync_kind": sync_kind,
                "extra": kwargs,
            }
        )
        return SimpleNamespace(
            id=uuid.uuid4(), connection_id=connection_id, status="queued"
        )

    monkeypatch.setattr("app.api.traffic.enqueue_sync_run", _fake_enqueue)

    resp = await client.post(f"/api/v1/projects/{project_id}/traffic/sync")
    assert resp.status_code == 202
    body = resp.json()
    # One enqueue call per fanned-out connection, pinned call contract:
    # workspace + connection + the on-demand kind, no explicit window.
    assert len(calls) == 2
    for call in calls:
        assert call["workspace_id"] == uuid.UUID(workspace_id)
        assert call["sync_kind"] == SYNC_KIND_ON_DEMAND
        assert call["extra"] == {}
    assert {call["connection_id"] for call in calls} == {gsc.id, ga4.id}
    # The exact bare-array C3 shape over the fake run identities.
    assert len(body) == 2
    for item in body:
        assert set(item) == _SYNC_ENQUEUE_KEYS
        assert item["status"] == "queued"
        uuid.UUID(item["sync_run_id"])  # parses as a UUID
    assert {item["connection_id"] for item in body} == {str(gsc.id), str(ga4.id)}


@pytest.mark.asyncio
async def test_sync_maps_enqueue_conflict_to_409(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ActiveWindowConflictError from the enqueue service -> 409."""
    from app.domain.integrations.sync import ActiveWindowConflictError

    await _register(client, "traffic-sync-fakeconflict@example.com")
    project_id, workspace_id = await _create_project(client)
    async with session_factory() as session:
        ws = uuid.UUID(workspace_id)
        grant = await _seed_grant(session, workspace_id=ws)
        gsc = await _seed_connection(
            session, workspace_id=ws, grant=grant, provider=INTEGRATION_PROVIDER_GSC
        )
        await _seed_mapping(
            session,
            workspace_id=ws,
            connection=gsc,
            property_ref="https://example.com/",
            project_id=uuid.UUID(project_id),
        )
        await session.commit()

    async def _conflicting_enqueue(session, **kwargs):
        raise ActiveWindowConflictError("an active run already covers the window")

    monkeypatch.setattr("app.api.traffic.enqueue_sync_run", _conflicting_enqueue)

    resp = await client.post(f"/api/v1/projects/{project_id}/traffic/sync")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "sync_active_window_conflict"
    assert detail["enqueued_connection_ids"] == []


@pytest.mark.asyncio
async def test_sync_409_names_already_enqueued_connections(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-fan-out conflict still reports the runs already committed.

    Each connection's enqueue commits independently, so when the second
    connection conflicts the first connection's run WILL run — the 409
    detail must name it instead of hiding the partial fan-out.
    """
    from app.domain.integrations.sync import ActiveWindowConflictError

    await _register(client, "traffic-sync-partial@example.com")
    project_id, workspace_id = await _create_project(client)
    async with session_factory() as session:
        ws = uuid.UUID(workspace_id)
        grant = await _seed_grant(session, workspace_id=ws)
        gsc = await _seed_connection(
            session, workspace_id=ws, grant=grant, provider=INTEGRATION_PROVIDER_GSC
        )
        ga4 = await _seed_connection(
            session, workspace_id=ws, grant=grant, provider=INTEGRATION_PROVIDER_GA4
        )
        await _seed_mapping(
            session,
            workspace_id=ws,
            connection=gsc,
            property_ref="https://example.com/",
            project_id=uuid.UUID(project_id),
        )
        await _seed_mapping(
            session,
            workspace_id=ws,
            connection=ga4,
            property_ref="properties/123456789",
            project_id=uuid.UUID(project_id),
        )
        await session.commit()

    async def _partially_conflicting_enqueue(session, *, connection_id, **kwargs):
        if connection_id == ga4.id:
            raise ActiveWindowConflictError("an active run already covers the window")
        return SimpleNamespace(
            id=uuid.uuid4(), connection_id=connection_id, status="queued"
        )

    monkeypatch.setattr(
        "app.api.traffic.enqueue_sync_run", _partially_conflicting_enqueue
    )

    resp = await client.post(f"/api/v1/projects/{project_id}/traffic/sync")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "sync_active_window_conflict"
    # The GSC run was enqueued (and committed) before the GA4 conflict.
    assert detail["enqueued_connection_ids"] == [str(gsc.id)]
