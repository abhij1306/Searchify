# Site Health read-model + mutation service (Slice 6, workspace-safe).
#
# Owns every workspace-scoped projection the Site Health API exposes: the
# entitlement view, crawl summaries/list, keyset inventory, monitored set, page
# summaries/detail, grouped issues + issue detail + per-URL issue history, the
# dashboard, event replay, and the atomic crawl cancel. It is the single place
# the plan's projection rules live:
#
#   - model aliases: ``random_seed -> seed``, count aliases,
#     ``rule_catalog_version -> rule_version``;
#   - grouped-issue / evaluation ``title`` reads the CURRENT
#     ``SITE_HEALTH_RULES_BY_ID[rule_id].display_label`` (unknown -> rule_id);
#   - the grouped-issue canonical id is the earliest immutable ``SiteIssue`` UUID
#     by ``(created_at, id)`` (never a synthetic id);
#   - ``blocked`` = the latest analyze task ended under a config-owned policy
#     denial code (robots/SSRF); any other terminal-unsuccessful analysis maps to
#     ``error``; ``failed`` is internal and never surfaced as page copy;
#   - a Free workspace (``count_disclosure`` False) never sees a discovered/total
#     count (redacted to ``None``).
#
# Every lookup is filtered by the resolved workspace, so a foreign / missing id
# is a 404 (never a cross-workspace leak). Reuses the ``planner`` /
# ``selection`` / ``entitlements`` / ``state_events`` domain helpers directly.
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.site_health import (
    ANALYSIS_STATUS_CANCELLED,
    CRAWL_STATUS_CANCELLED,
    CRAWL_TERMINAL_STATUSES,
    DISCOVERY_STATUS_CANCELLED,
    EVENT_CRAWL_CANCELLED,
    PAGE_ANALYSIS_STATUS_COMPLETED,
    PAGE_ANALYSIS_STATUS_PARTIALLY_COMPLETED,
    POLICY_BLOCKING_ERROR_CODES,
    RULE_DIMENSIONS,
    SCORING_VERSION,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_INFO,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SITE_HEALTH_RULES_BY_ID,
    TASK_KIND_ANALYZE,
    capability_profile,
)
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_LEASED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
)
from app.domain.site_health.entitlements import resolve_entitlement
from app.domain.site_health.normalization import (
    CursorScopeError,
    decode_keyset_cursor,
    encode_keyset_cursor,
)
from app.domain.site_health.snapshot import persist_crawl_snapshot
from app.domain.site_health.state_events import (
    apply_analysis_status,
    apply_crawl_status,
    apply_discovery_status,
    record_crawl_event,
)
from app.models.project import Project
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteCrawlEvent,
    SiteCrawlTask,
    SiteFetchArtifact,
    SiteHealthProfile,
    SiteIssue,
    SiteLinkReference,
    SitePageAnalysis,
    SiteRuleEvaluation,
    SiteUrl,
    SiteUrlObservation,
    WorkspaceSiteHealthEntitlement,
)

__all__ = [
    "SiteHealthNotFoundError",
    "InvalidCursorError",
    "get_entitlement_view",
    "get_crawl_summary",
    "list_crawls",
    "cancel_crawl",
    "get_inventory",
    "get_monitored_set",
    "get_pages",
    "get_page_detail",
    "get_issues",
    "get_issue_detail",
    "get_issue_history",
    "get_dashboard",
    "load_events",
    "load_crawl_for_stream",
    "presentation_status_for",
]

# Deterministic severity ordering (critical worst). Used for the grouped-issue
# keyset sort and the issues summary rollup.
_SEVERITY_RANK: dict[str, int] = {
    SEVERITY_CRITICAL: 0,
    SEVERITY_HIGH: 1,
    SEVERITY_MEDIUM: 2,
    SEVERITY_LOW: 3,
    SEVERITY_INFO: 4,
}
_SEVERITY_ORDER = (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    SEVERITY_LOW,
    SEVERITY_INFO,
)

_MAX_PAGE_LIMIT = 200
_DEFAULT_PAGE_LIMIT = 50


class SiteHealthNotFoundError(Exception):
    """A workspace-scoped resource was missing / foreign (maps to 404)."""


class InvalidCursorError(Exception):
    """A cursor was tampered with or replayed cross-scope (maps to 400)."""


def _clamp_limit(limit: int | None) -> int:
    if limit is None:
        return _DEFAULT_PAGE_LIMIT
    return max(1, min(int(limit), _MAX_PAGE_LIMIT))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def display_label_for(rule_id: str) -> str:
    """Current human-facing catalog title for a rule id (unknown -> rule_id)."""
    rule = SITE_HEALTH_RULES_BY_ID.get(rule_id)
    return rule.display_label if rule is not None else rule_id


# =========================================================================
# Crawl projection (model aliases -> strict contract)
# =========================================================================
def _crawl_count_disclosure(crawl: SiteCrawl) -> bool:
    """Whether this crawl may disclose total/discovered counts (Free = never).

    Reads the frozen ``configuration.count_disclosure`` snapshot first (so a
    later capability change never retroactively reveals a sample crawl's
    counts); falls back to the capability profile derived from the frozen
    ``capability`` key.
    """
    config = crawl.configuration or {}
    if "count_disclosure" in config:
        return bool(config.get("count_disclosure"))
    capability = str(config.get("capability") or "")
    return capability_profile(capability).count_disclosure


def _score_summary(crawl: SiteCrawl) -> dict | None:
    """Project the worker-written ``score_summary`` into the strict shape."""
    summary = crawl.score_summary or None
    if not summary:
        return None
    return {
        "overall_score": summary.get("overall_score"),
        "technical_score": summary.get("technical_score"),
        "aeo_score": summary.get("aeo_score"),
        "selected_count": int(summary.get("selected_count", 0) or 0),
        "analyzed_count": int(
            summary.get("analyzed_count", summary.get("analyzed_url_count", 0)) or 0
        ),
        "issue_count": int(summary.get("issue_count", 0) or 0),
        "scoring_version": str(
            summary.get("scoring_version") or crawl.scoring_version or SCORING_VERSION
        ),
    }


def project_crawl(crawl: SiteCrawl) -> dict:
    """Project a ``SiteCrawl`` to the strict crawl contract (with redaction).

    Aliases model columns to the contract (``random_seed -> seed``,
    ``admitted_url_count -> visible_url_count``, ``analyzed_url_count ->
    analyzed_count``, ``failed_url_count -> failed_count``,
    ``rule_catalog_version -> rule_version``). For a Free (non-disclosing)
    crawl the discovered/total/has-more fields are ``None`` so no full-site
    count ever leaks.
    """
    disclose = _crawl_count_disclosure(crawl)
    return {
        "id": crawl.id,
        "workspace_id": crawl.workspace_id,
        "project_id": crawl.project_id,
        "profile_id": crawl.profile_id,
        "status": crawl.status,
        "discovery_status": crawl.discovery_status,
        "analysis_status": crawl.analysis_status,
        "root_url": crawl.root_url,
        "sample_mode": crawl.sample_mode,
        "seed": crawl.random_seed,
        "inventory_complete": crawl.inventory_complete,
        "visible_url_count": int(crawl.admitted_url_count or 0),
        "analyzed_count": int(crawl.analyzed_url_count or 0),
        "failed_count": int(crawl.failed_url_count or 0),
        "discovered_count": (
            int(crawl.discovered_url_count or 0) if disclose else None
        ),
        "total_url_count": (
            int(crawl.discovered_url_count or 0)
            if (disclose and crawl.inventory_complete)
            else None
        ),
        "has_more_site_urls": ((not crawl.inventory_complete) if disclose else None),
        "score_summary": _score_summary(crawl),
        "extractor_version": crawl.extractor_version,
        "analyzer_version": crawl.analyzer_version,
        "rule_version": crawl.rule_catalog_version,
        "scoring_version": crawl.scoring_version,
        "error_message": crawl.error_message or "",
        "created_at": _iso(crawl.created_at),
        "updated_at": _iso(crawl.updated_at),
        "started_at": _iso(crawl.started_at),
        "completed_at": _iso(crawl.completed_at),
    }


