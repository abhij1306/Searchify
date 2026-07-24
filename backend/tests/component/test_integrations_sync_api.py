"""Component tests for the sync API (I7).

Covers ``POST /integrations/{id}/sync`` (202 + the contract-C3 enqueue
identity ``{sync_run_id, connection_id, status}``; optional window body
validated/clamped; 409 on a duplicate active window; a completed window
re-syncs with a bumped ``resync_seq``) and the ``GET .../syncs`` /
``GET .../syncs/{sync_id}`` history/detail projections (status, window, row
counts, error fields — projection only, never tokens). Every DTO is checked
for the EXACT key set of the frontend strict zod schemas (contract C6).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config.integrations import integration_settings
from app.core.config.task_queue import TASK_STATUS_SUCCEEDED
from app.models.integrations import (
    IntegrationConnection,
    IntegrationImportArtifact,
    IntegrationOAuthGrant,
    IntegrationSyncRun,
)
from app.models.workspace import Workspace

_BASE = "/api/v1/integrations"

# Exact key sets of the frontend strict zod schemas (C6 parse-shape).
_ENQUEUE_KEYS = {"sync_run_id", "connection_id", "status"}
_RUN_KEYS = {
    "id",
    "connection_id",
    "sync_kind",
    "status",
    "window_start",
    "window_end",
    "row_count",
    "resync_seq",
    "error_code",
    "error_detail",
    "created_at",
    "updated_at",
    "completed_at",
}
_SYNC_KINDS = {"scheduled", "on_demand", "backfill"}
_RUN_STATUSES = {
    "queued",
    "leased",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "cancelled",
}


async def _register(client: httpx.AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201


async def _workspace_id(db_session) -> uuid.UUID:
    return (await db_session.execute(select(Workspace))).scalars().first().id


async def _seed_grant(
    db_session,
    *,
    workspace_id: uuid.UUID,
    providers: tuple[str, ...] = ("gsc", "ga4"),
) -> tuple[IntegrationOAuthGrant, list[IntegrationConnection]]:
    grant = IntegrationOAuthGrant(
        workspace_id=workspace_id,
        transport="google_oauth",
        status="connected",
    )
    db_session.add(grant)
    await db_session.flush()
    connections = [
        IntegrationConnection(
            workspace_id=workspace_id,
            grant_id=grant.id,
            provider=provider,
            label=f"{provider} label",
            account_ref=f"{provider}-account-ref",
        )
        for provider in providers
    ]
    db_session.add_all(connections)
    await db_session.commit()
    return grant, connections


async def _complete_run(db_session, run_id: str) -> None:
    run = await db_session.get(IntegrationSyncRun, uuid.UUID(run_id))
    run.status = TASK_STATUS_SUCCEEDED
    run.completed_at = datetime.now(UTC)
    await db_session.commit()


def _assert_run_contract_shape(row: dict) -> None:
    """The projection parses against the frontend contract shape (C6)."""
    assert set(row) == _RUN_KEYS
    assert uuid.UUID(row["id"])
    assert uuid.UUID(row["connection_id"])
    assert row["sync_kind"] in _SYNC_KINDS
    assert row["status"] in _RUN_STATUSES
    date.fromisoformat(row["window_start"])
    date.fromisoformat(row["window_end"])
    assert isinstance(row["row_count"], int)
    assert isinstance(row["resync_seq"], int)
    assert isinstance(row["error_code"], str)
    assert isinstance(row["error_detail"], str)
    datetime.fromisoformat(row["created_at"])
    datetime.fromisoformat(row["updated_at"])
    if row["completed_at"] is not None:
        datetime.fromisoformat(row["completed_at"])


@pytest.mark.asyncio
async def test_enqueue_default_window_returns_202_enqueue_dto(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "sync-default@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = next(c for c in connections if c.provider == "gsc")

    resp = await client.post(f"{_BASE}/{gsc.id}/sync")
    assert resp.status_code == 202
    body = resp.json()
    # Exact C3 enqueue shape: {sync_run_id, connection_id, status}.
    assert set(body) == _ENQUEUE_KEYS
    assert body["connection_id"] == str(gsc.id)
    assert body["status"] == "queued"
    assert uuid.UUID(body["sync_run_id"])
    # Invariant 6: nothing token-ish on the wire (runs carry no credentials).
    assert "_token" not in resp.text

    run = await db_session.get(IntegrationSyncRun, uuid.UUID(body["sync_run_id"]))
    assert run.sync_kind == "on_demand"
    assert run.resync_seq == 0
    assert run.window_end == datetime.now(UTC).date() - timedelta(days=1)
    assert (run.window_end - run.window_start).days + 1 == (
        integration_settings.sync_default_window_days
    )


@pytest.mark.asyncio
async def test_enqueue_explicit_window_stored(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "sync-window@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = connections[0]

    resp = await client.post(
        f"{_BASE}/{gsc.id}/sync",
        json={"window_start": "2026-07-01", "window_end": "2026-07-05"},
    )
    assert resp.status_code == 202
    detail = await client.get(f"{_BASE}/{gsc.id}/syncs/{resp.json()['sync_run_id']}")
    assert detail.status_code == 200
    row = detail.json()
    _assert_run_contract_shape(row)
    assert row["window_start"] == "2026-07-01"
    assert row["window_end"] == "2026-07-05"
    assert row["sync_kind"] == "on_demand"
    assert row["row_count"] == 0
    assert row["error_code"] == ""
    assert row["error_detail"] == ""
    assert row["completed_at"] is None


@pytest.mark.asyncio
async def test_enqueue_window_clamped_to_backfill_max(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "sync-clamp@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = connections[0]
    window_end = date(2026, 1, 1)
    window_start = window_end - timedelta(
        days=integration_settings.sync_backfill_max_days + 60
    )

    resp = await client.post(
        f"{_BASE}/{gsc.id}/sync",
        json={
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        },
    )
    assert resp.status_code == 202
    detail = await client.get(f"{_BASE}/{gsc.id}/syncs/{resp.json()['sync_run_id']}")
    row = detail.json()
    start = date.fromisoformat(row["window_start"])
    end = date.fromisoformat(row["window_end"])
    assert end == window_end
    assert (end - start).days + 1 == integration_settings.sync_backfill_max_days


@pytest.mark.asyncio
async def test_enqueue_invalid_window_is_422(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "sync-422@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = connections[0]

    inverted = await client.post(
        f"{_BASE}/{gsc.id}/sync",
        json={"window_start": "2026-07-05", "window_end": "2026-07-01"},
    )
    assert inverted.status_code == 422
    assert inverted.json()["detail"] == "sync_window_invalid"

    half = await client.post(
        f"{_BASE}/{gsc.id}/sync", json={"window_start": "2026-07-01"}
    )
    assert half.status_code == 422
    assert half.json()["detail"] == "sync_window_invalid"


@pytest.mark.asyncio
async def test_duplicate_active_window_is_409(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "sync-409@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = connections[0]
    window = {"window_start": "2026-07-01", "window_end": "2026-07-03"}

    first = await client.post(f"{_BASE}/{gsc.id}/sync", json=window)
    assert first.status_code == 202
    duplicate = await client.post(f"{_BASE}/{gsc.id}/sync", json=window)
    assert duplicate.status_code == 409
    # Same dict detail shape as the project-level sync fan-out 409.
    detail = duplicate.json()["detail"]
    assert detail["error"] == "sync_active_window_conflict"
    assert detail["enqueued_connection_ids"] == []

    runs = list((await db_session.execute(select(IntegrationSyncRun))).scalars())
    assert [str(run.id) for run in runs] == [first.json()["sync_run_id"]]


@pytest.mark.asyncio
async def test_completed_window_resyncs_with_bumped_seq(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "sync-resync@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = connections[0]
    window = {"window_start": "2026-07-01", "window_end": "2026-07-03"}

    first = await client.post(f"{_BASE}/{gsc.id}/sync", json=window)
    await _complete_run(db_session, first.json()["sync_run_id"])
    second = await client.post(f"{_BASE}/{gsc.id}/sync", json=window)
    assert second.status_code == 202
    assert second.json()["sync_run_id"] != first.json()["sync_run_id"]

    detail = await client.get(f"{_BASE}/{gsc.id}/syncs/{second.json()['sync_run_id']}")
    assert detail.json()["resync_seq"] == 1
    # The completed run is retained with its own identity (invariant 3).
    old = await client.get(f"{_BASE}/{gsc.id}/syncs/{first.json()['sync_run_id']}")
    assert old.json()["status"] == "succeeded"
    assert old.json()["resync_seq"] == 0


@pytest.mark.asyncio
async def test_list_syncs_projection_shape_and_row_counts(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "sync-list@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = next(c for c in connections if c.provider == "gsc")

    older = await client.post(
        f"{_BASE}/{gsc.id}/sync",
        json={"window_start": "2026-07-01", "window_end": "2026-07-03"},
    )
    newer = await client.post(
        f"{_BASE}/{gsc.id}/sync",
        json={"window_start": "2026-07-04", "window_end": "2026-07-06"},
    )
    # Two immutable import artifacts land on the older run (5 + 7 rows).
    for row_count in (5, 7):
        db_session.add(
            IntegrationImportArtifact(
                sync_run_id=uuid.UUID(older.json()["sync_run_id"]),
                connection_id=gsc.id,
                workspace_id=ws,
                provider="gsc",
                dataset="gsc_page_daily",
                payload_hash=f"{row_count}" * 64,
                row_count=row_count,
                query_snapshot={"startDate": "2026-07-01"},
                payload={"rows": []},
            )
        )
    await db_session.commit()

    resp = await client.get(f"{_BASE}/{gsc.id}/syncs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    for row in body:
        _assert_run_contract_shape(row)
    # Newest first; row_count is the summed artifact rows (0 when none).
    assert body[0]["id"] == newer.json()["sync_run_id"]
    assert body[0]["row_count"] == 0
    assert body[1]["id"] == older.json()["sync_run_id"]
    assert body[1]["row_count"] == 12


@pytest.mark.asyncio
async def test_get_sync_detail_404s(client: httpx.AsyncClient, db_session) -> None:
    await _register(client, "sync-detail-404@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = next(c for c in connections if c.provider == "gsc")
    ga4 = next(c for c in connections if c.provider == "ga4")
    enqueued = await client.post(f"{_BASE}/{gsc.id}/sync")
    sync_run_id = enqueued.json()["sync_run_id"]

    unknown = await client.get(f"{_BASE}/{gsc.id}/syncs/{uuid.uuid4()}")
    assert unknown.status_code == 404
    # A run is only addressable through its OWN connection.
    other_connection = await client.get(f"{_BASE}/{ga4.id}/syncs/{sync_run_id}")
    assert other_connection.status_code == 404
    unknown_connection = await client.get(f"{_BASE}/{uuid.uuid4()}/syncs/{sync_run_id}")
    assert unknown_connection.status_code == 404


@pytest.mark.asyncio
async def test_cross_workspace_sync_endpoints_are_404(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "sync-xws-owner@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = connections[0]
    enqueued = await client.post(f"{_BASE}/{gsc.id}/sync")
    sync_run_id = enqueued.json()["sync_run_id"]

    await client.post("/api/v1/auth/logout")
    await _register(client, "sync-xws-intruder@example.com")
    assert (await client.post(f"{_BASE}/{gsc.id}/sync")).status_code == 404
    assert (await client.get(f"{_BASE}/{gsc.id}/syncs")).status_code == 404
    assert (
        await client.get(f"{_BASE}/{gsc.id}/syncs/{sync_run_id}")
    ).status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_sync_rejected(client: httpx.AsyncClient) -> None:
    some_id = uuid.uuid4()
    assert (await client.post(f"{_BASE}/{some_id}/sync")).status_code == 401
    assert (await client.get(f"{_BASE}/{some_id}/syncs")).status_code == 401
    assert (await client.get(f"{_BASE}/{some_id}/syncs/{some_id}")).status_code == 401
