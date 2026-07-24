# Opportunities recompute service + workspace-scoped projections.
#
# ``recompute`` is a pure projection pass (invariant 7): it reads the latest
# dashboard-ready audit (or an explicit one) and the latest terminal Site
# Health crawl, runs the pure detectors (``analysis/opportunities``), scores
# each hit with the config-owned formula, and atomically supersedes the prior
# live set + writes an immutable ``OpportunitySnapshot`` in ONE transaction
# serialized per project by the shared advisory lock (``prompts/locks.py``,
# invariant 2). Supersede-not-mutate (invariant 3): a fresh hit for a live
# ``(rule_id, target_key)`` inserts a NEW row (new id, status carried
# forward) and closes the old one; a live row with no fresh hit is closed
# with no successor; evidence/score/provenance on prior rows is never
# touched. The human ``status`` is the only mutable field.
#
# Every lookup is filtered by the resolved workspace, so a foreign / missing
# id is a 404 (invariant 5). Read projections are priority-sorted and
# keyset-paginated via the shared cursor helpers (invariant 2).
from __future__ import annotations

import statistics
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.opportunities.detectors import (
    AnalysisEvidence,
    DetectorHit,
    PromptSnapshotEvidence,
    SiteEvidence,
    SiteIssueEvidence,
    SiteUrlEvidence,
    VisibilityEvidence,
    detect_brand_absent_high_value_prompt,
    detect_owned_page_not_cited,
    detect_site_issue_opportunities,
)
from app.analysis.opportunities.scoring import priority_score
from app.core.config.audits import (
    AUDIT_STATUS_COMPLETED,
    AUDIT_STATUS_PARTIALLY_COMPLETED,
)
from app.core.config.opportunities import (
    ANALYZER_VERSION,
    CODE_OPPORTUNITY_SUPERSEDED,
    FORMULA_VERSION,
    LIST_DEFAULT_LIMIT,
    LIST_MAX_LIMIT,
    MAX_EXPORT_ITEMS,
    MIN_PRIORITY_TO_SURFACE,
    OPPORTUNITY_ACTIVE_STATUSES,
    OPPORTUNITY_RULES_BY_ID,
    OPPORTUNITY_SEVERITIES,
    OPPORTUNITY_STATUSES,
    OPPORTUNITY_TYPES,
    RECOMPUTE_MAX_ANALYSES,
    RECOMPUTE_MAX_ISSUES,
    RULE_VERSION,
    STATUS_OPEN,
    validate_rule_id,
)
from app.core.config.site_health import (
    CRAWL_STATUS_COMPLETED,
    CRAWL_STATUS_PARTIALLY_COMPLETED,
)
from app.domain.prompts.locks import acquire_project_lock
from app.domain.site_health.normalization import (
    CursorScopeError,
    decode_keyset_cursor,
    encode_keyset_cursor,
)
from app.models.analysis import (
    Citation,
    CompetitorMention,
    MetricSnapshot,
    ResponseAnalysis,
)
from app.models.audit import Audit, AuditPromptSnapshot
from app.models.brand import OwnedDomain
from app.models.opportunity import Opportunity, OpportunitySnapshot
from app.models.project import Project
from app.models.site_health import SiteCrawl, SiteIssue, SiteUrl

__all__ = [
    "OpportunityNotFoundError",
    "OpportunityValidationError",
    "OpportunitySupersededError",
    "InvalidCursorError",
    "recompute",
    "list_opportunities",
    "get_opportunity",
    "update_status",
    "get_summary",
    "load_export_rows",
]

# Dashboard-ready audit statuses (mirrors ``_DASHBOARD_STATUSES`` in
# ``domain/analysis/service.py``; the constants themselves are config-owned).
_DASHBOARD_READY_STATUSES = (AUDIT_STATUS_COMPLETED, AUDIT_STATUS_PARTIALLY_COMPLETED)
# A crawl whose issue rows are usable evidence (terminal, with analysis).
_EVIDENCE_CRAWL_STATUSES = (CRAWL_STATUS_COMPLETED, CRAWL_STATUS_PARTIALLY_COMPLETED)

_PROJECT_NOT_FOUND = "Project not found"
_OPPORTUNITY_NOT_FOUND = "Opportunity not found"
_AUDIT_NOT_FOUND = "Audit not found"
_CRAWL_NOT_FOUND = "Crawl not found"
_LIST_SCOPE = "opportunities"