async def _load_crawl(
    session: AsyncSession, *, workspace_id: uuid.UUID, crawl_id: uuid.UUID
) -> SiteCrawl:
    crawl = await session.scalar(
        select(SiteCrawl).where(
            SiteCrawl.id == crawl_id,
            SiteCrawl.workspace_id == workspace_id,
        )
    )
    if crawl is None:
        raise SiteHealthNotFoundError("Crawl not found")
    return crawl


async def _load_project(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    project = await session.scalar(
        select(Project).where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        )
    )
    if project is None:
        raise SiteHealthNotFoundError("Project not found")
    return project


def _admitted_site_url_subquery(crawl_id: uuid.UUID):
    """Scalar subquery of ``site_url_id`` admitted to (observed in) a crawl.

    A URL is "in" a crawl iff the discover worker wrote a
    ``SiteUrlObservation`` row for ``(crawl_id, site_url_id)`` (append-only
    admission provenance, unique per pair). Scoping ``SiteUrl`` queries through
    this set means a later (e.g. downgraded / different) crawl of the same
    project can only ever surface the URLs THAT crawl actually admitted — a
    Free sample crawl never exposes a prior Starter crawl's fuller catalog.
    """
    return (
        select(SiteUrlObservation.site_url_id)
        .where(SiteUrlObservation.crawl_id == crawl_id)
        .scalar_subquery()
    )


