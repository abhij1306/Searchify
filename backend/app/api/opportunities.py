# Opportunities router: workspace-scoped catalog + recompute API.
#
# Flat API surface under ``/api/v1`` (no workspace_id in the path); the active
# workspace is resolved by ``require_active_workspace`` from the
# ``X-Workspace-Id`` header (or the caller's default workspace) and EVERY
# lookup is filtered by it, so a foreign/missing id is always a 404
# (invariant 5). The router only maps the service layer's coded errors onto
# HTTP — it never fetches, re-scores, or fabricates a row. ``recompute`` is
# the only write beyond the human status patch; it is inline-only in v1 (no
# queue) and returns the immutable snapshot it wrote.
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.opportunities.exports import rows_to_csv, rows_to_markdown
from app.api.deps import WorkspaceContext, get_db, require_active_workspace
from app.core.config.opportunities import LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT
from app.domain.opportunities import service
from app.domain.opportunities.schemas import (
    OpportunitiesPage,
    OpportunityDetail,
    OpportunityItem,
    OpportunityStatusPatch,
    OpportunitySummary,
    RecomputeRequest,
    RecomputeResponse,
)
from app.domain.opportunities.service import (
    InvalidCursorError,
    OpportunityNotFoundError,
    OpportunitySupersededError,
    OpportunityValidationError,
)

router = APIRouter(prefix="", tags=["opportunities"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]


def _not_found(exc: OpportunityNotFoundError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _validation(exc: OpportunityValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
    )


def _bad_cursor(exc: InvalidCursorError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _superseded(exc: OpportunitySupersededError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": exc.code, "message": str(exc)},
    )


# =========================================================================
# Catalog (priority-sorted, keyset-paginated)
# =========================================================================
@router.get(
    "/projects/{project_id}/opportunities",
    response_model=OpportunitiesPage,
)
async def list_opportunities_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    type_filter: Annotated[str | None, Query(alias="type")] = None,
    severity: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    rule_id: Annotated[str | None, Query()] = None,
    min_priority: Annotated[float | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_MAX_LIMIT)] = LIST_DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> OpportunitiesPage:
    try:
        page = await service.list_opportunities(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            opportunity_type=type_filter,
            severity=severity,
            status=status_filter,
            rule_id=rule_id,
            min_priority=min_priority,
            limit=limit,
            cursor=cursor,
        )
    except OpportunityNotFoundError as exc:
        raise _not_found(exc) from exc
    except OpportunityValidationError as exc:
        raise _validation(exc) from exc
    except InvalidCursorError as exc:
        raise _bad_cursor(exc) from exc
    return OpportunitiesPage.model_validate(page)


@router.get(
    "/projects/{project_id}/opportunities/summary",
    response_model=OpportunitySummary,
)
async def get_summary_endpoint(
    project_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> OpportunitySummary:
    try:
        summary = await service.get_summary(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except OpportunityNotFoundError as exc:
        raise _not_found(exc) from exc
    return OpportunitySummary.model_validate(summary)


@router.post(
    "/projects/{project_id}/opportunities/recompute",
    response_model=RecomputeResponse,
)
async def recompute_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    payload: RecomputeRequest | None = None,
) -> RecomputeResponse:
    try:
        snapshot = await service.recompute(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            audit_id=payload.audit_id if payload is not None else None,
            site_crawl_id=payload.site_crawl_id if payload is not None else None,
        )
    except OpportunityNotFoundError as exc:
        raise _not_found(exc) from exc
    return RecomputeResponse.model_validate(snapshot)


# =========================================================================
# Row read + the one mutation (human workflow status)
# =========================================================================
@router.get("/opportunities/{opportunity_id}", response_model=OpportunityDetail)
async def get_opportunity_endpoint(
    opportunity_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> OpportunityDetail:
    try:
        detail = await service.get_opportunity(
            session, workspace_id=ctx.workspace_id, opportunity_id=opportunity_id
        )
    except OpportunityNotFoundError as exc:
        raise _not_found(exc) from exc
    return OpportunityDetail.model_validate(detail)


@router.patch("/opportunities/{opportunity_id}", response_model=OpportunityItem)
async def update_status_endpoint(
    opportunity_id: uuid.UUID,
    payload: OpportunityStatusPatch,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> OpportunityItem:
    try:
        item = await service.update_status(
            session,
            workspace_id=ctx.workspace_id,
            opportunity_id=opportunity_id,
            status=payload.status,
        )
    except OpportunityNotFoundError as exc:
        raise _not_found(exc) from exc
    except OpportunityValidationError as exc:
        raise _validation(exc) from exc
    except OpportunitySupersededError as exc:
        raise _superseded(exc) from exc
    return OpportunityItem.model_validate(item)


# =========================================================================
# Exports (same projection + filters as the catalog, workspace-safe)
# =========================================================================
async def _export_rows(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    type_filter: str | None,
    severity: str | None,
    status_filter: str | None,
    rule_id: str | None,
    min_priority: float | None,
) -> list[dict]:
    return await service.load_export_rows(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        opportunity_type=type_filter,
        severity=severity,
        status=status_filter,
        rule_id=rule_id,
        min_priority=min_priority,
    )


@router.get("/projects/{project_id}/opportunities/export.csv")
async def export_csv_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    type_filter: Annotated[str | None, Query(alias="type")] = None,
    severity: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    rule_id: Annotated[str | None, Query()] = None,
    min_priority: Annotated[float | None, Query()] = None,
) -> Response:
    try:
        rows = await _export_rows(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            type_filter=type_filter,
            severity=severity,
            status_filter=status_filter,
            rule_id=rule_id,
            min_priority=min_priority,
        )
    except OpportunityNotFoundError as exc:
        raise _not_found(exc) from exc
    except OpportunityValidationError as exc:
        raise _validation(exc) from exc
    headers = {
        "Content-Disposition": (
            f'attachment; filename="opportunities-{project_id}.csv"'
        )
    }
    return Response(content=rows_to_csv(rows), media_type="text/csv", headers=headers)


@router.get("/projects/{project_id}/opportunities/export.md")
async def export_markdown_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    type_filter: Annotated[str | None, Query(alias="type")] = None,
    severity: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    rule_id: Annotated[str | None, Query()] = None,
    min_priority: Annotated[float | None, Query()] = None,
) -> PlainTextResponse:
    try:
        rows = await _export_rows(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            type_filter=type_filter,
            severity=severity,
            status_filter=status_filter,
            rule_id=rule_id,
            min_priority=min_priority,
        )
    except OpportunityNotFoundError as exc:
        raise _not_found(exc) from exc
    except OpportunityValidationError as exc:
        raise _validation(exc) from exc
    headers = {
        "Content-Disposition": f'attachment; filename="opportunities-{project_id}.md"'
    }
    return PlainTextResponse(
        content=rows_to_markdown(rows), media_type="text/markdown", headers=headers
    )
