# LLM Analytics router (A9): the three read endpoints behind /analytics.
#
# Projections only (invariant 7): every endpoint serves persisted evidence —
# the ``AnalyticsSnapshot`` rows built by the A8 refresh executor (headline +
# themes) and the persisted ``ReferralClassification`` + ``ReferralEvent``
# rows (referrals drill-down, keyset-paged per contract C4). No provider is
# ever called and nothing is recomputed at read time: an absent snapshot
# yields an empty payload (the trends empty-history precedent).
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
from app.core.config.analytics import ANALYTICS_DEFAULT_GRANULARITY
from app.core.http_errors import raise_not_found
from app.domain.analytics.schemas import (
    AnalyticsReferralsPage,
    LlmAnalyticsResponse,
    LlmAnalyticsThemeRow,
)
from app.domain.analytics.service import (
    AnalyticsCursorError,
    AnalyticsQueryError,
    get_llm_analytics,
    get_llm_analytics_referrals,
    get_llm_analytics_themes,
)
from app.domain.projects.service import ProjectNotFoundError, get_project

router = APIRouter(prefix="/projects", tags=["llm-analytics"])

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


def _unprocessable(exc: AnalyticsQueryError) -> HTTPException:
    # Query-validation contract (the trends ``TrendQueryError`` precedent):
    # a bad granularity/window/source is a 422, never a 404 or a 500.
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
    )


@router.get("/{project_id}/llm-analytics", response_model=LlmAnalyticsResponse)
async def get_llm_analytics_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    granularity: Annotated[str, Query()] = ANALYTICS_DEFAULT_GRANULARITY,
) -> LlmAnalyticsResponse:
    """Headline AEO Insights projection for a project (invariant 7).

    AI-referral volume + share series, the per-``ai_source`` breakdown, the
    cross-engine visibility series, and the visibility<->referral
    correlation summary — served from the persisted ``AnalyticsSnapshot``
    matching ``(from, to, granularity)`` (or the project's latest snapshot
    at the granularity when the window is omitted). An absent snapshot
    returns an empty payload (not 404); an invalid granularity/window
    returns 422.
    """
    # Authorize the project first (404 for a cross-workspace/missing project).
    await _get_project_or_404(session, ctx.workspace_id, project_id)
    try:
        return await get_llm_analytics(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            from_date=from_date,
            to_date=to_date,
            granularity=granularity,
        )
    except AnalyticsQueryError as exc:
        raise _unprocessable(exc) from exc


@router.get(
    "/{project_id}/llm-analytics/referrals",
    response_model=AnalyticsReferralsPage,
)
async def get_llm_analytics_referrals_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    source: Annotated[str | None, Query()] = None,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> AnalyticsReferralsPage:
    """Paged classified-referral drill-down (keyset, contract C4).

    Newest-first pages over the persisted classification + event rows,
    optionally filtered by ``source`` (the ``ai_source`` vocabulary) and an
    inclusive ``from``/``to`` date window. A cursor replayed against a
    different source/window (or a tampered/malformed cursor) returns 400;
    an invalid source/window returns 422.
    """
    await _get_project_or_404(session, ctx.workspace_id, project_id)
    try:
        return await get_llm_analytics_referrals(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            source=source,
            from_date=from_date,
            to_date=to_date,
            cursor=cursor,
        )
    except AnalyticsQueryError as exc:
        raise _unprocessable(exc) from exc
    except AnalyticsCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc


@router.get(
    "/{project_id}/llm-analytics/themes",
    response_model=list[LlmAnalyticsThemeRow],
)
async def get_llm_analytics_themes_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
) -> list[LlmAnalyticsThemeRow]:
    """Prompt/theme-level visibility rollup for a project (invariant 7).

    The window-level rollup folded into the persisted snapshot (grouped by
    the frozen theme/intent of the audited prompts). An absent snapshot
    returns an empty list (not 404); an invalid window returns 422.
    """
    await _get_project_or_404(session, ctx.workspace_id, project_id)
    try:
        return await get_llm_analytics_themes(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            from_date=from_date,
            to_date=to_date,
        )
    except AnalyticsQueryError as exc:
        raise _unprocessable(exc) from exc
