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
from app.connectors.agent.client import AgentNotConfiguredError, DefaultAgentClient
from app.connectors.answer_engines.errors import ProviderError
from app.core.config.analysis import (
    VISIBILITY_EVIDENCE_DEFAULT_LIMIT,
    VISIBILITY_EVIDENCE_MAX_LIMIT,
    VISIBILITY_TREND_DEFAULT_GRANULARITY,
)
from app.core.http_errors import raise_not_found
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
from app.domain.projects.brand_profile import (
    BrandProfileNotFoundError,
    brand_profile_to_response,
    get_brand_profile,
    upsert_manual_brand_profile,
)
from app.domain.projects.brand_profile_suggestions import (
    BrandProfileSuggestionNotFoundError,
    BrandProfileSuggestionOutputError,
    BrandProfileSuggestionValidationError,
    accept_brand_profile_suggestion,
    brand_profile_suggestion_to_response,
    suggest_brand_profile,
    validate_brand_profile_suggest_request,
)
from app.domain.projects.schemas import (
    BrandProfileAcceptRequest,
    BrandProfileAcceptResponse,
    BrandProfileResponse,
    BrandProfileSuggestionResponse,
    BrandProfileSuggestRequest,
    BrandProfileUpsert,
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

_RES_PROJECT = "Project"

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]


def _resolve_default_agent() -> DefaultAgentClient:
    try:
        return DefaultAgentClient()
    except AgentNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "agent_not_configured",
                "message": (
                    "No default agent is configured. Set DEFAULT_AGENT_API_KEY "
                    "(or MISTRALAI_API_KEY) in the backend environment."
                ),
            },
        ) from exc


async def _get_project_or_404(
    session: AsyncSession, workspace_id: uuid.UUID, project_id: uuid.UUID
):
    """Authorize the project, translating a cross-workspace/missing project
    into the API's 404 (mirrors ``_get_or_404`` in audits.py)."""
    try:
        return await get_project(
            session, workspace_id=workspace_id, project_id=project_id
        )
    except ProjectNotFoundError as exc:
        raise_not_found(_RES_PROJECT, cause=exc)


@router.get("", response_model=list[ProjectResponse])
async def list_projects_endpoint(
    ctx: _WorkspaceDep, session: _SessionDep
) -> list[ProjectResponse]:
    projects = await list_projects(session, workspace_id=ctx.workspace_id)
    return [project_to_response(p) for p in projects]


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project_endpoint(
    payload: ProjectCreate, ctx: _WorkspaceDep, session: _SessionDep
) -> ProjectResponse:
    project = await create_project(
        session, workspace_id=ctx.workspace_id, payload=payload
    )
    return project_to_response(project)


@router.get(
    "/{project_id}/brand-profile",
    response_model=BrandProfileResponse,
)
async def get_brand_profile_endpoint(
    project_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> BrandProfileResponse:
    # Authorize through the owning project before reading the denormalized row.
    await _get_project_or_404(session, ctx.workspace_id, project_id)
    try:
        profile = await get_brand_profile(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except BrandProfileNotFoundError as exc:
        raise_not_found("Brand profile", cause=exc)
    return brand_profile_to_response(profile)


@router.put(
    "/{project_id}/brand-profile",
    response_model=BrandProfileResponse,
)
async def put_brand_profile_endpoint(
    project_id: uuid.UUID,
    payload: BrandProfileUpsert,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> BrandProfileResponse:
    try:
        profile = await upsert_manual_brand_profile(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            payload=payload,
        )
    except (ProjectNotFoundError, BrandProfileNotFoundError) as exc:
        raise_not_found("Brand profile", cause=exc)
    return brand_profile_to_response(profile)


@router.post(
    "/{project_id}/brand-profile/suggest",
    response_model=BrandProfileSuggestionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def suggest_brand_profile_endpoint(
    project_id: uuid.UUID,
    payload: BrandProfileSuggestRequest,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> BrandProfileSuggestionResponse:
    await _get_project_or_404(session, ctx.workspace_id, project_id)
    try:
        validate_brand_profile_suggest_request(payload)
    except BrandProfileSuggestionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "brand_profile_suggestion_invalid", "message": str(exc)},
        ) from exc
    agent = _resolve_default_agent()
    try:
        suggestion = await suggest_brand_profile(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            agent=agent,
        )
    except (ProjectNotFoundError, BrandProfileNotFoundError) as exc:
        raise_not_found("Brand profile", cause=exc)
    except BrandProfileSuggestionOutputError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "brand_profile_suggestion_unparseable",
                "message": str(exc),
            },
        ) from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "agent_call_failed", "message": str(exc)},
        ) from exc
    return brand_profile_suggestion_to_response(suggestion)


@router.post(
    "/{project_id}/brand-profile/suggestions/{suggestion_id}/accept",
    response_model=BrandProfileAcceptResponse,
)
async def accept_brand_profile_suggestion_endpoint(
    project_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    payload: BrandProfileAcceptRequest,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> BrandProfileAcceptResponse:
    try:
        return await accept_brand_profile_suggestion(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            suggestion_id=suggestion_id,
            payload=payload,
        )
    except (ProjectNotFoundError, BrandProfileNotFoundError) as exc:
        raise_not_found("Brand profile", cause=exc)
    except BrandProfileSuggestionNotFoundError as exc:
        raise_not_found("Brand profile suggestion", cause=exc)
    except BrandProfileSuggestionValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "brand_profile_acceptance_invalid", "message": str(exc)},
        ) from exc


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
    granularity: Annotated[str, Query()] = VISIBILITY_TREND_DEFAULT_GRANULARITY,
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
    await _get_project_or_404(session, ctx.workspace_id, project_id)
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
    await _get_project_or_404(session, ctx.workspace_id, project_id)
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
        raise_not_found("Audit", cause=exc)
    except TrendQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project_endpoint(
    project_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> ProjectResponse:
    project = await _get_project_or_404(session, ctx.workspace_id, project_id)
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
    await _get_project_or_404(session, ctx.workspace_id, project_id)
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
        raise_not_found(_RES_PROJECT, cause=exc)
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
        raise_not_found(_RES_PROJECT, cause=exc)