# =========================================================================
# Entitlement view
# =========================================================================
async def get_entitlement_view(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> dict:
    """Project the workspace entitlement into the strict entitlement contract.

    Derives ``access_mode`` (Starter selects a monitored set; Free gets a
    server sample) and ``can_view_discovered_total`` from the live capability
    profile. Seeds a Free row on first use (fail-closed).
    """
    row: WorkspaceSiteHealthEntitlement = await resolve_entitlement(
        session, workspace_id
    )
    profile = capability_profile(row.plan_key)
    access_mode = "selection" if profile.allows_user_selection else "sample"
    return {
        "workspace_id": row.workspace_id,
        "plan_key": profile.capability,
        "access_mode": access_mode,
        "sample_url_limit": int(row.sample_url_limit),
        "monitored_url_limit": int(row.monitored_url_limit),
        "can_view_discovered_total": bool(row.count_disclosure),
        "capability_revision": int(row.capability_revision),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


# =========================================================================
# Crawl summary / list
# =========================================================================
async def get_crawl_summary(
    session: AsyncSession, *, workspace_id: uuid.UUID, crawl_id: uuid.UUID
) -> dict:
    crawl = await _load_crawl(session, workspace_id=workspace_id, crawl_id=crawl_id)
    return project_crawl(crawl)


async def list_crawls(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID | None,
    limit: int | None,
    cursor: str | None,
) -> dict:
    """List crawls ordered ``(created_at DESC, id DESC)`` with a keyset cursor."""
    limit = _clamp_limit(limit)
    scope = "crawls"
    filters = {"project_id": str(project_id) if project_id else None}

    stmt = select(SiteCrawl).where(SiteCrawl.workspace_id == workspace_id)
    if project_id is not None:
        # Authorize the project so a foreign id is a 404, not an empty page.
        await _load_project(session, workspace_id=workspace_id, project_id=project_id)
        stmt = stmt.where(SiteCrawl.project_id == project_id)

    if cursor:
        cur_created, cur_id = _decode_created_id_keyset(
            cursor, scope=scope, filters=filters
        )
        # (created_at, id) DESC keyset: rows strictly "older" than the cursor.
        stmt = stmt.where(
            or_(
                SiteCrawl.created_at < cur_created,
                and_(
                    SiteCrawl.created_at == cur_created,
                    SiteCrawl.id < cur_id,
                ),
            )
        )

    stmt = stmt.order_by(SiteCrawl.created_at.desc(), SiteCrawl.id.desc()).limit(
        limit + 1
    )
    rows = list((await session.scalars(stmt)).all())

    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_keyset_cursor(
            scope=scope,
            filters=filters,
            sort_values=[last.created_at.isoformat(), str(last.id)],
        )
    return {
        "items": [project_crawl(row) for row in rows],
        "next_cursor": next_cursor,
    }


# =========================================================================
# Cancel (atomic)
# =========================================================================
async def cancel_crawl(
    session: AsyncSession, *, workspace_id: uuid.UUID, crawl_id: uuid.UUID
) -> dict:
    """Cancel a crawl atomically: transition states, cancel tasks, record event.

    Locks the crawl row ``FOR UPDATE``, drives the overall/discovery/analysis
    sub-states to ``cancelled`` where the guarded machine allows it, cancels
    every non-terminal ``SiteCrawlTask``, records a ``crawl.cancelled`` event
    (payload redacted for Free), and commits. Cancelling an already-terminal
    crawl is idempotent (no-op transition, still returns the current summary).
    """
    locked = await session.execute(
        select(SiteCrawl)
        .where(
            SiteCrawl.id == crawl_id,
            SiteCrawl.workspace_id == workspace_id,
        )
        .with_for_update()
    )
    crawl = locked.scalar_one_or_none()
    if crawl is None:
        raise SiteHealthNotFoundError("Crawl not found")

    if crawl.status in CRAWL_TERMINAL_STATUSES:
        return project_crawl(crawl)

    apply_crawl_status(crawl, CRAWL_STATUS_CANCELLED)
    # Discovery / analysis sub-states are cancelled only from a non-terminal
    # state (the guarded machine keeps a completed sub-state as-is).
    try:
        apply_discovery_status(crawl, DISCOVERY_STATUS_CANCELLED)
    except Exception:
        pass
    try:
        apply_analysis_status(crawl, ANALYSIS_STATUS_CANCELLED)
    except Exception:
        pass
    crawl.completed_at = func.now()

    # Cancel every non-terminal task for this crawl (queued/leased/running/
    # retry). Succeeded/failed/cancelled tasks keep their immutable evidence.
    await session.execute(
        update(SiteCrawlTask)
        .where(
            SiteCrawlTask.crawl_id == crawl_id,
            SiteCrawlTask.status.notin_(
                [
                    TASK_STATUS_SUCCEEDED,
                    TASK_STATUS_FAILED,
                    TASK_STATUS_CANCELLED,
                ]
            ),
        )
        .values(
            status=TASK_STATUS_CANCELLED,
            lease_owner=None,
            lease_expires_at=None,
            completed_at=func.now(),
            error_code="cancelled",
        )
    )

    # Cancellation-time snapshot: if the run already produced completed
    # analyses for ACTIVE monitored URLs, roll them up into the SAME canonical
    # crawl snapshot the worker writes on clean terminalization (one shared
    # algorithm, no duplication). This makes ``score_summary`` non-null so the
    # frontend keeps the dashboard (partial scores + inventory), labels the run
    # Cancelled, and offers Recrawl — instead of hiding results behind a null
    # summary. ``persist_crawl_snapshot`` decides from its single fetched
    # aggregate row set: when nothing aggregable exists (no active completed
    # analyses — including a completed analysis whose monitored URL was since
    # deactivated) it writes neither the snapshot nor the projection and returns
    # ``False``, so the summary stays null (never a fabricated zero) and the UI
    # shows its terminal / selection state. No separate precheck — that would be
    # a TOCTOU race against membership/analysis changes.
    await persist_crawl_snapshot(session, crawl=crawl)

    record_crawl_event(
        session,
        crawl_id=crawl.id,
        event_type=EVENT_CRAWL_CANCELLED,
        message="crawl cancelled",
        count_disclosure=_crawl_count_disclosure(crawl),
    )
    await session.commit()
    refreshed = await _load_crawl(session, workspace_id=workspace_id, crawl_id=crawl_id)
    return project_crawl(refreshed)


# =========================================================================
# Presentation status derivation (plan projection rules)
# =========================================================================
def presentation_status_for(
    *,
    analysis: SitePageAnalysis | None,
    monitored: bool,
    latest_analyze_task: SiteCrawlTask | None,
) -> tuple[str, str]:
    """Derive the mockup-facing ``(analysis_status, error_code)`` for a URL.

    Rules (plan §Projection):
      - a completed analysis -> its persisted status (``completed`` /
        ``partially_completed``);
      - no analysis + the latest analyze task ended under a policy denial code
        (robots/SSRF) -> ``blocked`` (with the error code);
      - no analysis + any other terminal-unsuccessful analyze task -> ``error``;
      - an in-flight analyze task -> ``pending`` / ``running``;
      - a monitored URL with no analyze task yet -> ``pending``;
      - an un-monitored URL with nothing -> ``not_selected``.
    ``failed`` is never surfaced as page copy (it maps to ``error``/``blocked``).
    """
    if analysis is not None and analysis.status in (
        PAGE_ANALYSIS_STATUS_COMPLETED,
        PAGE_ANALYSIS_STATUS_PARTIALLY_COMPLETED,
    ):
        return analysis.status, ""

    task = latest_analyze_task
    if task is not None:
        if task.status == TASK_STATUS_CANCELLED:
            return "cancelled", task.error_code or ""
        if task.status == TASK_STATUS_FAILED:
            code = task.error_code or ""
            if code in POLICY_BLOCKING_ERROR_CODES:
                return "blocked", code
            return "error", code
        if task.status == TASK_STATUS_SUCCEEDED:
            # Succeeded fetch but no completed analysis row yet: still resolving.
            return "pending", ""
        # queued / leased / running / retry_wait -> in-flight.
        if task.status in (TASK_STATUS_RUNNING, TASK_STATUS_LEASED):
            return "running", ""
        return "pending", ""

    if monitored:
        return "pending", ""
    return "not_selected", ""


async def _monitored_site_url_ids(
    session: AsyncSession, *, project_id: uuid.UUID
) -> set[uuid.UUID]:
    """Set of ACTIVE monitored ``site_url_id`` for a project."""
    rows = await session.execute(
        select(MonitoredSiteUrl.site_url_id).where(
            MonitoredSiteUrl.project_id == project_id,
            MonitoredSiteUrl.active.is_(True),
        )
    )
    return {row[0] for row in rows.all()}


async def _latest_analysis_by_site_url(
    session: AsyncSession,
    *,
    crawl_id: uuid.UUID,
    site_url_ids: list[uuid.UUID],
) -> dict[uuid.UUID, SitePageAnalysis]:
    """Latest ``SitePageAnalysis`` per site_url within a crawl (by created_at)."""
    if not site_url_ids:
        return {}
    rows = await session.execute(
        select(SitePageAnalysis)
        .where(
            SitePageAnalysis.crawl_id == crawl_id,
            SitePageAnalysis.site_url_id.in_(site_url_ids),
        )
        .order_by(
            SitePageAnalysis.site_url_id,
            SitePageAnalysis.created_at.asc(),
            SitePageAnalysis.id.asc(),
        )
    )
    latest: dict[uuid.UUID, SitePageAnalysis] = {}
    for analysis in rows.scalars().all():
        latest[analysis.site_url_id] = analysis  # last wins = newest
    return latest


async def _latest_analyze_task_by_site_url(
    session: AsyncSession,
    *,
    crawl_id: uuid.UUID,
    site_url_ids: list[uuid.UUID],
) -> dict[uuid.UUID, SiteCrawlTask]:
    """Latest ``analyze`` task per site_url within a crawl (by generation)."""
    if not site_url_ids:
        return {}
    rows = await session.execute(
        select(SiteCrawlTask)
        .where(
            SiteCrawlTask.crawl_id == crawl_id,
            SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
            SiteCrawlTask.site_url_id.in_(site_url_ids),
        )
        .order_by(
            SiteCrawlTask.site_url_id,
            SiteCrawlTask.generation.asc(),
            SiteCrawlTask.created_at.asc(),
        )
    )
    latest: dict[uuid.UUID, SiteCrawlTask] = {}
    for task in rows.scalars().all():
        if task.site_url_id is not None:
            latest[task.site_url_id] = task  # last wins = newest generation
    return latest


async def _issue_counts_by_site_url(
    session: AsyncSession,
    *,
    crawl_id: uuid.UUID,
    site_url_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """Count of persisted issues per site_url within a crawl."""
    if not site_url_ids:
        return {}
    rows = await session.execute(
        select(SiteIssue.site_url_id, func.count())
        .where(
            SiteIssue.crawl_id == crawl_id,
            SiteIssue.site_url_id.in_(site_url_ids),
        )
        .group_by(SiteIssue.site_url_id)
    )
    return {row[0]: int(row[1]) for row in rows.all()}


# =========================================================================
# Inventory (keyset (normalized_url, id) over SiteUrl)
# =========================================================================
async def get_inventory(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    crawl_id: uuid.UUID,
    limit: int | None,
    cursor: str | None,
    query: str | None = None,
    status: str | None = None,
    monitored: bool | None = None,
) -> dict:
    """Keyset inventory for a crawl's project, ordered ``(normalized_url, id)``.

    Filters by substring ``query`` (normalized/display url), a per-URL
    presentation ``status``, and the ``monitored`` flag. The cursor is bound to
    the endpoint + filter fingerprint so a filter change invalidates it. Nullable
    latest-analysis summaries are attached per row.
    """
    crawl = await _load_crawl(session, workspace_id=workspace_id, crawl_id=crawl_id)
    limit = _clamp_limit(limit)
    project_id = crawl.project_id
    scope = "inventory"
    filters = {
        "crawl_id": str(crawl_id),
        "query": (query or "").strip().lower() or None,
        "status": status or None,
        "monitored": (str(monitored) if monitored is not None else None),
    }

    # Scope to URLs THIS crawl admitted (observed), not the project's
    # historical catalog: a later Free/downgraded crawl can only surface the
    # URLs it actually admitted, never a prior Starter crawl's fuller set.
    stmt = select(SiteUrl).where(
        SiteUrl.project_id == project_id,
        SiteUrl.id.in_(_admitted_site_url_subquery(crawl_id)),
    )
    if query:
        pattern = f"%{query.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(SiteUrl.normalized_url).like(pattern),
                func.lower(SiteUrl.display_url).like(pattern),
            )
        )

    monitored_ids = await _monitored_site_url_ids(session, project_id=project_id)
    if monitored is True:
        if not monitored_ids:
            return {"items": [], "next_cursor": None}
        stmt = stmt.where(SiteUrl.id.in_(list(monitored_ids)))
    elif monitored is False and monitored_ids:
        stmt = stmt.where(SiteUrl.id.notin_(list(monitored_ids)))

    if cursor:
        cur_url, cur_id = _decode_url_keyset(cursor, scope=scope, filters=filters)
        stmt = stmt.where(
            tuple_(SiteUrl.normalized_url, SiteUrl.id) > (cur_url, cur_id)
        )

    # Over-fetch so a status filter (applied in Python from the derived
    # presentation status) can still return a full page.
    fetch = limit + 1
    fetch_size = fetch if status is None else fetch * 4
    stmt = stmt.order_by(SiteUrl.normalized_url.asc(), SiteUrl.id.asc()).limit(
        fetch_size
    )
    rows = list((await session.scalars(stmt)).all())

    site_ids = [r.id for r in rows]
    analyses = await _latest_analysis_by_site_url(
        session, crawl_id=crawl_id, site_url_ids=site_ids
    )
    tasks = await _latest_analyze_task_by_site_url(
        session, crawl_id=crawl_id, site_url_ids=site_ids
    )
    issue_counts = await _issue_counts_by_site_url(
        session, crawl_id=crawl_id, site_url_ids=site_ids
    )

    items: list[dict] = []
    last_scanned: SiteUrl | None = None
    for row in rows:
        last_scanned = row
        analysis = analyses.get(row.id)
        pres_status, _code = presentation_status_for(
            analysis=analysis,
            monitored=row.id in monitored_ids,
            latest_analyze_task=tasks.get(row.id),
        )
        if status is not None and pres_status != status:
            continue
        items.append(
            {
                "site_url_id": row.id,
                "normalized_url": row.normalized_url,
                "display_url": row.display_url or row.normalized_url,
                "title": row.latest_title or None,
                "content_type": row.latest_content_type or None,
                "source": row.latest_source_kind or None,
                "depth": row.depth,
                "monitored": row.id in monitored_ids,
                "first_seen_at": _iso(row.first_seen_at),
                "last_seen_at": _iso(row.last_seen_at),
                "issue_count": (
                    issue_counts.get(row.id, 0) if analysis is not None else None
                ),
                "technical_score": (
                    analysis.technical_score if analysis is not None else None
                ),
                "aeo_score": (analysis.aeo_score if analysis is not None else None),
                "overall_score": (
                    analysis.overall_score if analysis is not None else None
                ),
                "last_audited": (
                    _iso(analysis.finalized_at) if analysis is not None else None
                ),
            }
        )
        if len(items) >= limit + 1:
            break

    next_cursor: str | None = None
    if len(items) > limit:
        items = items[:limit]
        last_kept = items[-1]
        next_cursor = encode_keyset_cursor(
            scope=scope,
            filters=filters,
            sort_values=[
                last_kept["normalized_url"],
                str(last_kept["site_url_id"]),
            ],
        )
    elif status is not None and last_scanned is not None and len(rows) >= fetch_size:
        # A sparse status filter can leave a partial (or even empty) page while
        # more matching rows exist beyond the scanned window. We fetched a full
        # window, so emit a cursor at the last SCANNED row (not the last matched
        # one) to guarantee forward progress even when a window had no matches.
        next_cursor = encode_keyset_cursor(
            scope=scope,
            filters=filters,
            sort_values=[last_scanned.normalized_url, str(last_scanned.id)],
        )
    return {"items": items, "next_cursor": next_cursor}


def _decode_url_keyset(
    cursor: str, *, scope: str, filters: dict
) -> tuple[str, uuid.UUID]:
    # Any typed-cursor failure (scope/filter mismatch, tamper, or a malformed
    # id payload) becomes an InvalidCursorError so the router returns 400.
    try:
        url_raw, id_raw = decode_keyset_cursor(cursor, scope=scope, filters=filters)
        return url_raw, uuid.UUID(id_raw)
    except CursorScopeError as exc:
        raise InvalidCursorError(str(exc)) from exc
    except ValueError as exc:
        raise InvalidCursorError(str(exc)) from exc


def _decode_created_id_keyset(
    cursor: str, *, scope: str, filters: dict
) -> tuple[datetime, uuid.UUID]:
    """Decode a ``(created_at, id)`` keyset cursor (400 on any failure)."""
    try:
        created_raw, id_raw = decode_keyset_cursor(cursor, scope=scope, filters=filters)
        return datetime.fromisoformat(created_raw), uuid.UUID(id_raw)
    except CursorScopeError as exc:
        raise InvalidCursorError(str(exc)) from exc
    except ValueError as exc:
        raise InvalidCursorError(str(exc)) from exc


# =========================================================================
# Monitored set
# =========================================================================
async def get_monitored_set(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> dict:
    """Project's persistent monitored set + selection version + workspace quota."""
    await _load_project(session, workspace_id=workspace_id, project_id=project_id)
    profile = await session.scalar(
        select(SiteHealthProfile).where(SiteHealthProfile.project_id == project_id)
    )
    selection_version = int(profile.selection_version) if profile else 0

    rows = await session.execute(
        select(MonitoredSiteUrl, SiteUrl)
        .join(SiteUrl, SiteUrl.id == MonitoredSiteUrl.site_url_id)
        .where(MonitoredSiteUrl.project_id == project_id)
        .order_by(SiteUrl.normalized_url.asc(), SiteUrl.id.asc())
    )
    monitored_urls: list[dict] = []
    for membership, site_url in rows.all():
        monitored_urls.append(
            {
                "site_url_id": membership.site_url_id,
                "normalized_url": site_url.normalized_url,
                "display_url": site_url.display_url or site_url.normalized_url,
                "title": site_url.latest_title or None,
                "active": membership.active,
                "selection_source": membership.selection_source,
                "selected_at": _iso(membership.selected_at),
                "deselected_at": _iso(membership.deselected_at),
            }
        )

    entitlement = await resolve_entitlement(session, workspace_id)
    used = await session.scalar(
        select(func.count())
        .select_from(MonitoredSiteUrl)
        .where(
            MonitoredSiteUrl.workspace_id == workspace_id,
            MonitoredSiteUrl.active.is_(True),
        )
    )
    return {
        "project_id": project_id,
        "selection_version": selection_version,
        "monitored_urls": monitored_urls,
        "quota": {
            "used": int(used or 0),
            "limit": int(entitlement.monitored_url_limit),
        },
    }


# =========================================================================
# Pages (CursorPage<PageSummary> ordered (normalized_url, site_url_id))
# =========================================================================
# `error_or_blocked` is accepted as a combined presentation filter (mockup 710
# groups the two terminal-unsuccessful states).
_ERROR_OR_BLOCKED = "error_or_blocked"


def _matches_page_status(pres_status: str, wanted: str | None) -> bool:
    if wanted is None:
        return True
    if wanted == _ERROR_OR_BLOCKED:
        return pres_status in ("error", "blocked")
    return pres_status == wanted


async def get_pages(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    crawl_id: uuid.UUID,
    limit: int | None,
    cursor: str | None,
    status: str | None = None,
    monitored: bool | None = None,
) -> dict:
    """Analyzed-page summaries for a crawl, ordered ``(normalized_url, id)``.

    Accepts an exact presentation ``status`` or the combined ``error_or_blocked``
    filter, plus a ``monitored`` toggle. Status + monitored are part of the
    cursor fingerprint. Rows are the crawl's project ``SiteUrl`` set, projected
    with the latest analysis and derived presentation status.
    """
    crawl = await _load_crawl(session, workspace_id=workspace_id, crawl_id=crawl_id)
    limit = _clamp_limit(limit)
    project_id = crawl.project_id
    scope = "pages"
    filters = {
        "crawl_id": str(crawl_id),
        "status": status or None,
        "monitored": (str(monitored) if monitored is not None else None),
    }

    monitored_ids = await _monitored_site_url_ids(session, project_id=project_id)
    # Scope to URLs admitted to THIS crawl (see `get_inventory`): a downgraded
    # / different later crawl never exposes a prior crawl's fuller URL set.
    stmt = select(SiteUrl).where(
        SiteUrl.project_id == project_id,
        SiteUrl.id.in_(_admitted_site_url_subquery(crawl_id)),
    )
    if monitored is True:
        if not monitored_ids:
            return {"items": [], "next_cursor": None}
        stmt = stmt.where(SiteUrl.id.in_(list(monitored_ids)))
    elif monitored is False and monitored_ids:
        stmt = stmt.where(SiteUrl.id.notin_(list(monitored_ids)))

    if cursor:
        cur_url, cur_id = _decode_url_keyset(cursor, scope=scope, filters=filters)
        stmt = stmt.where(
            tuple_(SiteUrl.normalized_url, SiteUrl.id) > (cur_url, cur_id)
        )

    fetch = limit + 1
    fetch_size = fetch if status is None else fetch * 4
    stmt = stmt.order_by(SiteUrl.normalized_url.asc(), SiteUrl.id.asc()).limit(
        fetch_size
    )
    rows = list((await session.scalars(stmt)).all())

    site_ids = [r.id for r in rows]
    analyses = await _latest_analysis_by_site_url(
        session, crawl_id=crawl_id, site_url_ids=site_ids
    )
    tasks = await _latest_analyze_task_by_site_url(
        session, crawl_id=crawl_id, site_url_ids=site_ids
    )
    issue_counts = await _issue_counts_by_site_url(
        session, crawl_id=crawl_id, site_url_ids=site_ids
    )

    items: list[dict] = []
    last_scanned: SiteUrl | None = None
    for row in rows:
        analysis = analyses.get(row.id)
        pres_status, error_code = presentation_status_for(
            analysis=analysis,
            monitored=row.id in monitored_ids,
            latest_analyze_task=tasks.get(row.id),
        )
        last_scanned = row
        if not _matches_page_status(pres_status, status):
            continue
        items.append(
            {
                "site_url_id": row.id,
                "crawl_id": crawl_id,
                "normalized_url": row.normalized_url,
                "display_url": row.display_url or row.normalized_url,
                "title": row.latest_title or None,
                "monitored": row.id in monitored_ids,
                "analysis_status": pres_status,
                "error_code": error_code,
                "issue_count": (
                    issue_counts.get(row.id, 0) if analysis is not None else None
                ),
                "technical_score": (
                    analysis.technical_score if analysis is not None else None
                ),
                "aeo_score": (analysis.aeo_score if analysis is not None else None),
                "overall_score": (
                    analysis.overall_score if analysis is not None else None
                ),
                "last_audited": (
                    _iso(analysis.finalized_at) if analysis is not None else None
                ),
            }
        )
        if len(items) >= limit + 1:
            break

    next_cursor: str | None = None
    if len(items) > limit:
        items = items[:limit]
        last_kept = items[-1]
        next_cursor = encode_keyset_cursor(
            scope=scope,
            filters=filters,
            sort_values=[
                last_kept["normalized_url"],
                str(last_kept["site_url_id"]),
            ],
        )
    elif status is not None and last_scanned is not None and len(rows) >= fetch_size:
        # Sparse status filter: a full window yielded a partial/empty page while
        # more matching rows may exist. Advance the cursor to the last SCANNED
        # row so traversal keeps making progress across empty windows.
        next_cursor = encode_keyset_cursor(
            scope=scope,
            filters=filters,
            sort_values=[last_scanned.normalized_url, str(last_scanned.id)],
        )
    return {"items": items, "next_cursor": next_cursor}


# =========================================================================
# Page detail (persisted facts/delivery/scores/issues/provenance; no network)
# =========================================================================
def _page_facts(facts: dict | None) -> dict:
    facts = facts or {}
    robots = facts.get("robots") or {}
    directives: list[str] = []
    if robots.get("noindex"):
        directives.append("noindex")
    if robots.get("nofollow"):
        directives.append("nofollow")
    headings = facts.get("headings") or {}
    images = facts.get("images") or {}
    body = facts.get("body") or {}
    structured = facts.get("structured_data") or {}
    links = facts.get("links") or {}
    anchors = links.get("anchors") or []
    internal = sum(1 for a in anchors if a.get("is_internal"))
    external = len(anchors) - internal
    heading_counts = headings.get("counts") or {}
    heading_total = sum(int(v or 0) for v in heading_counts.values())
    return {
        "title": facts.get("title") or None,
        "meta_description": facts.get("meta_description") or None,
        "canonical_url": facts.get("canonical_url") or None,
        "robots_directives": directives,
        "h1_count": int(headings.get("h1_count", 0) or 0),
        "heading_count": int(heading_total),
        "image_count": int(images.get("count", 0) or 0),
        "image_missing_alt_count": int(images.get("missing_alt", 0) or 0),
        "word_count": int(body.get("word_count", 0) or 0),
        "internal_link_count": int(internal),
        "external_link_count": int(external),
        "structured_data_types": list(structured.get("types") or []),
    }


def _delivery_facts(facts: dict | None, *, html_bytes: int | None) -> dict:
    facts = facts or {}
    delivery = facts.get("delivery") or {}
    blocking = facts.get("blocking_resources") or {}
    compression = delivery.get("content_encoding") or None
    return {
        "field_cwv_available": False,
        "status_code": delivery.get("status_code"),
        "ttfb_ms": delivery.get("ttfb_ms"),
        "wire_bytes": delivery.get("wire_bytes"),
        "decoded_bytes": delivery.get("decoded_bytes"),
        "html_bytes": html_bytes,
        "http_version": delivery.get("http_version") or None,
        "compression": compression,
        "cache_control": (delivery.get("cache_control") or None),
        "blocking_resource_count": (
            int(blocking.get("total", 0)) if blocking else None
        ),
    }


# Bound the exact evidence/link projections so a pathological artifact can
# never balloon a detail response (plan §Projection: bounded evidence/links).
_MAX_EVALUATIONS = 200
_MAX_LINK_REFERENCES = 200


def _evaluation_row(evaluation: SiteRuleEvaluation) -> dict:
    """Project one persisted rule evaluation with the CURRENT display label."""
    return {
        "id": evaluation.id,
        "rule_id": evaluation.rule_id,
        "title": display_label_for(evaluation.rule_id),
        "dimension": evaluation.dimension,
        "category": evaluation.category,
        "severity": evaluation.severity,
        "outcome": evaluation.outcome,
        "weight": evaluation.weight,
        "evidence": evaluation.evidence or {},
        "analyzer_version": evaluation.analyzer_version,
        "rule_version": evaluation.rule_version,
        "created_at": _iso(evaluation.created_at),
    }


def _link_reference_row(link: SiteLinkReference) -> dict:
    """Project one deduplicated link reference (target status where known)."""
    return {
        "id": link.id,
        "kind": link.kind,
        "target_url": link.target_url,
        "is_internal": link.is_internal,
        "rel": link.rel or "",
        "anchor_text": link.anchor_text or "",
        "target_artifact_id": link.target_artifact_id,
    }


def _issue_row(issue: SiteIssue, affected_count: int) -> dict:
    return {
        "id": issue.id,
        "crawl_id": issue.crawl_id,
        "rule_id": issue.rule_id,
        "dimension": issue.dimension,
        "category": issue.category,
        "severity": issue.severity,
        "title": display_label_for(issue.rule_id),
        "remediation": issue.remediation or "",
        "affected_url_count": affected_count,
        "analyzer_version": issue.analyzer_version,
        "rule_version": issue.rule_version,
        "created_at": _iso(issue.created_at),
    }


async def get_page_detail(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    crawl_id: uuid.UUID,
    site_url_id: uuid.UUID,
) -> dict:
    """Full per-URL detail from persisted rows only (never a network call)."""
    crawl = await _load_crawl(session, workspace_id=workspace_id, crawl_id=crawl_id)
    # Only a URL admitted to THIS crawl has a detail here (404 otherwise), so a
    # detail request can never surface a URL the crawl did not observe.
    site_url = await session.scalar(
        select(SiteUrl).where(
            SiteUrl.id == site_url_id,
            SiteUrl.project_id == crawl.project_id,
            SiteUrl.id.in_(_admitted_site_url_subquery(crawl_id)),
        )
    )
    if site_url is None:
        raise SiteHealthNotFoundError("Site URL not found")

    monitored_ids = await _monitored_site_url_ids(session, project_id=crawl.project_id)
    analyses = await _latest_analysis_by_site_url(
        session, crawl_id=crawl_id, site_url_ids=[site_url_id]
    )
    analysis = analyses.get(site_url_id)
    tasks = await _latest_analyze_task_by_site_url(
        session, crawl_id=crawl_id, site_url_ids=[site_url_id]
    )
    pres_status, error_code = presentation_status_for(
        analysis=analysis,
        monitored=site_url_id in monitored_ids,
        latest_analyze_task=tasks.get(site_url_id),
    )

    facts: dict | None = None
    artifact_id: uuid.UUID | None = None
    html_bytes: int | None = None
    if analysis is not None:
        artifact = await session.get(SiteFetchArtifact, analysis.artifact_id)
        if artifact is not None:
            facts = artifact.normalized_facts
            artifact_id = artifact.id
            html_bytes = artifact.decoded_bytes

    issues: list[dict] = []
    evaluations: list[dict] = []
    link_references: list[dict] = []
    if analysis is not None:
        issue_rows = await session.execute(
            select(SiteIssue)
            .where(SiteIssue.analysis_id == analysis.id)
            .order_by(SiteIssue.created_at.asc(), SiteIssue.id.asc())
        )
        issues = [_issue_row(i, 1) for i in issue_rows.scalars().all()]

        # ALL persisted rule evaluations for this analysis, worst severity
        # first (then rule_id) so the current/failing rules lead the list.
        eval_rows = await session.execute(
            select(SiteRuleEvaluation)
            .where(SiteRuleEvaluation.analysis_id == analysis.id)
            .limit(_MAX_EVALUATIONS)
        )
        evaluations = sorted(
            (_evaluation_row(e) for e in eval_rows.scalars().all()),
            key=lambda r: (
                _SEVERITY_RANK.get(r["severity"], 99),
                r["rule_id"],
            ),
        )

        # Deduplicated link references for this analysis. Rows are already
        # deduped at write time by (artifact, kind, target_hash, fingerprint);
        # collapse defensively by (kind, target_hash) and order by target url.
        link_rows = await session.execute(
            select(SiteLinkReference)
            .where(SiteLinkReference.source_analysis_id == analysis.id)
            .order_by(
                SiteLinkReference.target_url.asc(),
                SiteLinkReference.id.asc(),
            )
        )
        seen_links: set[tuple[str, str]] = set()
        for link in link_rows.scalars().all():
            key = (link.kind, link.target_hash)
            if key in seen_links:
                continue
            seen_links.add(key)
            link_references.append(_link_reference_row(link))
            if len(link_references) >= _MAX_LINK_REFERENCES:
                break

    return {
        "site_url_id": site_url.id,
        "crawl_id": crawl_id,
        "normalized_url": site_url.normalized_url,
        "display_url": site_url.display_url or site_url.normalized_url,
        "title": site_url.latest_title or None,
        "analysis_status": pres_status,
        "error_code": error_code,
        "field_cwv_available": False,
        "technical_score": (analysis.technical_score if analysis is not None else None),
        "aeo_score": analysis.aeo_score if analysis is not None else None,
        "overall_score": (analysis.overall_score if analysis is not None else None),
        "issue_count": len(issues) if analysis is not None else None,
        "last_audited": (_iso(analysis.finalized_at) if analysis is not None else None),
        "facts": _page_facts(facts),
        "delivery": _delivery_facts(facts, html_bytes=html_bytes),
        "issues": issues,
        "evaluations": evaluations,
        "link_references": link_references,
        "artifact_id": artifact_id,
        "extractor_version": crawl.extractor_version,
        "analyzer_version": crawl.analyzer_version,
        "rule_version": crawl.rule_catalog_version,
        "scoring_version": crawl.scoring_version,
    }


# =========================================================================
# Grouped issues (mockup 710): group by (crawl_id, rule_id) after filters.
# =========================================================================
@dataclass
class _IssueGroup:
    rule_id: str
    dimension: str
    category: str
    severity: str
    canonical_id: uuid.UUID
    canonical_created_at: datetime
    affected_url_count: int
    remediation: str
    analyzer_version: str
    rule_version: str


def _issue_filter_clause(
    *,
    crawl_id: uuid.UUID,
    query: str | None,
    severity: str | None,
    category: str | None,
    dimension: str | None,
    rule: str | None,
    site_url_id: uuid.UUID | None,
):
    clauses = [SiteIssue.crawl_id == crawl_id]
    if severity:
        if severity == SEVERITY_HIGH:
            # The catalog UI exposes a three-tier vocabulary (high/medium/low);
            # ``critical`` folds into ``high`` so the High filter matches the
            # rows its chip count includes.
            clauses.append(SiteIssue.severity.in_([SEVERITY_HIGH, SEVERITY_CRITICAL]))
        else:
            clauses.append(SiteIssue.severity == severity)
    if category:
        clauses.append(SiteIssue.category == category)
    if dimension:
        clauses.append(SiteIssue.dimension == dimension)
    if rule:
        clauses.append(SiteIssue.rule_id == rule)
    if site_url_id is not None:
        clauses.append(SiteIssue.site_url_id == site_url_id)
    if query:
        clauses.append(SiteIssue.rule_id.ilike(f"%{query.strip()}%"))
    return clauses


async def _load_issue_groups(
    session: AsyncSession,
    *,
    crawl_id: uuid.UUID,
    clauses: list,
) -> list[_IssueGroup]:
    """Aggregate issues into per-rule groups (canonical id, distinct affected).

    Group aggregation happens in the query BEFORE keyset/limit: for each
    ``rule_id`` we take the earliest issue id by ``(created_at, id)`` as the
    canonical (immutable) id and count the DISTINCT affected ``site_url_id``.
    """
    rows = await session.execute(
        select(
            SiteIssue.rule_id,
            func.min(SiteIssue.dimension),
            func.min(SiteIssue.category),
            func.min(SiteIssue.severity),
            func.count(func.distinct(SiteIssue.site_url_id)),
            func.min(SiteIssue.created_at),
            func.min(SiteIssue.remediation),
            func.min(SiteIssue.analyzer_version),
            func.min(SiteIssue.rule_version),
        )
        .where(*clauses)
        .group_by(SiteIssue.rule_id)
    )
    groups: list[_IssueGroup] = []
    for row in rows.all():
        rule_id = row[0]
        # Resolve a STABLE canonical id: the earliest issue row for this
        # (crawl_id, rule_id) by (created_at, id), computed UNFILTERED so the
        # representative id never changes when a query/severity/URL filter is
        # applied (issue rows are immutable). MIN(created_at) alone is not
        # enough (ties), so pick the row explicitly.
        canonical = await session.scalar(
            select(SiteIssue)
            .where(
                SiteIssue.crawl_id == crawl_id,
                SiteIssue.rule_id == rule_id,
            )
            .order_by(SiteIssue.created_at.asc(), SiteIssue.id.asc())
            .limit(1)
        )
        if canonical is None:
            continue
        groups.append(
            _IssueGroup(
                rule_id=rule_id,
                dimension=canonical.dimension,
                category=canonical.category,
                severity=canonical.severity,
                canonical_id=canonical.id,
                canonical_created_at=canonical.created_at,
                affected_url_count=int(row[4]),
                remediation=canonical.remediation or "",
                analyzer_version=canonical.analyzer_version,
                rule_version=canonical.rule_version,
            )
        )
    # Deterministic sort: (severity_rank, rule_id, canonical_id).
    groups.sort(
        key=lambda g: (
            _SEVERITY_RANK.get(g.severity, 99),
            g.rule_id,
            str(g.canonical_id),
        )
    )
    return groups


async def _issues_summary(
    session: AsyncSession, *, crawl_id: uuid.UUID, clauses: list
) -> dict:
    """Crawl-level canonical-group/severity/dimension + distinct affected counts.

    Counts are DISTINCT RULE GROUPS (the canonical issue cards the catalog
    renders), not per-page occurrence rows: 6 issue types across 10 pages is
    "6 issues", matching what the user sees in the list. Per-page multiplicity
    is carried by each group's ``affected_url_count`` instead.
    """
    total = (
        await session.scalar(
            select(func.count(func.distinct(SiteIssue.rule_id)))
            .select_from(SiteIssue)
            .where(*clauses)
        )
        or 0
    )
    sev_rows = await session.execute(
        select(SiteIssue.severity, func.count(func.distinct(SiteIssue.rule_id)))
        .where(*clauses)
        .group_by(SiteIssue.severity)
    )
    severity_counts = {name: 0 for name in _SEVERITY_ORDER}
    for name, count in sev_rows.all():
        # Three-tier UI vocabulary (high/medium/low): ``critical`` folds into
        # ``high`` so the High chip count matches the High filter's row set
        # (which already matches high OR critical). ``critical`` stays 0.
        key = SEVERITY_HIGH if name == SEVERITY_CRITICAL else name
        severity_counts[key] = severity_counts.get(key, 0) + int(count)
    dim_rows = await session.execute(
        select(SiteIssue.dimension, func.count(func.distinct(SiteIssue.rule_id)))
        .where(*clauses)
        .group_by(SiteIssue.dimension)
    )
    dimension_counts = {name: 0 for name in sorted(RULE_DIMENSIONS)}
    for name, count in dim_rows.all():
        dimension_counts[name] = int(count)
    affected = (
        await session.scalar(
            select(func.count(func.distinct(SiteIssue.site_url_id))).where(*clauses)
        )
        or 0
    )
    # Distinct affected URLs that are also active monitored members.
    monitored_affected = (
        await session.scalar(
            select(func.count(func.distinct(SiteIssue.site_url_id)))
            .select_from(SiteIssue)
            .join(
                MonitoredSiteUrl,
                and_(
                    MonitoredSiteUrl.site_url_id == SiteIssue.site_url_id,
                    MonitoredSiteUrl.active.is_(True),
                ),
            )
            .where(*clauses)
        )
        or 0
    )
    return {
        "issue_count": int(total),
        "severity_counts": severity_counts,
        "dimension_counts": dimension_counts,
        "affected_url_count": int(affected),
        "monitored_affected_url_count": int(monitored_affected),
    }


async def get_issues(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    crawl_id: uuid.UUID,
    limit: int | None,
    cursor: str | None,
    query: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    dimension: str | None = None,
    rule: str | None = None,
    site_url_id: uuid.UUID | None = None,
) -> dict:
    """Grouped issue catalog (``{items, next_cursor, summary}``) for mockup 710.

    Groups by ``(crawl_id, rule_id)`` after filters, keysets by
    ``(severity_rank, rule_id, canonical_id)`` and applies ``limit + 1`` so a
    rule group is never split across pages. ``id`` is the canonical (earliest)
    issue UUID; ``title`` reads the CURRENT display label.
    """
    await _load_crawl(session, workspace_id=workspace_id, crawl_id=crawl_id)
    limit = _clamp_limit(limit)
    scope = "issues"
    filters = {
        "crawl_id": str(crawl_id),
        "query": (query or "").strip() or None,
        "severity": severity or None,
        "category": category or None,
        "dimension": dimension or None,
        "rule": rule or None,
        "site_url_id": str(site_url_id) if site_url_id else None,
    }
    clauses = _issue_filter_clause(
        crawl_id=crawl_id,
        query=query,
        severity=severity,
        category=category,
        dimension=dimension,
        rule=rule,
        site_url_id=site_url_id,
    )
    groups = await _load_issue_groups(session, crawl_id=crawl_id, clauses=clauses)

    start = 0
    if cursor:
        try:
            rank_raw, rule_raw, id_raw = decode_keyset_cursor(
                cursor, scope=scope, filters=filters
            )
            cursor_key = (int(rank_raw), rule_raw, id_raw)
        except CursorScopeError as exc:
            raise InvalidCursorError(str(exc)) from exc
        except ValueError as exc:
            raise InvalidCursorError(str(exc)) from exc
        for idx, g in enumerate(groups):
            gkey = (
                _SEVERITY_RANK.get(g.severity, 99),
                g.rule_id,
                str(g.canonical_id),
            )
            if gkey > cursor_key:
                start = idx
                break
        else:
            start = len(groups)

    window = groups[start : start + limit + 1]
    next_cursor: str | None = None
    if len(window) > limit:
        window = window[:limit]
        last = window[-1]
        next_cursor = encode_keyset_cursor(
            scope=scope,
            filters=filters,
            sort_values=[
                _SEVERITY_RANK.get(last.severity, 99),
                last.rule_id,
                str(last.canonical_id),
            ],
        )

    items = [
        {
            "id": g.canonical_id,
            "crawl_id": crawl_id,
            "rule_id": g.rule_id,
            "dimension": g.dimension,
            "category": g.category,
            "severity": g.severity,
            "title": display_label_for(g.rule_id),
            "remediation": g.remediation,
            "affected_url_count": g.affected_url_count,
            "analyzer_version": g.analyzer_version,
            "rule_version": g.rule_version,
            "created_at": _iso(g.canonical_created_at),
        }
        for g in window
    ]
    # The summary powers the tiles + filter-chip counts, so it is computed
    # WITHOUT the severity/dimension chip filters (but WITH search/rule/url
    # narrowing): selecting the "High" chip must not zero out the other
    # chips' counts or shrink the headline tiles.
    summary_clauses = _issue_filter_clause(
        crawl_id=crawl_id,
        query=query,
        severity=None,
        category=category,
        dimension=None,
        rule=rule,
        site_url_id=site_url_id,
    )
    summary = await _issues_summary(session, crawl_id=crawl_id, clauses=summary_clauses)
    return {"items": items, "next_cursor": next_cursor, "summary": summary}


async def get_issue_detail(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    crawl_id: uuid.UUID,
    canonical_id: uuid.UUID,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict:
    """Resolve a canonical issue then return its rule group + affected URLs.

    Affected URLs are ordered ``(normalized_url, site_url_id)`` and keyset-
    limited for navigation. Title reads the current display label; remediation/
    evidence/versions come from the persisted canonical row.
    """
    await _load_crawl(session, workspace_id=workspace_id, crawl_id=crawl_id)
    row = await session.scalar(
        select(SiteIssue).where(
            SiteIssue.id == canonical_id,
            SiteIssue.crawl_id == crawl_id,
            SiteIssue.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise SiteHealthNotFoundError("Issue not found")

    # Canonicalize to the stable representative row for the rule group (the
    # earliest issue by (created_at, id)) so a non-representative member id
    # resolves to the same group detail rather than a different projection.
    canonical = await session.scalar(
        select(SiteIssue)
        .where(
            SiteIssue.crawl_id == crawl_id,
            SiteIssue.rule_id == row.rule_id,
        )
        .order_by(SiteIssue.created_at.asc(), SiteIssue.id.asc())
        .limit(1)
    )
    if canonical is None:  # pragma: no cover - row proves at least one exists
        canonical = row

    limit = _clamp_limit(limit)
    scope = "issue_detail"
    # Fingerprint on the stable canonical id (not the requested member id) so a
    # non-representative id and its canonical share the same page identity.
    filters = {
        "crawl_id": str(crawl_id),
        "canonical_id": str(canonical.id),
    }

    total = (
        await session.scalar(
            select(func.count(func.distinct(SiteIssue.site_url_id))).where(
                SiteIssue.crawl_id == crawl_id,
                SiteIssue.rule_id == canonical.rule_id,
            )
        )
        or 0
    )

    # Distinct affected URLs, ordered (normalized_url, site_url_id).
    aff_stmt = (
        select(
            SiteUrl.id,
            SiteUrl.normalized_url,
            SiteUrl.display_url,
            SiteUrl.latest_title,
        )
        .join(SiteIssue, SiteIssue.site_url_id == SiteUrl.id)
        .where(
            SiteIssue.crawl_id == crawl_id,
            SiteIssue.rule_id == canonical.rule_id,
        )
        .distinct()
        .order_by(SiteUrl.normalized_url.asc(), SiteUrl.id.asc())
    )
    if cursor:
        cur_url, cur_id = _decode_url_keyset(cursor, scope=scope, filters=filters)
        aff_stmt = aff_stmt.where(
            tuple_(SiteUrl.normalized_url, SiteUrl.id) > (cur_url, cur_id)
        )
    aff_stmt = aff_stmt.limit(limit + 1)
    aff_rows = list((await session.execute(aff_stmt)).all())

    next_cursor: str | None = None
    if len(aff_rows) > limit:
        aff_rows = aff_rows[:limit]
        last = aff_rows[-1]
        next_cursor = encode_keyset_cursor(
            scope=scope,
            filters=filters,
            sort_values=[last[1], str(last[0])],
        )

    affected_urls = [
        {
            "site_url_id": row[0],
            "normalized_url": row[1],
            "display_url": row[2] or row[1],
            "title": row[3] or None,
        }
        for row in aff_rows
    ]
    return {
        "id": canonical.id,
        "crawl_id": crawl_id,
        "rule_id": canonical.rule_id,
        "dimension": canonical.dimension,
        "category": canonical.category,
        "severity": canonical.severity,
        "title": display_label_for(canonical.rule_id),
        "remediation": canonical.remediation or "",
        "evidence": canonical.evidence or {},
        "affected_urls": affected_urls,
        "affected_url_count": int(total),
        "analyzer_version": canonical.analyzer_version,
        "rule_version": canonical.rule_version,
        "created_at": _iso(canonical.created_at),
        "next_cursor": next_cursor,
    }


async def get_issue_history(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    crawl_id: uuid.UUID,
    site_url_id: uuid.UUID,
    limit: int | None,
    cursor: str | None,
) -> dict:
    """Per-URL issue history ordered ``(created_at DESC, id DESC)``.

    Uses the ``ix_site_issues_url_created`` index, bounded to the URL's project
    AND to crawls at or before the selected crawl in the project chronology, so
    an older crawl's detail never shows issues from a later crawl.
    """
    crawl = await _load_crawl(session, workspace_id=workspace_id, crawl_id=crawl_id)
    # The URL must be admitted to the selected crawl (404 otherwise), matching
    # the page-detail scope; history then spans that crawl and prior ones.
    site_url = await session.scalar(
        select(SiteUrl).where(
            SiteUrl.id == site_url_id,
            SiteUrl.project_id == crawl.project_id,
            SiteUrl.id.in_(_admitted_site_url_subquery(crawl_id)),
        )
    )
    if site_url is None:
        raise SiteHealthNotFoundError("Site URL not found")

    limit = _clamp_limit(limit)
    scope = "issue_history"
    filters = {
        "site_url_id": str(site_url_id),
        "project_id": str(crawl.project_id),
        "crawl_id": str(crawl_id),
    }

    # Bound history to crawls at or before the SELECTED crawl in the project's
    # chronology (by (created_at, id)) so viewing an older crawl never shows
    # issues from a later one. Issue rows are immutable, so the crawl's
    # position is stable.
    prior_or_same_crawls = (
        select(SiteCrawl.id)
        .where(
            SiteCrawl.project_id == crawl.project_id,
            or_(
                SiteCrawl.created_at < crawl.created_at,
                and_(
                    SiteCrawl.created_at == crawl.created_at,
                    SiteCrawl.id <= crawl.id,
                ),
            ),
        )
        .scalar_subquery()
    )
    stmt = select(SiteIssue).where(
        SiteIssue.site_url_id == site_url_id,
        SiteIssue.project_id == crawl.project_id,
        SiteIssue.crawl_id.in_(prior_or_same_crawls),
    )
    if cursor:
        cur_created, cur_id = _decode_created_id_keyset(
            cursor, scope=scope, filters=filters
        )
        stmt = stmt.where(
            or_(
                SiteIssue.created_at < cur_created,
                and_(
                    SiteIssue.created_at == cur_created,
                    SiteIssue.id < cur_id,
                ),
            )
        )
    stmt = stmt.order_by(SiteIssue.created_at.desc(), SiteIssue.id.desc()).limit(
        limit + 1
    )
    rows = list((await session.scalars(stmt)).all())

    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_keyset_cursor(
            scope=scope,
            filters=filters,
            sort_values=[last.created_at.isoformat(), str(last.id)],
        )
    items = [
        {
            "id": i.id,
            "crawl_id": i.crawl_id,
            "rule_id": i.rule_id,
            "dimension": i.dimension,
            "category": i.category,
            "severity": i.severity,
            "title": display_label_for(i.rule_id),
            "remediation": i.remediation or "",
            "analyzer_version": i.analyzer_version,
            "rule_version": i.rule_version,
            "created_at": _iso(i.created_at),
        }
        for i in rows
    ]
    return {"items": items, "next_cursor": next_cursor}


# =========================================================================
# Dashboard (selected/latest crawl + score summary + quota)
# =========================================================================
async def get_dashboard(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    crawl_id: uuid.UUID | None = None,
) -> dict:
    """Project dashboard: selected/latest crawl, score summary, monitored quota.

    With an explicit ``crawl_id`` uses that crawl (404 if foreign); otherwise
    the project's most recent crawl by ``created_at``. No severity/category
    rollups (Slice 7 does not need them). Quota is the workspace-wide active
    monitored count over the entitlement limit.
    """
    await _load_project(session, workspace_id=workspace_id, project_id=project_id)
    crawl: SiteCrawl | None
    if crawl_id is not None:
        crawl = await session.scalar(
            select(SiteCrawl).where(
                SiteCrawl.id == crawl_id,
                SiteCrawl.workspace_id == workspace_id,
                SiteCrawl.project_id == project_id,
            )
        )
        if crawl is None:
            raise SiteHealthNotFoundError("Crawl not found")
    else:
        crawl = await session.scalar(
            select(SiteCrawl)
            .where(
                SiteCrawl.workspace_id == workspace_id,
                SiteCrawl.project_id == project_id,
            )
            .order_by(SiteCrawl.created_at.desc(), SiteCrawl.id.desc())
            .limit(1)
        )

    entitlement = await resolve_entitlement(session, workspace_id)
    used = await session.scalar(
        select(func.count())
        .select_from(MonitoredSiteUrl)
        .where(
            MonitoredSiteUrl.workspace_id == workspace_id,
            MonitoredSiteUrl.active.is_(True),
        )
    )
    return {
        "project_id": project_id,
        "crawl": project_crawl(crawl) if crawl is not None else None,
        "score_summary": _score_summary(crawl) if crawl is not None else None,
        "quota": {
            "used": int(used or 0),
            "limit": int(entitlement.monitored_url_limit),
        },
    }


# =========================================================================
# Events (JSON replay + SSE support)
# =========================================================================
async def load_events(
    session: AsyncSession,
    *,
    crawl_id: uuid.UUID,
    after: uuid.UUID | None = None,
) -> list[SiteCrawlEvent]:
    """Ordered crawl events (``created_at`` ASC), optionally after an id."""
    stmt = (
        select(SiteCrawlEvent)
        .where(SiteCrawlEvent.crawl_id == crawl_id)
        .order_by(SiteCrawlEvent.created_at.asc(), SiteCrawlEvent.id.asc())
    )
    events = list((await session.scalars(stmt)).all())
    if after is None:
        return events
    seen = False
    tail: list[SiteCrawlEvent] = []
    for event in events:
        if seen:
            tail.append(event)
        elif event.id == after:
            seen = True
    return tail if seen else events


async def load_crawl_for_stream(
    session: AsyncSession, *, workspace_id: uuid.UUID, crawl_id: uuid.UUID
) -> SiteCrawl:
    """Load a crawl for the SSE loop (workspace-scoped; None-safe caller)."""
    return await _load_crawl(session, workspace_id=workspace_id, crawl_id=crawl_id)
