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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
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
from app.core.config.site_health import (
    CODE_CRAWL_ALREADY_ACTIVE,
    CRAWL_TERMINAL_STATUSES,
    site_health_settings,
)
from app.core.database import SessionLocal
from app.domain.site_health import service
from app.domain.site_health.api_schemas import (
    CrawlListPage,
    CrawlResponse,
    CreateCrawlRequest,
    DashboardResponse,
    EntitlementResponse,
    InventoryPage,
    IssueHistoryPage,
    MonitoredUrlsResponse,
    PageDetail,
    PagesPage,
    ReplaceMonitoredRequest,
    RerunPageResponse,
    SiteIssueDetail,
    SiteIssuesPage,
)
from app.domain.site_health.planner import (
    CrawlAlreadyActiveError,
    CrawlPlanError,
    create_crawl,
)
from app.domain.site_health.selection import (
    QuotaExceededError,
    RerunNotAllowedError,
    SelectionValidationError,
    StaleSelectionVersionError,
    StarterRequiredError,
    replace_monitored_set,
    rerun_page,
)
from app.domain.site_health.service import (
    InvalidCursorError,
    SiteHealthNotFoundError,
)
from app.domain.site_health.state_events import redact_event_payload

router = APIRouter(prefix="", tags=["site-health"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]

_SSE_TERMINAL_GRACE_POLLS = 2


def _not_found(detail: str = "Not found") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _bad_cursor(exc: InvalidCursorError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# =========================================================================
# Entitlement
# =========================================================================
@router.get("/entitlements", response_model=EntitlementResponse)
async def get_entitlements_endpoint(
    ctx: _WorkspaceDep, session: _SessionDep
) -> EntitlementResponse:
    view = await service.get_entitlement_view(session, workspace_id=ctx.workspace_id)
    await session.commit()  # persist the fail-closed Free seed on first use
    return EntitlementResponse.model_validate(view)


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
    try:
        crawl = await create_crawl(
            session,
            workspace_id=ctx.workspace_id,
            project_id=payload.project_id,
            include_globs=payload.include_globs,
            exclude_globs=payload.exclude_globs,
            random_seed=payload.seed,
        )
    except CrawlAlreadyActiveError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": CODE_CRAWL_ALREADY_ACTIVE, "message": str(exc)},
        ) from exc
    except CrawlPlanError as exc:
        if exc.code == "project_not_found":
            raise _not_found("Project not found") from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return CrawlResponse.model_validate(service.project_crawl(crawl))


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
    except StarterRequiredError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except QuotaExceededError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": exc.code,
                "message": str(exc),
                "limit": exc.limit,
                "currently_used": exc.currently_used,
            },
        ) from exc
    except StaleSelectionVersionError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": exc.code,
                "message": str(exc),
                "current_selection_version": exc.current_version,
            },
        ) from exc
    except SelectionValidationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

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
    except StarterRequiredError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except RerunNotAllowedError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except SelectionValidationError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc)},
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
    response_model=IssueHistoryPage,
)
async def get_issue_history_endpoint(
    crawl_id: uuid.UUID,
    site_url_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> IssueHistoryPage:
    try:
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
    return items, truncated


def _validate_view(view: str) -> str:
    if view not in EXPORT_VIEWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown export view: {view}",
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
