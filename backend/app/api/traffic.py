# Traffic router (A10 read surface + A11 sync pass-through).
#
# Projections only (invariant 7): every read endpoint serves persisted
# evidence — the ``TrafficSnapshot`` rows built by the A7 refresh executor
# (headline) and the persisted ``TrafficPageStat`` / ``TrafficQueryStat``
# rows (keyset-paged tables per contract C4). No provider is ever called
# and nothing is recomputed at read time: an absent snapshot yields an
# empty payload (the trends/A9 empty-history precedent).
#
# ``POST /projects/{id}/traffic/sync`` is a PASS-THROUGH (traffic.md
# section 6): it performs no fetch itself — it enqueues one on-demand
# ``IntegrationSyncRun`` per ACTIVE mapped GSC/GA4 connection of the
# project via the integrations enqueue service (invariant 2) and returns
# the 202 per-run identities (contract C3: a bare array of
# ``{sync_run_id, connection_id, status}``). The snapshot refresh fires
# when those integrations runs complete (the C5 hook).
#
# The surface is flat like the other MVP routers: the active workspace is
# resolved by ``require_active_workspace`` (``X-Workspace-Id`` header or the
# caller's default workspace) and the project is authorized through the
# workspace before any read (invariant 5).
from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import WorkspaceContext, get_db, require_active_workspace
from app.core.config.integrations import (
    ERROR_SYNC_ACTIVE_WINDOW_CONFLICT,
    SYNC_KIND_ON_DEMAND,
)
from app.core.config.traffic import TRAFFIC_DEFAULT_GRANULARITY
from app.core.http_errors import raise_not_found
from app.domain.integrations.schemas import IntegrationSyncEnqueueResponse
from app.domain.integrations.sync import (
    ActiveWindowConflictError,
    enqueue_sync_run,
)
from app.domain.projects.service import ProjectNotFoundError, get_project
from app.domain.traffic.schemas import (
    TrafficDashboardResponse,
    TrafficPagesPage,
    TrafficQueriesPage,
)
from app.domain.traffic.service import (
    TrafficCursorError,
    TrafficQueryError,
    get_traffic_dashboard,
    get_traffic_pages,
    get_traffic_queries,
    list_traffic_sync_connections,
)

router = APIRouter(prefix="/projects", tags=["traffic"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]


async def _get_project_or_404(
    session: AsyncSession, workspace_id: uuid.UUID, project_id: uuid.UUID
):
    """Authorize the project, translating a cross-workspace/missing project
    into the API's 404 (mirrors ``_get_project_or_404`` in projects.py)."""
    try:
        return await get_project(
            session, workspace_id=workspace_id, project_id=project_id
        )
    except ProjectNotFoundError as exc:
        raise_not_found("Project", cause=exc)


def _unprocessable(exc: TrafficQueryError) -> HTTPException:
    # Query-validation contract (the trends/A9 precedent): a bad
    # granularity/window/sort is a 422, never a 404 or a 500.
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
    )


def _bad_cursor(exc: TrafficCursorError) -> HTTPException:
    # A cursor replayed against different filters (or tampered/malformed)
    # is a 400 — never a silent row skip (site-health convention, C4).
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
    )


@router.get("/{project_id}/traffic", response_model=TrafficDashboardResponse)
async def get_traffic_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    granularity: Annotated[str, Query()] = TRAFFIC_DEFAULT_GRANULARITY,
) -> TrafficDashboardResponse:
    """Headline Traffic projection for a project (invariant 7).

    Totals + dated series (nullable points = unmeasured buckets) for
    impressions/clicks/ctr/position (GSC) and sessions/conversions (GA4) —
    served from the persisted ``TrafficSnapshot`` matching ``(from, to,
    granularity)`` (or the project's latest snapshot at the granularity
    when the window is omitted). An absent snapshot returns an empty
    payload (not 404); an invalid granularity/window returns 422.
    """
    # Authorize the project first (404 for a cross-workspace/missing project).
    await _get_project_or_404(session, ctx.workspace_id, project_id)
    try:
        return await get_traffic_dashboard(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            from_date=from_date,
            to_date=to_date,
            granularity=granularity,
        )
    except TrafficQueryError as exc:
        raise _unprocessable(exc) from exc


@router.get(
    "/{project_id}/traffic/pages",
    response_model=TrafficPagesPage,
)
async def get_traffic_pages_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    sort: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> TrafficPagesPage:
    """Paged per-page traffic rows (keyset, contract C4).

    Pages the persisted ``TrafficPageStat`` rows of the snapshot matching
    the window — sorting is restricted to the config whitelist
    (``-<key>`` descending / bare ``<key>`` ascending) and hits the stored
    aggregates only (invariant 7). A cursor replayed against a different
    window/sort (or a tampered/malformed cursor) returns 400; an invalid
    window/sort returns 422; an absent snapshot returns an empty page.
    """
    await _get_project_or_404(session, ctx.workspace_id, project_id)
    try:
        return await get_traffic_pages(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            from_date=from_date,
            to_date=to_date,
            sort=sort,
            cursor=cursor,
        )
    except TrafficQueryError as exc:
        raise _unprocessable(exc) from exc
    except TrafficCursorError as exc:
        raise _bad_cursor(exc) from exc


@router.get(
    "/{project_id}/traffic/queries",
    response_model=TrafficQueriesPage,
)
async def get_traffic_queries_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    sort: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> TrafficQueriesPage:
    """Paged per-query traffic rows (keyset, contract C4).

    Same contract as ``/traffic/pages`` over the persisted
    ``TrafficQueryStat`` rows (GSC-only measures; the key is the
    normalized query string).
    """
    await _get_project_or_404(session, ctx.workspace_id, project_id)
    try:
        return await get_traffic_queries(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            from_date=from_date,
            to_date=to_date,
            sort=sort,
            cursor=cursor,
        )
    except TrafficQueryError as exc:
        raise _unprocessable(exc) from exc
    except TrafficCursorError as exc:
        raise _bad_cursor(exc) from exc


@router.post(
    "/{project_id}/traffic/sync",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=list[IntegrationSyncEnqueueResponse],
)
async def sync_traffic_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> list[IntegrationSyncEnqueueResponse]:
    """Enqueue one on-demand sync run per active mapped GSC/GA4 connection.

    A pass-through to the integrations enqueue service (traffic.md section
    6 — NO fetch here, invariant 7): the default trailing window per
    connection; the snapshot refresh fires when the runs complete (C5).
    Returns 202 with the contract-C3 bare array — one
    ``{sync_run_id, connection_id, status}`` per queued run (empty when no
    active mapped connection feeds the project). A run still active for
    the same window upstream is a 409.
    """
    await _get_project_or_404(session, ctx.workspace_id, project_id)
    connections = await list_traffic_sync_connections(
        session, workspace_id=ctx.workspace_id, project_id=project_id
    )
    enqueued: list[IntegrationSyncEnqueueResponse] = []
    for connection in connections:
        try:
            run = await enqueue_sync_run(
                session,
                workspace_id=ctx.workspace_id,
                connection_id=connection.id,
                sync_kind=SYNC_KIND_ON_DEMAND,
            )
        except ActiveWindowConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ERROR_SYNC_ACTIVE_WINDOW_CONFLICT,
            ) from exc
        enqueued.append(
            IntegrationSyncEnqueueResponse(
                sync_run_id=run.id,
                connection_id=run.connection_id,
                status=run.status,
            )
        )
    return enqueued
