# Site Health router: workspace-scoped crawl/discovery/selection/analysis API.
#
# Flat API surface under ``/api/v1`` (no workspace_id in the path); the active
# workspace is resolved by ``require_active_workspace`` from the
# ``X-Workspace-Id`` header (or the caller's default workspace) and EVERY lookup
# is filtered by it, so a foreign/missing id is always a 404 (invariant 5). The
# router only projects persisted rows through the service layer — it never
# fetches, re-scores, or fabricates a metric. Coded selection/crawl failures are
# mapped to their stable HTTP statuses + bodies.
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import (
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.site_health.exports import (
    EXPORT_VIEWS,
    rows_to_csv,
    rows_to_markdown,
)
from app.api.deps import WorkspaceContext, get_db, require_active_workspace
from app.api.usage_limits import enforce_workspace_request
from app.core.config.abuse import abuse_settings
from app.core.config.errors import (
    CODE_INVALID_CURSOR,
    CODE_NOT_FOUND,
    CODE_VALIDATION_ERROR,
)
from app.core.config.site_health import (
    CODE_ADVANCED_CONTROLS_UNAVAILABLE,
    CODE_CRAWL_ALREADY_ACTIVE,
    CRAWL_TERMINAL_STATUSES,
    site_health_settings,
)
from app.core.database import SessionLocal
from app.core.errors import ApiException
from app.domain.site_health import service
from app.domain.site_health.api_schemas import (
    BulkSelectMonitoredRequest,
    ContradictionPage,
    CrawlListPage,
    CrawlResponse,
    CreateCrawlRequest,
    DashboardResponse,
    GroupedIssueHistoryPage,
    IntelligenceOverviewResponse,
    InventoryPage,
    IssueHistoryPage,
    KnowledgeAssertionPage,
    KnowledgeEntityPage,
    KnowledgeRelationPage,
    MonitoredUrlsResponse,
    PageDetail,
    PagesPage,
    PhaseMutationResponse,
    ReplaceMonitoredRequest,
    RerunPageResponse,
    SchemaGraphResponse,
    SiteHealthEntitlementResponse,
    SiteIssueDetail,
    SiteIssuesPage,
    StartAnalysisRequest,
    StartDiscoveryRequest,
    UrlPreviewRequest,
    UrlPreviewResponse,
)
from app.domain.site_health.phase_control import (
    CODE_ANALYSIS_LIMIT_EXCEEDED,
    CODE_DISCOVERY_LIMIT_EXCEEDED,
    CODE_PHASE_ALREADY_RUNNING,
    CODE_PHASE_NOT_RESUMABLE,
    PhaseControlError,
    PhaseMutationResult,
    start_analysis,
    start_discovery,
    stop_analysis,
    stop_discovery,
)
from app.domain.site_health.planner import (
    CrawlAlreadyActiveError,
    CrawlPlanError,
    create_crawl,
    preview_crawl_urls,
)
from app.domain.site_health.selection import (
    MonitoringNotAllowedError,
    QuotaExceededError,
    RerunNotAllowedError,
    SelectionValidationError,
    StaleSelectionVersionError,
    bulk_select_monitored_set,
    replace_monitored_set,
    rerun_page,
)
from app.domain.site_health.service import (
    InvalidCursorError,
    SiteHealthNotFoundError,
    project_phase_run,
)
from app.domain.site_health.state_events import redact_event_payload

router = APIRouter(prefix="", tags=["site-health"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]

_SSE_TERMINAL_GRACE_POLLS = 2


def _not_found(detail: str = "Not found") -> ApiException:
    return ApiException(status.HTTP_404_NOT_FOUND, CODE_NOT_FOUND, detail)


def _bad_cursor(exc: InvalidCursorError) -> ApiException:
    return ApiException(status.HTTP_400_BAD_REQUEST, CODE_INVALID_CURSOR, str(exc))


def _selection_error_response(exc: Exception) -> ApiException:
    """Map a coded selection error onto the Task 2 HTTP contract.

    ``monitoring_not_allowed`` -> 403, ``site_health_quota_exceeded`` -> 403 (with
    ``limit``/``currently_used``), ``stale_selection_version`` -> 409 (with
    ``current_selection_version``), ``invalid_selection`` -> 422. Shared by
    the PUT replacement and the bulk-select endpoints so both speak the same
    coded-error dialect. ``ApiException.coded`` keeps the legacy ``detail``
    dict byte-identical while adding the canonical ``error`` block (WS-A A1).
    """
    if isinstance(exc, QuotaExceededError):
        return ApiException.coded(
            status.HTTP_403_FORBIDDEN,
            exc.code,
            str(exc),
            details={"limit": exc.limit, "currently_used": exc.currently_used},
        )
    if isinstance(exc, StaleSelectionVersionError):
        return ApiException.coded(
            status.HTTP_409_CONFLICT,
            exc.code,
            str(exc),
            details={"current_selection_version": exc.current_version},
        )
    if isinstance(exc, MonitoringNotAllowedError):
        return ApiException.coded(status.HTTP_403_FORBIDDEN, exc.code, str(exc))
    # SelectionValidationError (and any other coded selection error) -> 422.
    code = getattr(exc, "code", "invalid_selection")
    return ApiException.coded(status.HTTP_422_UNPROCESSABLE_ENTITY, code, str(exc))


def _phase_error_response(exc: PhaseControlError) -> ApiException:
    if exc.code == "not_found":
        return _not_found(str(exc))
    if exc.code in {
        CODE_PHASE_ALREADY_RUNNING,
        CODE_PHASE_NOT_RESUMABLE,
        "stale_selection_version",
    }:
        return ApiException.coded(status.HTTP_409_CONFLICT, exc.code, str(exc))
    if exc.code == "site_health_quota_exceeded":
        return ApiException.coded(status.HTTP_403_FORBIDDEN, exc.code, str(exc))
    if exc.code in {CODE_DISCOVERY_LIMIT_EXCEEDED, CODE_ANALYSIS_LIMIT_EXCEEDED}:
        return ApiException.coded(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code, str(exc)
        )
    return ApiException.coded(status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code, str(exc))


async def _phase_mutation_view(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    result: PhaseMutationResult,
) -> dict:
    try:
        dashboard = await service.get_dashboard(
            session,
            workspace_id=workspace_id,
            project_id=result.crawl.project_id,
            crawl_id=result.crawl.id,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    run = result.phase_run
    return {
        "crawl": dashboard["crawl"],
        "phase_run": (project_phase_run(run) if run is not None else None),
        "created_new_crawl": result.created_new_crawl,
        "selection_version": result.selection_version,
        "scheduled_count": result.scheduled_count,
    }


async def _require_advanced_controls(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> None:
    entitlement = await service.get_entitlement_view(session, workspace_id=workspace_id)
    if not entitlement["advanced_controls_enabled"]:
        raise ApiException.coded(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            CODE_ADVANCED_CONTROLS_UNAVAILABLE,
            "Advanced Site Health controls are unavailable",
        )


async def _enforce_phase_mutation_request(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    phase: Literal["discovery", "analysis"],
) -> None:
    if phase == "discovery":
        operation = "site_health.discovery_phase_mutation"
        limit = abuse_settings.discovery_phase_mutation_limit
        window_seconds = abuse_settings.discovery_phase_mutation_window_seconds
    else:
        operation = "site_health.analysis_phase_mutation"
        limit = abuse_settings.analysis_phase_mutation_limit
        window_seconds = abuse_settings.analysis_phase_mutation_window_seconds
    await enforce_workspace_request(
        session,
        workspace_id=workspace_id,
        operation=operation,
        limit=limit,
        window_seconds=window_seconds,
    )


def _crawl_requested_page_limit(payload: CreateCrawlRequest) -> int | None:
    if payload.discovery_count is not None:
        return payload.discovery_count
    return payload.requested_page_limit


# =========================================================================
# Entitlement
# =========================================================================
@router.get("/entitlements", response_model=SiteHealthEntitlementResponse)
async def get_entitlements_endpoint(
    ctx: _WorkspaceDep, session: _SessionDep
) -> SiteHealthEntitlementResponse:
    # Read-only: the runtime projection refreshes lazily but the read commits
    # nothing (no seed commit).
    view = await service.get_entitlement_view(session, workspace_id=ctx.workspace_id)
    return SiteHealthEntitlementResponse.model_validate(view)


# =========================================================================
# Crawls
# =========================================================================
@router.post(
    "/site-crawls",
    response_model=CrawlResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_crawl_endpoint(
    payload: CreateCrawlRequest, ctx: _WorkspaceDep, session: _SessionDep
) -> CrawlResponse:
    await enforce_workspace_request(
        session,
        workspace_id=ctx.workspace_id,
        operation="site_health.crawl_create",
        limit=abuse_settings.crawl_create_limit,
        window_seconds=abuse_settings.crawl_create_window_seconds,
    )
    try:
        crawl = await create_crawl(
            session,
            workspace_id=ctx.workspace_id,
            project_id=payload.project_id,
            include_globs=payload.include_globs,
            exclude_globs=payload.exclude_globs,
            random_seed=payload.seed,
            input_mode=payload.input_mode,
            requested_page_limit=_crawl_requested_page_limit(payload),
            seed_urls=payload.seed_urls,
            page_kinds=payload.page_kinds,
        )
    except CrawlAlreadyActiveError as exc:
        raise ApiException.coded(
            status.HTTP_409_CONFLICT, CODE_CRAWL_ALREADY_ACTIVE, str(exc)
        ) from exc
    except CrawlPlanError as exc:
        if exc.code == "project_not_found":
            raise _not_found("Project not found") from exc
        raise ApiException.coded(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code, str(exc)
        ) from exc
    return CrawlResponse.model_validate(service.project_crawl(crawl))


@router.post(
    "/site-crawls/{crawl_id}/discovery/start",
    response_model=PhaseMutationResponse,
)
async def start_discovery_endpoint(
    crawl_id: uuid.UUID,
    payload: StartDiscoveryRequest,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> PhaseMutationResponse:
    await _require_advanced_controls(session, workspace_id=ctx.workspace_id)
    await _enforce_phase_mutation_request(
        session,
        workspace_id=ctx.workspace_id,
        phase="discovery",
    )
    try:
        result = await start_discovery(
            session,
            workspace_id=ctx.workspace_id,
            crawl_id=crawl_id,
            additional_url_count=payload.additional_url_count,
        )
    except PhaseControlError as exc:
        raise _phase_error_response(exc) from exc
    return PhaseMutationResponse.model_validate(
        await _phase_mutation_view(
            session, workspace_id=ctx.workspace_id, result=result
        )
    )


@router.post(
    "/site-crawls/{crawl_id}/discovery/stop",
    response_model=PhaseMutationResponse,
)
async def stop_discovery_endpoint(
    crawl_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> PhaseMutationResponse:
    await _require_advanced_controls(session, workspace_id=ctx.workspace_id)
    await _enforce_phase_mutation_request(
        session,
        workspace_id=ctx.workspace_id,
        phase="discovery",
    )
    try:
        result = await stop_discovery(
            session, workspace_id=ctx.workspace_id, crawl_id=crawl_id
        )
    except PhaseControlError as exc:
        raise _phase_error_response(exc) from exc
    return PhaseMutationResponse.model_validate(
        await _phase_mutation_view(
            session, workspace_id=ctx.workspace_id, result=result
        )
    )


@router.post(
    "/site-crawls/{crawl_id}/analysis/start",
    response_model=PhaseMutationResponse,
)
async def start_analysis_endpoint(
    crawl_id: uuid.UUID,
    payload: StartAnalysisRequest,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> PhaseMutationResponse:
    await _require_advanced_controls(session, workspace_id=ctx.workspace_id)
    await _enforce_phase_mutation_request(
        session,
        workspace_id=ctx.workspace_id,
        phase="analysis",
    )
    try:
        result = await start_analysis(
            session,
            workspace_id=ctx.workspace_id,
            crawl_id=crawl_id,
            requested_url_count=payload.requested_url_count,
            site_url_ids=payload.site_url_ids,
            expected_selection_version=payload.expected_selection_version,
        )
    except PhaseControlError as exc:
        raise _phase_error_response(exc) from exc
    return PhaseMutationResponse.model_validate(
        await _phase_mutation_view(
            session, workspace_id=ctx.workspace_id, result=result
        )
    )


@router.post(
    "/site-crawls/{crawl_id}/analysis/stop",
    response_model=PhaseMutationResponse,
)
async def stop_analysis_endpoint(
    crawl_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> PhaseMutationResponse:
    await _require_advanced_controls(session, workspace_id=ctx.workspace_id)
    await _enforce_phase_mutation_request(
        session,
        workspace_id=ctx.workspace_id,
        phase="analysis",
    )
    try:
        result = await stop_analysis(
            session, workspace_id=ctx.workspace_id, crawl_id=crawl_id
        )
    except PhaseControlError as exc:
        raise _phase_error_response(exc) from exc
    return PhaseMutationResponse.model_validate(
        await _phase_mutation_view(
            session, workspace_id=ctx.workspace_id, result=result
        )
    )


@router.post("/site-crawls/url-preview", response_model=UrlPreviewResponse)
async def preview_crawl_urls_endpoint(
    payload: UrlPreviewRequest, ctx: _WorkspaceDep, session: _SessionDep
) -> UrlPreviewResponse:
    """Preview admission only; it never creates a crawl, task, or fetch."""
    try:
        preview = await preview_crawl_urls(
            session,
            workspace_id=ctx.workspace_id,
            project_id=payload.project_id,
            content=payload.content,
            input_format=payload.input_format,
            include_globs=payload.include_globs,
            exclude_globs=payload.exclude_globs,
        )
    except CrawlPlanError as exc:
        if exc.code == "project_not_found":
            raise _not_found("Project not found") from exc
        raise ApiException.coded(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code, str(exc)
        ) from exc
    return UrlPreviewResponse.model_validate(preview)


@router.get("/site-crawls", response_model=CrawlListPage)
async def list_crawls_endpoint(
    ctx: _WorkspaceDep,
    session: _SessionDep,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> CrawlListPage:
    try:
        page = await service.list_crawls(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            limit=limit,
            cursor=cursor,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except InvalidCursorError as exc:
        raise _bad_cursor(exc) from exc
    return CrawlListPage.model_validate(page)


@router.get("/site-crawls/{crawl_id}", response_model=CrawlResponse)
async def get_crawl_endpoint(
    crawl_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> CrawlResponse:
    try:
        crawl = await service.get_crawl_summary(
            session, workspace_id=ctx.workspace_id, crawl_id=crawl_id
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return CrawlResponse.model_validate(crawl)


@router.post("/site-crawls/{crawl_id}/cancel", response_model=CrawlResponse)
async def cancel_crawl_endpoint(
    crawl_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> CrawlResponse:
    try:
        crawl = await service.cancel_crawl(
            session, workspace_id=ctx.workspace_id, crawl_id=crawl_id
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return CrawlResponse.model_validate(crawl)


# =========================================================================
# Inventory
# =========================================================================
@router.get("/site-crawls/{crawl_id}/inventory", response_model=InventoryPage)
async def get_inventory_endpoint(
    crawl_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    query: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    monitored: Annotated[bool | None, Query()] = None,
    page_kind: Annotated[str | None, Query()] = None,
) -> InventoryPage:
    try:
        page = await service.get_inventory(
            session,
            workspace_id=ctx.workspace_id,
            crawl_id=crawl_id,
            limit=limit,
            cursor=cursor,
            query=query,
            status=status_filter,
            monitored=monitored,
            page_kind=page_kind,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except InvalidCursorError as exc:
        raise _bad_cursor(exc) from exc
    return InventoryPage.model_validate(page)


# =========================================================================
# Monitored set
# =========================================================================
@router.get(
    "/projects/{project_id}/monitored-urls",
    response_model=MonitoredUrlsResponse,
)
async def get_monitored_urls_endpoint(
    project_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> MonitoredUrlsResponse:
    try:
        result = await service.get_monitored_set(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return MonitoredUrlsResponse.model_validate(result)


@router.put(
    "/projects/{project_id}/monitored-urls",
    response_model=MonitoredUrlsResponse,
)
async def replace_monitored_urls_endpoint(
    project_id: uuid.UUID,
    payload: ReplaceMonitoredRequest,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> MonitoredUrlsResponse:
    # Authorize the project first so a foreign id is a 404 (not a coded error).
    try:
        await service.get_monitored_set(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    try:
        await replace_monitored_set(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            site_url_ids=payload.site_url_ids,
            expected_selection_version=payload.expected_selection_version,
        )
        await session.commit()
    except (
        MonitoringNotAllowedError,
        QuotaExceededError,
        StaleSelectionVersionError,
        SelectionValidationError,
    ) as exc:
        await session.rollback()
        raise _selection_error_response(exc) from exc

    result = await service.get_monitored_set(
        session, workspace_id=ctx.workspace_id, project_id=project_id
    )
    return MonitoredUrlsResponse.model_validate(result)


@router.post(
    "/projects/{project_id}/monitored-urls/bulk-select",
    response_model=MonitoredUrlsResponse,
)
async def bulk_select_monitored_urls_endpoint(
    project_id: uuid.UUID,
    payload: BulkSelectMonitoredRequest,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> MonitoredUrlsResponse:
    """Server-resolved bulk selection (first N / all / clear).

    Resolves candidate ids server-side in the inventory's deterministic
    ``(normalized_url, id)`` order, then reuses the SAME atomic replacement
    path (locks, version check, workspace quota, coded errors) as the PUT.
    """
    # Authorize the project first so a foreign id is a 404 (not a coded error).
    try:
        await service.get_monitored_set(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    try:
        await bulk_select_monitored_set(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            crawl_id=payload.crawl_id,
            mode=payload.mode,
            count=payload.count,
            query=payload.query,
            expected_selection_version=payload.expected_selection_version,
        )
        await session.commit()
    except (
        MonitoringNotAllowedError,
        QuotaExceededError,
        StaleSelectionVersionError,
        SelectionValidationError,
    ) as exc:
        await session.rollback()
        raise _selection_error_response(exc) from exc

    result = await service.get_monitored_set(
        session, workspace_id=ctx.workspace_id, project_id=project_id
    )
    return MonitoredUrlsResponse.model_validate(result)


# =========================================================================
# Pages
# =========================================================================
@router.get("/site-crawls/{crawl_id}/pages", response_model=PagesPage)
async def get_pages_endpoint(
    crawl_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    monitored: Annotated[bool | None, Query()] = None,
    page_kind: Annotated[str | None, Query()] = None,
) -> PagesPage:
    try:
        page = await service.get_pages(
            session,
            workspace_id=ctx.workspace_id,
            crawl_id=crawl_id,
            limit=limit,
            cursor=cursor,
            status=status_filter,
            monitored=monitored,
            page_kind=page_kind,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except InvalidCursorError as exc:
        raise _bad_cursor(exc) from exc
    return PagesPage.model_validate(page)


@router.get("/site-crawls/{crawl_id}/pages/{site_url_id}", response_model=PageDetail)
async def get_page_detail_endpoint(
    crawl_id: uuid.UUID,
    site_url_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> PageDetail:
    try:
        detail = await service.get_page_detail(
            session,
            workspace_id=ctx.workspace_id,
            crawl_id=crawl_id,
            site_url_id=site_url_id,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return PageDetail.model_validate(detail)


@router.post(
    "/site-crawls/{crawl_id}/pages/{site_url_id}/rerun",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RerunPageResponse,
)
async def rerun_page_endpoint(
    crawl_id: uuid.UUID,
    site_url_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> RerunPageResponse:
    """Enqueue an explicit rerun of one page's analysis (202).

    Workspace-authorized via the same page-detail lookup as the GET route (a
    foreign/missing crawl or URL is a 404, never a coded selection error), so
    the rerun can never target another workspace's evidence.

    "Re-audit this page" is normally invoked from a COMPLETED (terminal) crawl.
    Because enqueuing into a terminal crawl would be cancelled by the worker,
    the domain layer mints a fresh single-page rerun crawl in that case. The
    202 body therefore carries the (possibly new) crawl identity + analysis
    status so the client polls the fresh run rather than the terminal source
    crawl: ``{crawl_id, site_url_id, task_id, created_new_crawl,
    analysis_status}``.
    """
    try:
        await service.get_page_detail(
            session,
            workspace_id=ctx.workspace_id,
            crawl_id=crawl_id,
            site_url_id=site_url_id,
        )
        crawl_summary = await service.get_crawl_summary(
            session, workspace_id=ctx.workspace_id, crawl_id=crawl_id
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc

    try:
        result = await rerun_page(
            session,
            workspace_id=ctx.workspace_id,
            project_id=crawl_summary["project_id"],
            site_url_id=site_url_id,
        )
        await session.commit()
    except MonitoringNotAllowedError as exc:
        await session.rollback()
        raise ApiException.coded(status.HTTP_403_FORBIDDEN, exc.code, str(exc)) from exc
    except RerunNotAllowedError as exc:
        await session.rollback()
        raise ApiException.coded(status.HTTP_409_CONFLICT, exc.code, str(exc)) from exc
    except SelectionValidationError as exc:
        await session.rollback()
        raise ApiException.coded(
            status.HTTP_422_UNPROCESSABLE_ENTITY, exc.code, str(exc)
        ) from exc

    return RerunPageResponse(
        crawl_id=result.crawl_id,
        site_url_id=result.site_url_id,
        task_id=result.task_id,
        created_new_crawl=result.created_new_crawl,
        analysis_status=result.analysis_status,
    )


@router.get(
    "/site-crawls/{crawl_id}/pages/{site_url_id}/issue-history",
    response_model=IssueHistoryPage | GroupedIssueHistoryPage,
)
async def get_issue_history_endpoint(
    crawl_id: uuid.UUID,
    site_url_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    view: Annotated[Literal["occurrences", "grouped"], Query()] = "occurrences",
) -> IssueHistoryPage | GroupedIssueHistoryPage:
    try:
        if view == "grouped":
            page = await service.get_grouped_issue_history(
                session,
                workspace_id=ctx.workspace_id,
                crawl_id=crawl_id,
                site_url_id=site_url_id,
                limit=limit,
                cursor=cursor,
            )
        else:
            page = await service.get_issue_history(
                session,
                workspace_id=ctx.workspace_id,
                crawl_id=crawl_id,
                site_url_id=site_url_id,
                limit=limit,
                cursor=cursor,
            )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except InvalidCursorError as exc:
        raise _bad_cursor(exc) from exc
    if view == "grouped":
        return GroupedIssueHistoryPage.model_validate(page)
    return IssueHistoryPage.model_validate(page)


# =========================================================================
# Issues (grouped) + detail
# =========================================================================
@router.get("/site-crawls/{crawl_id}/issues", response_model=SiteIssuesPage)
async def get_issues_endpoint(
    crawl_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    query: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    dimension: Annotated[str | None, Query()] = None,
    rule: Annotated[str | None, Query()] = None,
    site_url_id: Annotated[uuid.UUID | None, Query()] = None,
    page_kind: Annotated[str | None, Query()] = None,
) -> SiteIssuesPage:
    try:
        page = await service.get_issues(
            session,
            workspace_id=ctx.workspace_id,
            crawl_id=crawl_id,
            limit=limit,
            cursor=cursor,
            query=query,
            severity=severity,
            category=category,
            dimension=dimension,
            rule=rule,
            site_url_id=site_url_id,
            page_kind=page_kind,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except InvalidCursorError as exc:
        raise _bad_cursor(exc) from exc
    return SiteIssuesPage.model_validate(page)


@router.get(
    "/site-crawls/{crawl_id}/issues/{canonical_id}",
    response_model=SiteIssueDetail,
)
async def get_issue_detail_endpoint(
    crawl_id: uuid.UUID,
    canonical_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> SiteIssueDetail:
    try:
        detail = await service.get_issue_detail(
            session,
            workspace_id=ctx.workspace_id,
            crawl_id=crawl_id,
            canonical_id=canonical_id,
            limit=limit,
            cursor=cursor,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    except InvalidCursorError as exc:
        raise _bad_cursor(exc) from exc
    return SiteIssueDetail.model_validate(detail)


# =========================================================================
# Dashboard
# =========================================================================
@router.get("/projects/{project_id}/site-health", response_model=DashboardResponse)
async def get_dashboard_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    crawl_id: Annotated[uuid.UUID | None, Query()] = None,
) -> DashboardResponse:
    try:
        result = await service.get_dashboard(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            crawl_id=crawl_id,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return DashboardResponse.model_validate(result)


# =========================================================================
# Events (JSON replay or redacted SSE tail)
# =========================================================================
def _sse_payload(event, *, count_disclosure: bool) -> str:
    payload = redact_event_payload(event.payload, count_disclosure=count_disclosure)
    body = {
        "id": str(event.id),
        "crawl_id": str(event.crawl_id),
        "event_type": event.event_type,
        "message": event.message or "",
        "payload": payload or {},
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
    return f"event: {event.event_type}\nid: {event.id}\ndata: {json.dumps(body)}\n\n"


async def _event_stream(
    workspace_id: uuid.UUID,
    crawl_id: uuid.UUID,
    *,
    last_event_id: uuid.UUID | None,
):  # pragma: no cover - streaming loop
    """Tail a crawl's redacted events until terminal grace or max duration.

    Opens its own short-lived sessions (the request session is closed once the
    handler returns the ``StreamingResponse``). Redacts every payload with the
    crawl's frozen ``count_disclosure`` so a Free stream never leaks a total.
    """
    last_id = last_event_id
    terminal_polls = 0
    elapsed = 0.0
    poll = float(site_health_settings.sse_poll_interval_seconds)
    max_duration = float(site_health_settings.sse_max_duration_seconds)
    while True:
        async with SessionLocal() as session:
            crawl = await service.load_crawl_for_stream(
                session, workspace_id=workspace_id, crawl_id=crawl_id
            )
            disclose = service._crawl_count_disclosure(crawl)
            new_events = await service.load_events(
                session, crawl_id=crawl_id, after=last_id
            )
            for event in new_events:
                last_id = event.id
                yield _sse_payload(event, count_disclosure=disclose)
            terminal = crawl.status in CRAWL_TERMINAL_STATUSES
        if terminal:
            terminal_polls += 1
            if terminal_polls >= _SSE_TERMINAL_GRACE_POLLS:
                break
        if elapsed >= max_duration:
            break
        await asyncio.sleep(poll)
        elapsed += poll


@router.get("/site-crawls/{crawl_id}/events")
async def get_events_endpoint(
    crawl_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    request: Request,
    stream: Annotated[bool, Query()] = False,
) -> Response:
    # Authorize first (404 for a cross-workspace / missing crawl).
    try:
        crawl = await service.load_crawl_for_stream(
            session, workspace_id=ctx.workspace_id, crawl_id=crawl_id
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc

    disclose = service._crawl_count_disclosure(crawl)
    if not stream:
        events = await service.load_events(session, crawl_id=crawl_id)
        body = [
            {
                "id": str(e.id),
                "crawl_id": str(e.crawl_id),
                "event_type": e.event_type,
                "message": e.message or "",
                "payload": redact_event_payload(e.payload, count_disclosure=disclose)
                or {},
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ]
        return JSONResponse(content=body)

    # Resume from Last-Event-ID (header or query) so a reconnect does not
    # replay the whole stream.
    last_event_id: uuid.UUID | None = None
    raw_last = request.headers.get("Last-Event-ID") or request.query_params.get(
        "last_event_id"
    )
    if raw_last:
        try:
            last_event_id = uuid.UUID(raw_last)
        except ValueError:
            last_event_id = None
    return StreamingResponse(
        _event_stream(ctx.workspace_id, crawl_id, last_event_id=last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# =========================================================================
# Exports (workspace-safe attachments over persisted projections)
# =========================================================================
async def _export_items(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    crawl_id: uuid.UUID,
    view: str,
) -> tuple[list[dict], bool]:
    """Collect projected rows for an export view (workspace-scoped).

    Bounded by ``max_export_items`` (config-owned): once the cap is reached
    the loop stops paging and reports truncation, so a very large inventory
    can never be materialized entirely into memory for one export request.
    """
    items: list[dict] = []
    cursor: str | None = None
    limit = site_health_settings.max_export_items
    truncated = False
    while True:
        if view == "inventory":
            page = await service.get_inventory(
                session,
                workspace_id=workspace_id,
                crawl_id=crawl_id,
                limit=200,
                cursor=cursor,
            )
        elif view == "pages":
            page = await service.get_pages(
                session,
                workspace_id=workspace_id,
                crawl_id=crawl_id,
                limit=200,
                cursor=cursor,
            )
        else:  # issues
            page = await service.get_issues(
                session,
                workspace_id=workspace_id,
                crawl_id=crawl_id,
                limit=200,
                cursor=cursor,
            )
        items.extend(page["items"])
        if len(items) >= limit:
            items = items[:limit]
            truncated = bool(page.get("next_cursor"))
            break
        cursor = page.get("next_cursor")
        if not cursor:
            break
    if view == "issues" and items:
        # v2 P1: the issues export carries a page_kind column listing the
        # distinct classified types of each group's affected analyses (a
        # group can span types, so the JSON issue DTO has no single badge).
        # Pages/inventory rows already carry their scalar page_kind.
        page_kinds_by_rule = await service.issue_group_page_kinds(
            session, workspace_id=workspace_id, crawl_id=crawl_id
        )
        for item in items:
            item["page_kind"] = ", ".join(
                page_kinds_by_rule.get(str(item.get("rule_id") or ""), [])
            )
    return items, truncated


def _validate_view(view: str) -> str:
    if view not in EXPORT_VIEWS:
        raise ApiException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            CODE_VALIDATION_ERROR,
            f"unknown export view: {view}",
        )
    return view


@router.get("/site-crawls/{crawl_id}/export.csv")
async def export_csv_endpoint(
    crawl_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    view: Annotated[str, Query()] = "inventory",
) -> Response:
    view = _validate_view(view)
    try:
        items, truncated = await _export_items(
            session, workspace_id=ctx.workspace_id, crawl_id=crawl_id, view=view
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    body = rows_to_csv(view, items)
    headers = {
        "Content-Disposition": (
            f'attachment; filename="site-health-{crawl_id}-{view}.csv"'
        )
    }
    if truncated:
        headers["X-Export-Truncated"] = "true"
    return Response(
        content=body,
        media_type="text/csv",
        headers=headers,
    )


@router.get("/site-crawls/{crawl_id}/export.md")
async def export_markdown_endpoint(
    crawl_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    view: Annotated[str, Query()] = "inventory",
) -> PlainTextResponse:
    view = _validate_view(view)
    try:
        items, truncated = await _export_items(
            session, workspace_id=ctx.workspace_id, crawl_id=crawl_id, view=view
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    body = rows_to_markdown(view, items)
    headers = {
        "Content-Disposition": (
            f'attachment; filename="site-health-{crawl_id}-{view}.md"'
        )
    }
    if truncated:
        headers["X-Export-Truncated"] = "true"
    return PlainTextResponse(
        content=body,
        media_type="text/markdown",
        headers=headers,
    )


# =========================================================================
# Site Intelligence
# =========================================================================
# The URL family stays under ``/site-health`` while the navigation label becomes
# Site Intelligence (plan §11), so existing links and bookmarks keep working.
#
# Every handler below renders the projection FROZEN onto the crawl's snapshot.
# None of them resolves a pack, fetches a URL, reclassifies a page, or
# recomputes a score: a read that recomputed could disagree with the report a
# user already exported, and a historical crawl must keep reporting what it
# reported under the pack it froze.
@router.get(
    "/projects/{project_id}/site-intelligence",
    response_model=IntelligenceOverviewResponse,
)
async def get_site_intelligence_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    crawl_id: Annotated[uuid.UUID | None, Query()] = None,
) -> IntelligenceOverviewResponse:
    """The one projection that drives every workspace panel."""
    try:
        result = await service.get_intelligence_overview(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            crawl_id=crawl_id,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return IntelligenceOverviewResponse.model_validate(result)


@router.get(
    "/projects/{project_id}/knowledge/entities",
    response_model=KnowledgeEntityPage,
)
async def get_knowledge_entities_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    crawl_id: Annotated[uuid.UUID | None, Query()] = None,
    entity_type_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> KnowledgeEntityPage:
    try:
        result = await service.get_knowledge_entities(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            crawl_id=crawl_id,
            entity_type_id=entity_type_id,
            limit=limit,
            offset=offset,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return KnowledgeEntityPage.model_validate(result)


@router.get(
    "/projects/{project_id}/knowledge/assertions",
    response_model=KnowledgeAssertionPage,
)
async def get_knowledge_assertions_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    crawl_id: Annotated[uuid.UUID | None, Query()] = None,
    predicate_id: Annotated[str | None, Query(max_length=64)] = None,
    subject_entity_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> KnowledgeAssertionPage:
    try:
        result = await service.get_knowledge_assertions(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            crawl_id=crawl_id,
            predicate_id=predicate_id,
            subject_entity_id=subject_entity_id,
            limit=limit,
            offset=offset,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return KnowledgeAssertionPage.model_validate(result)


@router.get(
    "/projects/{project_id}/knowledge/contradictions",
    response_model=ContradictionPage,
)
async def get_knowledge_contradictions_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    crawl_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ContradictionPage:
    """Disputed claims as GROUPS, every side included and none pre-selected."""
    try:
        result = await service.get_knowledge_contradictions(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            crawl_id=crawl_id,
            limit=limit,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return ContradictionPage.model_validate(result)


@router.get(
    "/projects/{project_id}/knowledge/relations",
    response_model=KnowledgeRelationPage,
)
async def get_knowledge_relations_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    crawl_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> KnowledgeRelationPage:
    try:
        result = await service.get_knowledge_relations(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            crawl_id=crawl_id,
            limit=limit,
            offset=offset,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return KnowledgeRelationPage.model_validate(result)


@router.get(
    "/projects/{project_id}/site-intelligence/schema",
    response_model=SchemaGraphResponse,
)
async def get_schema_graph_endpoint(
    project_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    crawl_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> SchemaGraphResponse:
    try:
        result = await service.get_schema_graph(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            crawl_id=crawl_id,
            limit=limit,
        )
    except SiteHealthNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return SchemaGraphResponse.model_validate(result)