class OpportunityNotFoundError(Exception):
    """A workspace-scoped resource was missing / foreign (maps to 404)."""


class OpportunityValidationError(Exception):
    """An unknown filter/status token was supplied (maps to 422)."""


class OpportunitySupersededError(Exception):
    """A mutation targeted a superseded row (maps to 409, coded)."""

    code = CODE_OPPORTUNITY_SUPERSEDED


class InvalidCursorError(Exception):
    """A cursor was tampered with or replayed cross-scope (maps to 400)."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _clamp_limit(limit: int | None) -> int:
    if limit is None:
        return LIST_DEFAULT_LIMIT
    return max(1, min(int(limit), LIST_MAX_LIMIT))


# =========================================================================
# Source resolution (audit + crawl)
# =========================================================================
async def _require_project(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    exists = await session.scalar(
        select(Project.id).where(
            Project.id == project_id, Project.workspace_id == workspace_id
        )
    )
    if exists is None:
        raise OpportunityNotFoundError(_PROJECT_NOT_FOUND)


async def _resolve_audit(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    audit_id: uuid.UUID | None,
) -> Audit | None:
    """Explicit audit (404 if foreign) else the latest dashboard-ready one."""
    if audit_id is not None:
        audit = await session.scalar(
            select(Audit).where(
                Audit.id == audit_id,
                Audit.workspace_id == workspace_id,
                Audit.project_id == project_id,
            )
        )
        if audit is None:
            raise OpportunityNotFoundError(_AUDIT_NOT_FOUND)
        return audit
    return await session.scalar(
        select(Audit)
        .where(
            Audit.workspace_id == workspace_id,
            Audit.project_id == project_id,
            Audit.status.in_(_DASHBOARD_READY_STATUSES),
        )
        .order_by(Audit.completed_at.desc().nullslast(), Audit.created_at.desc())
        .limit(1)
    )


async def _resolve_crawl(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    site_crawl_id: uuid.UUID | None,
) -> SiteCrawl | None:
    """Explicit crawl (404 if foreign) else the latest terminal one."""
    if site_crawl_id is not None:
        crawl = await session.scalar(
            select(SiteCrawl).where(
                SiteCrawl.id == site_crawl_id,
                SiteCrawl.workspace_id == workspace_id,
                SiteCrawl.project_id == project_id,
            )
        )
        if crawl is None:
            raise OpportunityNotFoundError(_CRAWL_NOT_FOUND)
        return crawl
    return await session.scalar(
        select(SiteCrawl)
        .where(
            SiteCrawl.workspace_id == workspace_id,
            SiteCrawl.project_id == project_id,
            SiteCrawl.status.in_(_EVIDENCE_CRAWL_STATUSES),
        )
        .order_by(
            SiteCrawl.completed_at.desc().nullslast(), SiteCrawl.created_at.desc()
        )
        .limit(1)
    )


# =========================================================================
# Evidence loading (bounded, deterministic truncation order)
# =========================================================================
async def _load_visibility_evidence(
    session: AsyncSession, *, workspace_id: uuid.UUID, audit: Audit
) -> tuple[VisibilityEvidence, MetricSnapshot | None]:
    """Load analyses/citations/mentions/snapshots + the metric snapshot."""
    analyses = list(
        (
            await session.scalars(
                select(ResponseAnalysis)
                .where(
                    ResponseAnalysis.audit_id == audit.id,
                    ResponseAnalysis.workspace_id == workspace_id,
                )
                .order_by(
                    ResponseAnalysis.prompt_index.asc(),
                    ResponseAnalysis.id.asc(),
                )
                .limit(RECOMPUTE_MAX_ANALYSES)
            )
        ).all()
    )
    analysis_ids = [a.id for a in analyses]

    owned_counts: dict[uuid.UUID, int] = {}
    competitor_names: dict[uuid.UUID, set[str]] = {}
    if analysis_ids:
        citations = list(
            (
                await session.scalars(
                    select(Citation)
                    .where(Citation.analysis_id.in_(analysis_ids))
                    .order_by(Citation.analysis_id.asc(), Citation.ordinal.asc())
                )
            ).all()
        )
        for citation in citations:
            if citation.is_owned:
                owned_counts[citation.analysis_id] = (
                    owned_counts.get(citation.analysis_id, 0) + 1
                )
            if citation.matched_competitor:
                competitor_names.setdefault(citation.analysis_id, set()).add(
                    citation.matched_competitor
                )
        mentions = list(
            (
                await session.scalars(
                    select(CompetitorMention)
                    .where(CompetitorMention.analysis_id.in_(analysis_ids))
                    .order_by(
                        CompetitorMention.created_at.asc(), CompetitorMention.id.asc()
                    )
                )
            ).all()
        )
        for mention in mentions:
            if mention.competitor_name:
                competitor_names.setdefault(mention.analysis_id, set()).add(
                    mention.competitor_name
                )

    snapshots = list(
        (
            await session.scalars(
                select(AuditPromptSnapshot)
                .where(AuditPromptSnapshot.audit_id == audit.id)
                .order_by(AuditPromptSnapshot.prompt_index.asc())
            )
        ).all()
    )
    owned_domains = list(
        (
            await session.scalars(
                select(OwnedDomain.domain)
                .where(OwnedDomain.project_id == audit.project_id)
                .order_by(OwnedDomain.domain.asc())
            )
        ).all()
    )
    metric_snapshot = await session.scalar(
        select(MetricSnapshot).where(
            MetricSnapshot.audit_id == audit.id,
            MetricSnapshot.workspace_id == workspace_id,
        )
    )
    evidence = VisibilityEvidence(
        audit_id=audit.id,
        analyses=tuple(
            AnalysisEvidence(
                analysis_id=a.id,
                prompt_index=a.prompt_index,
                logical_engine=a.logical_engine or "",
                owned_citation_count=owned_counts.get(a.id, 0),
                competitor_names=tuple(sorted(competitor_names.get(a.id, ()))),
            )
            for a in analyses
        ),
        prompt_snapshots=tuple(
            PromptSnapshotEvidence(
                prompt_index=s.prompt_index,
                prompt_id=s.prompt_id,
                text=s.text or "",
                theme=s.theme or "",
                intent=s.intent or "",
            )
            for s in snapshots
        ),
        owned_domains=tuple(sorted(owned_domains)),
    )
    return evidence, metric_snapshot


async def _load_site_evidence(
    session: AsyncSession, *, workspace_id: uuid.UUID, crawl: SiteCrawl
) -> SiteEvidence:
    issues = list(
        (
            await session.scalars(
                select(SiteIssue)
                .where(
                    SiteIssue.crawl_id == crawl.id,
                    SiteIssue.workspace_id == workspace_id,
                )
                .order_by(SiteIssue.created_at.asc(), SiteIssue.id.asc())
                .limit(RECOMPUTE_MAX_ISSUES)
            )
        ).all()
    )
    url_ids = sorted({issue.site_url_id for issue in issues})
    urls: list[SiteUrl] = []
    if url_ids:
        urls = list(
            (
                await session.scalars(
                    select(SiteUrl)
                    .where(SiteUrl.id.in_(url_ids))
                    .order_by(SiteUrl.id.asc())
                )
            ).all()
        )
    return SiteEvidence(
        crawl_id=crawl.id,
        issues=tuple(
            SiteIssueEvidence(
                issue_id=issue.id,
                rule_id=issue.rule_id,
                severity=issue.severity or "",
                category=issue.category or "",
                site_url_id=issue.site_url_id,
                evidence=issue.evidence or {},
            )
            for issue in issues
        ),
        urls=tuple(
            SiteUrlEvidence(site_url_id=url.id, normalized_url=url.normalized_url)
            for url in urls
        ),
    )


# =========================================================================
# Recompute (supersede-not-mutate write path, one transaction)
# =========================================================================
async def recompute(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    audit_id: uuid.UUID | None = None,
    site_crawl_id: uuid.UUID | None = None,
) -> dict:
    """Recompute the project's opportunities and return the new snapshot.

    A missing audit/crawl source is NOT an error — that family simply yields
    zero hits (an empty snapshot is a valid, explicit "nothing to act on"
    result). Concurrent recomputes on the same project serialize on the
    shared advisory lock; the second one recomputes on the latest state.
    """
    await _require_project(session, workspace_id=workspace_id, project_id=project_id)
    audit = await _resolve_audit(
        session, workspace_id=workspace_id, project_id=project_id, audit_id=audit_id
    )
    crawl = await _resolve_crawl(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        site_crawl_id=site_crawl_id,
    )

    hits: list[DetectorHit] = []
    if audit is not None:
        visibility, metric_snapshot = await _load_visibility_evidence(
            session, workspace_id=workspace_id, audit=audit
        )
        if metric_snapshot is None and audit_id is None:
            # Not dashboard-ready (mirrors ``_load_snapshot``): the default
            # resolution requires the audit's aggregate snapshot.
            audit = None
        else:
            visibility_hits = detect_brand_absent_high_value_prompt(
                visibility
            ) + detect_owned_page_not_cited(visibility)
            if metric_snapshot is not None:
                metric_ids = (str(metric_snapshot.id),)
                visibility_hits = [
                    replace(hit, source_metric_ids=metric_ids)
                    for hit in visibility_hits
                ]
            hits.extend(visibility_hits)
    if crawl is not None:
        site = await _load_site_evidence(
            session, workspace_id=workspace_id, crawl=crawl
        )
        hits.extend(detect_site_issue_opportunities(site))

    # Score + apply the write-time floor; dedupe on the live-target identity
    # (first hit wins — detector output is already deterministically ordered).
    scored: list[tuple[DetectorHit, float]] = []
    seen_targets: set[tuple[str, str]] = set()
    for hit in hits:
        rule = OPPORTUNITY_RULES_BY_ID[hit.rule_id]
        score = priority_score(
            severity=rule.severity,
            value_factor=hit.value_factor,
            gap_factor=hit.gap_factor,
        )
        if score < MIN_PRIORITY_TO_SURFACE:
            continue
        target = (hit.rule_id, hit.target_key)
        if target in seen_targets:
            continue
        seen_targets.add(target)
        scored.append((hit, score))
    scored.sort(key=lambda item: (item[0].rule_id, item[0].target_key))

    # Write path: ONE transaction, serialized per project.
    await acquire_project_lock(session, project_id)
    live_rows = list(
        (
            await session.scalars(
                select(Opportunity).where(
                    Opportunity.project_id == project_id,
                    Opportunity.workspace_id == workspace_id,
                    Opportunity.superseded_at.is_(None),
                )
            )
        ).all()
    )
    live_by_target = {(row.rule_id, row.target_key): row for row in live_rows}

    now = _utcnow()
    successor_ids: dict[uuid.UUID, uuid.UUID] = {}  # live row id -> new row id
    new_rows: list[Opportunity] = []
    for hit, score in scored:
        # Write-path catalog validation (invariants 1 + 4).
        rule = OPPORTUNITY_RULES_BY_ID[validate_rule_id(hit.rule_id)]
        live = live_by_target.get((hit.rule_id, hit.target_key))
        new_id = uuid.uuid4()
        new_rows.append(
            Opportunity(
                id=new_id,
                workspace_id=workspace_id,
                project_id=project_id,
                rule_id=rule.rule_id,
                opportunity_type=rule.opportunity_type,
                severity=rule.severity,
                priority_score=score,
                title=rule.title,
                remediation=rule.remediation,
                target_key=hit.target_key,
                target_prompt_id=hit.target_prompt_id,
                target_url=hit.target_url,
                target_theme=hit.target_theme,
                evidence=hit.evidence,
                source_analysis_ids=list(hit.source_analysis_ids),
                source_issue_ids=list(hit.source_issue_ids),
                source_metric_ids=list(hit.source_metric_ids),
                source_traffic_ids=None,
                analyzer_version=ANALYZER_VERSION,
                rule_version=RULE_VERSION,
                formula_version=FORMULA_VERSION,
                # D5: carry the human workflow status forward on supersede.
                status=live.status if live is not None else STATUS_OPEN,
            )
        )
        if live is not None:
            successor_ids[live.id] = new_id
    # Three ordered phases inside the ONE transaction:
    # 1. Close every prior live row (link later) so the partial unique index
    #    releases the (project, rule, target) keys.
    # 2. Insert the successors (their ids now exist for the self-FK).
    # 3. Link predecessors to their successors.
    for live in live_rows:
        live.superseded_at = now
    await session.flush()
    session.add_all(new_rows)
    await session.flush()
    for live in live_rows:
        new_id = successor_ids.get(live.id)
        if new_id is not None:
            live.superseded_by_id = new_id

    snapshot = _build_snapshot(
        workspace_id=workspace_id,
        project_id=project_id,
        audit=audit,
        crawl=crawl,
        new_rows=new_rows,
        scored=scored,
    )
    session.add(snapshot)
    await session.commit()
    return _project_snapshot(snapshot)


def _build_snapshot(
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    audit: Audit | None,
    crawl: SiteCrawl | None,
    new_rows: list[Opportunity],
    scored: list[tuple[DetectorHit, float]],
) -> OpportunitySnapshot:
    """Aggregate the immutable per-run snapshot over the NEW live set."""
    counts_by_type = {name: 0 for name in sorted(OPPORTUNITY_TYPES)}
    counts_by_severity = {name: 0 for name in sorted(OPPORTUNITY_SEVERITIES)}
    counts_by_status = {name: 0 for name in sorted(OPPORTUNITY_STATUSES)}
    for row in new_rows:
        counts_by_type[row.opportunity_type] = (
            counts_by_type.get(row.opportunity_type, 0) + 1
        )
        counts_by_severity[row.severity] = counts_by_severity.get(row.severity, 0) + 1
        counts_by_status[row.status] = counts_by_status.get(row.status, 0) + 1
    scores = sorted(score for _hit, score in scored)
    median = (
        round(statistics.median(scores), 1) if scores else None
    )
    source_analysis_ids = sorted(
        {sid for hit, _score in scored for sid in hit.source_analysis_ids}
    )
    source_issue_ids = sorted(
        {sid for hit, _score in scored for sid in hit.source_issue_ids}
    )
    return OpportunitySnapshot(
        workspace_id=workspace_id,
        project_id=project_id,
        run_id=uuid.uuid4(),
        audit_id=audit.id if audit is not None else None,
        site_crawl_id=crawl.id if crawl is not None else None,
        counts_by_type=counts_by_type,
        counts_by_severity=counts_by_severity,
        counts_by_status=counts_by_status,
        total_count=len(new_rows),
        median_priority=median,
        analyzer_version=ANALYZER_VERSION,
        rule_version=RULE_VERSION,
        formula_version=FORMULA_VERSION,
        source_analysis_ids=source_analysis_ids,
        source_issue_ids=source_issue_ids,
    )


# =========================================================================
# Read projections (priority-sorted, keyset-paginated, workspace-scoped)
# =========================================================================
def _validate_filters(
    *,
    opportunity_type: str | None,
    severity: str | None,
    status: str | None,
    rule_id: str | None,
) -> None:
    if opportunity_type is not None and opportunity_type not in OPPORTUNITY_TYPES:
        raise OpportunityValidationError(
            f"unknown opportunity type: {opportunity_type!r}"
        )
    if severity is not None and severity not in OPPORTUNITY_SEVERITIES:
        raise OpportunityValidationError(f"unknown opportunity severity: {severity!r}")
    if status is not None and status not in OPPORTUNITY_STATUSES:
        raise OpportunityValidationError(f"unknown opportunity status: {status!r}")
    if rule_id is not None:
        try:
            validate_rule_id(rule_id)
        except ValueError as exc:
            raise OpportunityValidationError(str(exc)) from exc


def _filter_clauses(
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    opportunity_type: str | None,
    severity: str | None,
    status: str | None,
    rule_id: str | None,
    min_priority: float | None,
) -> list:
    clauses = [
        Opportunity.workspace_id == workspace_id,
        Opportunity.project_id == project_id,
        Opportunity.superseded_at.is_(None),
    ]
    if opportunity_type:
        clauses.append(Opportunity.opportunity_type == opportunity_type)
    if severity:
        clauses.append(Opportunity.severity == severity)
    if status:
        clauses.append(Opportunity.status == status)
    else:
        # Default view: the triage queue.
        clauses.append(Opportunity.status.in_(sorted(OPPORTUNITY_ACTIVE_STATUSES)))
    if rule_id:
        clauses.append(Opportunity.rule_id == rule_id)
    if min_priority is not None:
        clauses.append(Opportunity.priority_score >= min_priority)
    return clauses


def _cursor_filters(
    *,
    project_id: uuid.UUID,
    opportunity_type: str | None,
    severity: str | None,
    status: str | None,
    rule_id: str | None,
    min_priority: float | None,
) -> dict:
    return {
        "project_id": str(project_id),
        "type": opportunity_type or None,
        "severity": severity or None,
        "status": status or None,
        "rule_id": rule_id or None,
        "min_priority": min_priority,
    }


async def list_opportunities(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    opportunity_type: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    rule_id: str | None = None,
    min_priority: float | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict:
    """Live-row catalog page, ordered ``(priority_score DESC, id DESC)``."""
    await _require_project(session, workspace_id=workspace_id, project_id=project_id)
    _validate_filters(
        opportunity_type=opportunity_type,
        severity=severity,
        status=status,
        rule_id=rule_id,
    )
    limit = _clamp_limit(limit)
    filters = _cursor_filters(
        project_id=project_id,
        opportunity_type=opportunity_type,
        severity=severity,
        status=status,
        rule_id=rule_id,
        min_priority=min_priority,
    )
    clauses = _filter_clauses(
        workspace_id=workspace_id,
        project_id=project_id,
        opportunity_type=opportunity_type,
        severity=severity,
        status=status,
        rule_id=rule_id,
        min_priority=min_priority,
    )
    if cursor:
        try:
            score_raw, id_raw = decode_keyset_cursor(
                cursor, scope=_LIST_SCOPE, filters=filters
            )
            cursor_score = float(score_raw)
            cursor_id = uuid.UUID(id_raw)
        except (CursorScopeError, ValueError) as exc:
            raise InvalidCursorError(str(exc)) from exc
        clauses.append(
            or_(
                Opportunity.priority_score < cursor_score,
                and_(
                    Opportunity.priority_score == cursor_score,
                    Opportunity.id < cursor_id,
                ),
            )
        )
    rows = list(
        (
            await session.scalars(
                select(Opportunity)
                .where(*clauses)
                .order_by(Opportunity.priority_score.desc(), Opportunity.id.desc())
                .limit(limit + 1)
            )
        ).all()
    )
    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_keyset_cursor(
            scope=_LIST_SCOPE,
            filters=filters,
            sort_values=[last.priority_score, str(last.id)],
        )
    return {
        "items": [_project_item(row) for row in rows],
        "next_cursor": next_cursor,
    }


async def get_opportunity(
    session: AsyncSession, *, workspace_id: uuid.UUID, opportunity_id: uuid.UUID
) -> dict:
    row = await session.scalar(
        select(Opportunity).where(
            Opportunity.id == opportunity_id,
            Opportunity.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise OpportunityNotFoundError(_OPPORTUNITY_NOT_FOUND)
    return _project_detail(row)


async def update_status(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    opportunity_id: uuid.UUID,
    status: str,
) -> dict:
    """Mutate the human workflow status (the ONLY mutable field)."""
    if status not in OPPORTUNITY_STATUSES:
        raise OpportunityValidationError(f"unknown opportunity status: {status!r}")
    row = await session.scalar(
        select(Opportunity).where(
            Opportunity.id == opportunity_id,
            Opportunity.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise OpportunityNotFoundError(_OPPORTUNITY_NOT_FOUND)
    if row.superseded_at is not None:
        raise OpportunitySupersededError(
            "Opportunity was superseded by a newer recompute"
        )
    row.status = status
    await session.commit()
    return _project_item(row)


async def get_summary(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> dict:
    """Latest snapshot projection; ``computed=false`` when never recomputed."""
    await _require_project(session, workspace_id=workspace_id, project_id=project_id)
    snapshot = await session.scalar(
        select(OpportunitySnapshot)
        .where(
            OpportunitySnapshot.workspace_id == workspace_id,
            OpportunitySnapshot.project_id == project_id,
        )
        .order_by(
            OpportunitySnapshot.created_at.desc(), OpportunitySnapshot.id.desc()
        )
        .limit(1)
    )
    if snapshot is None:
        return {
            "computed": False,
            "run_id": None,
            "audit_id": None,
            "site_crawl_id": None,
            "counts_by_type": {},
            "counts_by_severity": {},
            "counts_by_status": {},
            "total_count": 0,
            "median_priority": None,
            "analyzer_version": ANALYZER_VERSION,
            "rule_version": RULE_VERSION,
            "formula_version": FORMULA_VERSION,
            "computed_at": None,
        }
    return {
        "computed": True,
        "run_id": snapshot.run_id,
        "audit_id": snapshot.audit_id,
        "site_crawl_id": snapshot.site_crawl_id,
        "counts_by_type": snapshot.counts_by_type or {},
        "counts_by_severity": snapshot.counts_by_severity or {},
        "counts_by_status": snapshot.counts_by_status or {},
        "total_count": snapshot.total_count,
        "median_priority": snapshot.median_priority,
        "analyzer_version": snapshot.analyzer_version,
        "rule_version": snapshot.rule_version,
        "formula_version": snapshot.formula_version,
        "computed_at": _iso(snapshot.created_at),
    }


async def load_export_rows(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    opportunity_type: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    rule_id: str | None = None,
    min_priority: float | None = None,
) -> list[dict]:
    """The list projection, uncapped-sortable but bounded by MAX_EXPORT_ITEMS.

    Same filters as ``list_opportunities`` (including the default active-status
    view) so an export always matches what the catalog shows.
    """
    await _require_project(session, workspace_id=workspace_id, project_id=project_id)
    _validate_filters(
        opportunity_type=opportunity_type,
        severity=severity,
        status=status,
        rule_id=rule_id,
    )
    clauses = _filter_clauses(
        workspace_id=workspace_id,
        project_id=project_id,
        opportunity_type=opportunity_type,
        severity=severity,
        status=status,
        rule_id=rule_id,
        min_priority=min_priority,
    )
    rows = list(
        (
            await session.scalars(
                select(Opportunity)
                .where(*clauses)
                .order_by(Opportunity.priority_score.desc(), Opportunity.id.desc())
                .limit(MAX_EXPORT_ITEMS)
            )
        ).all()
    )
    return [_project_export_row(row) for row in rows]


# =========================================================================
# Row projections (model -> strict contract dicts)
# =========================================================================
def _project_item(row: Opportunity) -> dict:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "rule_id": row.rule_id,
        "opportunity_type": row.opportunity_type,
        "severity": row.severity,
        "priority_score": row.priority_score,
        "title": row.title or "",
        "target_key": row.target_key,
        "target_prompt_id": row.target_prompt_id,
        "target_url": row.target_url,
        "target_theme": row.target_theme,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _project_detail(row: Opportunity) -> dict:
    return {
        **_project_item(row),
        "remediation": row.remediation or "",
        "evidence": row.evidence or {},
        "source_analysis_ids": list(row.source_analysis_ids or []),
        "source_issue_ids": list(row.source_issue_ids or []),
        "source_metric_ids": list(row.source_metric_ids or []),
        "source_traffic_ids": list(row.source_traffic_ids or []),
        "analyzer_version": row.analyzer_version,
        "rule_version": row.rule_version,
        "formula_version": row.formula_version,
        "superseded_by_id": row.superseded_by_id,
        "superseded_at": _iso(row.superseded_at),
    }


def _project_export_row(row: Opportunity) -> dict:
    evidence = row.evidence or {}
    target = row.target_url or evidence.get("prompt_text") or row.target_key
    return {
        "id": str(row.id),
        "rule_id": row.rule_id,
        "opportunity_type": row.opportunity_type,
        "severity": row.severity,
        "priority_score": row.priority_score,
        "status": row.status,
        "title": row.title or "",
        "target": target,
        "remediation": row.remediation or "",
        "rule_version": row.rule_version,
        "formula_version": row.formula_version,
        "created_at": _iso(row.created_at),
    }


def _project_snapshot(snapshot: OpportunitySnapshot) -> dict:
    return {
        "id": snapshot.id,
        "run_id": snapshot.run_id,
        "audit_id": snapshot.audit_id,
        "site_crawl_id": snapshot.site_crawl_id,
        "counts_by_type": snapshot.counts_by_type or {},
        "counts_by_severity": snapshot.counts_by_severity or {},
        "counts_by_status": snapshot.counts_by_status or {},
        "total_count": snapshot.total_count,
        "median_priority": snapshot.median_priority,
        "analyzer_version": snapshot.analyzer_version,
        "rule_version": snapshot.rule_version,
        "formula_version": snapshot.formula_version,
        "created_at": _iso(snapshot.created_at),
    }
