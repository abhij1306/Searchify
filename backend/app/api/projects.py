# Projects router: workspace-scoped CRUD (invariant 5).
#
# The MVP API surface is flat (no workspace_id in the path); the active
# workspace is resolved by ``require_active_workspace`` from the
# ``X-Workspace-Id`` header (or the caller's default workspace). Every query
# filters by that workspace. ``/projects/{id}/visibility`` is added in B6.
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import WorkspaceContext, get_db, require_active_workspace
from app.core.config.analysis import (
    VISIBILITY_EVIDENCE_DEFAULT_LIMIT,
    VISIBILITY_EVIDENCE_MAX_LIMIT,
    VISIBILITY_TREND_DEFAULT_GRANULARITY,
)
from app.domain.analysis.schemas import (
    VisibilityEvidenceResponse,
    VisibilityResponse,
    VisibilityTrendPoint,
)
from app.domain.analysis.service import (
    AnalysisNotFoundError,
    TrendQueryError,
    get_visibility,
    get_visibility_evidence,
    get_visibility_trends,
)
from app.domain.projects.schemas import (
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
)
from app.domain.projects.service import (
    ProjectNotFoundError,
    create_project,
    delete_project,
    get_project,
    list_projects,
    project_to_response,
    update_project,
)

router = APIRouter(prefix="/projects", tags=["projects"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=list[ProjectResponse])
async def list_projects_endpoint(
    ctx: _WorkspaceDep, session: _SessionDep
) -> list[ProjectResponse]:
    projects = await list_projects(session, workspace_id=ctx.workspace_id)
    return [project_to_response(p) for p in projects]


@router.post(
    "", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED
)
async def create_project_endpoint(
    payload: ProjectCreate, ctx: _WorkspaceDep, session: _SessionDep
) -> ProjectResponse:
    project = await create_project(
        session, workspace_id=ctx.workspace_id, payload=payload
    )
    return project_to_response(project)


@router.get(
    "/{project_id}/visibility/trends",
    response_model=list[VisibilityTrendPoint],
)
async def get_visibility_trends_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    engine: Annotated[str | None, Query()] = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    granularity: Annotated[
        str, Query()
    ] = VISIBILITY_TREND_DEFAULT_GRANULARITY,
) -> list[VisibilityTrendPoint]:
    """Cross-run Visibility trend projection for a project (invariant 7).

    An ordered series of ``VisibilityTrendPoint``s projected from the project's
    persisted dashboard-ready ``MetricSnapshot`` rows — optionally filtered by
    ``engine`` (``logical_engine``) and an inclusive UTC ``from``/``to`` window,
    and bucketed by ``granularity=run|week|month``. No provider is called and no
    historical run is re-scored. A valid project with no matching history
    returns ``[]`` (not 404); invalid engine/granularity/range or naive
    timestamps return 422.
    """
    # Authorize the project first (404 for a cross-workspace/missing project).
    try:
        await get_project(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    try:
        return await get_visibility_trends(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            logical_engine=engine,
            from_at=from_at,
            to_at=to_at,
            granularity=granularity,
        )
    except TrendQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get(
    "/{project_id}/visibility/evidence",
    response_model=VisibilityEvidenceResponse,
)
async def get_visibility_evidence_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    audit_id: Annotated[uuid.UUID | None, Query()] = None,
    prompt_id: Annotated[uuid.UUID | None, Query()] = None,
    engine: Annotated[str | None, Query()] = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[
        int, Query(ge=1, le=VISIBILITY_EVIDENCE_MAX_LIMIT)
    ] = VISIBILITY_EVIDENCE_DEFAULT_LIMIT,
) -> VisibilityEvidenceResponse:
    """Persisted execution-evidence projection for a project (invariant 7).

    The shared read-only dataset behind the Mentions & Citations and Query
    Fanout tabs: persisted brand/competitor mentions, classified citations, and
    normalized query-fanout events for the project's dashboard-ready audits —
    optionally filtered by ``audit_id``, ``prompt_id`` (source prompt on the
    frozen snapshot), ``engine`` (``logical_engine``), and an inclusive UTC
    ``from``/``to`` completion window. When both ``audit_id`` and a date window
    are supplied the filters intersect. No provider is called and no evidence is
    inferred/backfilled. A valid project with no matching evidence returns an
    empty ``items`` list (not 404); an unknown engine/range or naive timestamp
    returns 422; an ``audit_id`` outside the project/workspace returns 404
    without leaking whether it exists elsewhere.
    """
    # Authorize the project first (404 for a cross-workspace/missing project).
    try:
        await get_project(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    try:
        return await get_visibility_evidence(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            audit_id=audit_id,
            prompt_id=prompt_id,
            logical_engine=engine,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
        )
    except AnalysisNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Audit not found"
        ) from exc
    except TrendQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project_endpoint(
    project_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> ProjectResponse:
    try:
        project = await get_project(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    return project_to_response(project)


@router.get("/{project_id}/visibility", response_model=VisibilityResponse)
async def get_visibility_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    audit_id: Annotated[uuid.UUID | None, Query()] = None,
) -> VisibilityResponse:
    """Selected-run dashboard projection for a project (invariant 7).

    Visibility Score + per-engine comparison + brand-vs-competitor rankings,
    computed server-side from the persisted ``MetricSnapshot``. Defaults to the
    project's latest completed audit when ``audit_id`` is omitted. No provider
    is called; no cross-run trend at MVP (roadmap).
    """
    # Authorize the project first (404 for a cross-workspace/missing project).
    try:
        await get_project(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    try:
        return await get_visibility(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            audit_id=audit_id,
        )
    except AnalysisNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No visibility metrics available for project",
        ) from exc


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project_endpoint(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> ProjectResponse:
    try:
        project = await update_project(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            payload=payload,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    return project_to_response(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_endpoint(
    project_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> None:
    try:
        await delete_project(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
