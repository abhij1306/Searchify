# Analysis/metrics projections (B6, invariant 7 — read persisted analysis only).
#
# Every function here reads persisted rows (``MetricSnapshot`` /
# ``ResponseAnalysis`` / ``Citation`` / ``Audit`` / ``AuditTask``) and NEVER
# calls a provider. They back the metrics/dashboard/evidence/export endpoints.
# All queries are workspace-scoped (invariant 5).
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.analysis import (
    VISIBILITY_EVIDENCE_DEFAULT_LIMIT,
    VISIBILITY_EVIDENCE_MAX_LIMIT,
    VISIBILITY_TREND_DEFAULT_GRANULARITY,
    VISIBILITY_TREND_GRANULARITIES,
    VISIBILITY_TREND_MAX_POINTS,
    VISIBILITY_TRENDS_STRICT_VERSION_BUCKETS,
)
from app.core.config.audits import (
    AUDIT_STATUS_COMPLETED,
    AUDIT_STATUS_PARTIALLY_COMPLETED,
)
from app.core.config.provider_catalog import LOGICAL_ENGINES
from app.domain.analysis.schemas import (
    CitationEvidence,
    EngineComparisonRow,
    ExecutionEvidenceResponse,
    MetricsResponse,
    RankingRow,
    VisibilityEvidenceResponse,
    VisibilityEvidenceSearchEvent,
    VisibilityExecutionEvidence,
    VisibilityFanoutState,
    VisibilityMentionEvidence,
    VisibilityResponse,
    VisibilityTrendPoint,
    VisibilityTrendRankingRow,
    VisibilityTrendSov,
)
from app.models.analysis import (
    BrandMention,
    Citation,
    CompetitorMention,
    MetricSnapshot,
    ResponseAnalysis,
)
from app.models.audit import (
    Audit,
    AuditPromptSnapshot,
    AuditTask,
    RawResponseArtifact,
)

# A run is "completed" (dashboard-eligible) when fully or partially completed.
_DASHBOARD_STATUSES = (
    AUDIT_STATUS_COMPLETED,
    AUDIT_STATUS_PARTIALLY_COMPLETED,
)


class AnalysisNotFoundError(LookupError):
    """Raised when a requested projection has no persisted rows to serve."""


async def get_metrics(
    session: AsyncSession, *, workspace_id: uuid.UUID, audit_id: uuid.UUID
) -> MetricsResponse:
    """Serve the single-run ``MetricSnapshot`` projection."""
    snapshot = await _load_snapshot(
        session, workspace_id=workspace_id, audit_id=audit_id
    )
    return MetricsResponse.model_validate(snapshot)


async def get_visibility(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    audit_id: uuid.UUID | None = None,
) -> VisibilityResponse:
    """Serve the selected-run dashboard projection for a project.

    Defaults to the project's latest completed/partially-completed audit when
    ``audit_id`` is omitted. Computed server-side from the persisted snapshot;
    no provider call (invariant 7).
    """
    if audit_id is None:
        audit_id = await _latest_dashboard_audit_id(
            session, workspace_id=workspace_id, project_id=project_id
        )
        if audit_id is None:
            raise AnalysisNotFoundError("No completed audit for project")

    audit = await session.scalar(
        select(Audit).where(
            Audit.id == audit_id,
            Audit.workspace_id == workspace_id,
            Audit.project_id == project_id,
        )
    )
    if audit is None:
        raise AnalysisNotFoundError("Audit not found")
    snapshot = await _load_snapshot(
        session, workspace_id=workspace_id, audit_id=audit_id
    )
    metrics = snapshot.metrics or {}
    return VisibilityResponse(
        project_id=project_id,
        audit_id=audit_id,
        audit_status=audit.status,
        analyzer_version=snapshot.analyzer_version,
        scoring_rule_version=snapshot.scoring_rule_version,
        total_completed=snapshot.total_completed,
        total_failed=snapshot.total_failed,
        visibility_score=snapshot.visibility_score,
        rankings=_rankings(metrics),
        per_engine=_engine_rows(metrics),
        sentiment=metrics.get("sentiment"),
        avg_position=metrics.get("avg_position"),
        created_at=snapshot.created_at,
    )


class TrendQueryError(ValueError):
    """Raised for an invalid trend query (bad engine/granularity/range).

    The API layer maps this to HTTP 422; it is never a not-found condition.
    """


async def get_visibility_trends(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    logical_engine: str | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    granularity: str = VISIBILITY_TREND_DEFAULT_GRANULARITY,
) -> list[VisibilityTrendPoint]:
    """Project the workspace-scoped cross-run Visibility trend (invariant 7).

    A pure projection over the already-persisted per-run ``MetricSnapshot`` rows
    for the project's dashboard-ready audits — no provider call, no re-scoring.
    ``granularity=run`` returns one point per snapshot; ``week``/``month`` fold
    snapshots into deterministic UTC buckets. Under strict version bucketing any
    requested bucket that would cross an analyzer/scoring version boundary makes
    the whole range fall back to raw per-run points. Returns ``[]`` (never an
    error) for a valid project with no matching history.
    """
    granularity = granularity or VISIBILITY_TREND_DEFAULT_GRANULARITY
    _validate_trend_query(
        logical_engine=logical_engine,
        from_at=from_at,
        to_at=to_at,
        granularity=granularity,
    )
    from_at = _to_utc(from_at)
    to_at = _to_utc(to_at)

    rows = await _load_trend_rows(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        from_at=from_at,
        to_at=to_at,
    )
    sources = [
        source
        for snapshot, audit in rows
        if (source := _trend_source(snapshot, audit, logical_engine))
        is not None
    ]
    if not sources:
        return []

    # Cap to the newest N source snapshots but keep the response chronological.
    if len(sources) > VISIBILITY_TREND_MAX_POINTS:
        sources = sources[-VISIBILITY_TREND_MAX_POINTS:]

    if granularity == "run":
        return [_raw_point(source) for source in sources]

    return _bucket_points(sources, granularity)


async def get_visibility_evidence(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    audit_id: uuid.UUID | None = None,
    prompt_id: uuid.UUID | None = None,
    logical_engine: str | None = None,
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    limit: int = VISIBILITY_EVIDENCE_DEFAULT_LIMIT,
) -> VisibilityEvidenceResponse:
    """Project the workspace-scoped execution evidence dataset (invariant 7).

    A pure READ-ONLY projection over already-persisted per-execution rows for
    the project's dashboard-ready audits — never a provider call and never a
    mutation/backfill. Feeds the Mentions & Citations and Query Fanout tabs.

    Optional filters (``audit_id`` / ``prompt_id`` / ``logical_engine`` /
    inclusive UTC ``from``/``to`` completion window) INTERSECT: when both
    ``audit_id`` and a date window are supplied the selected audit must also
    fall inside the window. Returns at most ``limit`` items in deterministic
    newest-first order with ``truncated`` set when more matches exist. A valid
    project with no matching evidence returns an empty list, ``truncated=False``.
    """
    _validate_engine_and_range(
        logical_engine=logical_engine, from_at=from_at, to_at=to_at
    )
    if limit < 1 or limit > VISIBILITY_EVIDENCE_MAX_LIMIT:
        raise TrendQueryError(
            f"'limit' must be between 1 and {VISIBILITY_EVIDENCE_MAX_LIMIT}"
        )
    from_at = _to_utc(from_at)
    to_at = _to_utc(to_at)

    # If an audit is selected, it must belong to this workspace/project (else a
    # cross-workspace/missing id must 404 — never leak that it exists).
    if audit_id is not None:
        owning = await session.scalar(
            select(Audit.id).where(
                Audit.id == audit_id,
                Audit.workspace_id == workspace_id,
                Audit.project_id == project_id,
            )
        )
        if owning is None:
            raise AnalysisNotFoundError("Audit not found")

    stmt = (
        select(
            ResponseAnalysis,
            AuditTask,
            AuditPromptSnapshot,
            Audit,
            RawResponseArtifact,
        )
        .join(AuditTask, AuditTask.id == ResponseAnalysis.task_id)
        .join(Audit, Audit.id == ResponseAnalysis.audit_id)
        .join(
            AuditPromptSnapshot,
            AuditPromptSnapshot.id == AuditTask.prompt_snapshot_id,
        )
        .outerjoin(
            RawResponseArtifact,
            RawResponseArtifact.id == ResponseAnalysis.artifact_id,
        )
        .where(
            ResponseAnalysis.workspace_id == workspace_id,
            Audit.workspace_id == workspace_id,
            Audit.project_id == project_id,
            Audit.status.in_(_DASHBOARD_STATUSES),
        )
    )
    if audit_id is not None:
        stmt = stmt.where(ResponseAnalysis.audit_id == audit_id)
    if prompt_id is not None:
        stmt = stmt.where(AuditPromptSnapshot.prompt_id == prompt_id)
    if logical_engine is not None:
        stmt = stmt.where(ResponseAnalysis.logical_engine == logical_engine)
    if from_at is not None:
        stmt = stmt.where(Audit.completed_at >= from_at)
    if to_at is not None:
        stmt = stmt.where(Audit.completed_at <= to_at)
    # Newest-first: audit completion desc, then prompt index / engine /
    # repetition asc for a deterministic order (created_at + analysis id break
    # any remaining ties so the truncation window is stable).
    stmt = stmt.order_by(
        Audit.completed_at.desc().nullslast(),
        Audit.created_at.desc(),
        ResponseAnalysis.prompt_index.asc(),
        ResponseAnalysis.logical_engine.asc(),
        ResponseAnalysis.repetition.asc(),
        ResponseAnalysis.id.asc(),
    ).limit(limit + 1)

    rows = list((await session.execute(stmt)).all())
    truncated = len(rows) > limit
    rows = rows[:limit]
    if not rows:
        return VisibilityEvidenceResponse(items=[], truncated=False)

    analysis_ids = [analysis.id for analysis, *_ in rows]
    brand_by_analysis = await _mentions_by_analysis(
        session, model=BrandMention, analysis_ids=analysis_ids, kind="brand"
    )
    competitor_by_analysis = await _mentions_by_analysis(
        session,
        model=CompetitorMention,
        analysis_ids=analysis_ids,
        kind="competitor",
    )
    citations_by_analysis = await _citations_by_analysis(
        session, analysis_ids=analysis_ids
    )

    items = [
        _evidence_item(
            analysis=analysis,
            task=task,
            snapshot=snapshot,
            audit=audit,
            artifact=artifact,
            mentions=(
                brand_by_analysis.get(analysis.id, [])
                + competitor_by_analysis.get(analysis.id, [])
            ),
            citations=citations_by_analysis.get(analysis.id, []),
        )
        for analysis, task, snapshot, audit, artifact in rows
    ]
    return VisibilityEvidenceResponse(items=items, truncated=truncated)


async def get_execution_evidence(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    task_id: uuid.UUID,
) -> ExecutionEvidenceResponse:
    """Serve one execution's persisted analysis + citation evidence.

    Keyed on the *execution* (``AuditTask``) id — the id clients receive from
    ``GET /audits/{id}/executions`` — not the internal ``ResponseAnalysis`` id.
    The analysis' own id is still surfaced as ``analysis_id``.
    """
    analysis = await session.scalar(
        select(ResponseAnalysis).where(
            ResponseAnalysis.task_id == task_id,
            ResponseAnalysis.workspace_id == workspace_id,
        )
    )
    if analysis is None:
        raise AnalysisNotFoundError("Execution analysis not found")
    citations = list(
        (
            await session.scalars(
                select(Citation)
                .where(Citation.analysis_id == analysis.id)
                .order_by(Citation.ordinal.asc())
            )
        ).all()
    )
    score = analysis.score or {}
    return ExecutionEvidenceResponse(
        id=analysis.task_id,
        analysis_id=analysis.id,
        audit_id=analysis.audit_id,
        task_id=analysis.task_id,
        artifact_id=analysis.artifact_id,
        analyzer_version=analysis.analyzer_version,
        scoring_rule_version=analysis.scoring_rule_version,
        logical_engine=analysis.logical_engine,
        transport_provider=analysis.transport_provider,
        transport_model=analysis.transport_model,
        prompt_index=analysis.prompt_index,
        repetition=analysis.repetition,
        prompt_class=analysis.prompt_class,
        brand_mentioned=analysis.brand_mentioned,
        brand_first_offset=analysis.brand_first_offset,
        owned_domain_cited=analysis.owned_domain_cited,
        owned_citation_count=analysis.owned_citation_count,
        unintended_domain_cited=analysis.unintended_domain_cited,
        citation_count=analysis.citation_count,
        search_used=analysis.search_used,
        search_query_count=analysis.search_query_count,
        sentiment=analysis.sentiment,
        avg_position=analysis.avg_position,
        score=analysis.score,
        citations=[CitationEvidence.model_validate(c) for c in citations],
        competitors_mentioned=list(score.get("competitors_mentioned") or []),
        created_at=analysis.created_at,
    )


async def load_export_bundle(
    session: AsyncSession, *, workspace_id: uuid.UUID, audit_id: uuid.UUID
) -> tuple[Audit, list[AuditTask]]:
    """Load the audit + its execution rows for CSV/MD export (invariant 7)."""
    audit = await session.scalar(
        select(Audit).where(
            Audit.id == audit_id, Audit.workspace_id == workspace_id
        )
    )
    if audit is None:
        raise AnalysisNotFoundError("Audit not found")
    tasks = list(
        (
            await session.scalars(
                select(AuditTask)
                .where(AuditTask.audit_id == audit_id)
                .where(AuditTask.workspace_id == workspace_id)
                .order_by(
                    AuditTask.prompt_index.asc(), AuditTask.repetition.asc()
                )
            )
        ).all()
    )
    return audit, tasks


# --- Execution-evidence projection helpers (pure, read-only, invariant 7) --
#
# Every helper below reads only already-persisted rows and normalizes stored
# JSON tolerantly: malformed event entries are ignored, empty query strings are
# preserved, and query text / call ids / counts are never invented.


async def _mentions_by_analysis(
    session: AsyncSession,
    *,
    model: type,
    analysis_ids: list[uuid.UUID],
    kind: str,
) -> dict[uuid.UUID, list[VisibilityMentionEvidence]]:
    """Batch-load persisted mention rows grouped by analysis id."""
    if not analysis_ids:
        return {}
    name_attr = (
        model.brand_name if kind == "brand" else model.competitor_name
    )
    rows = list(
        (
            await session.scalars(
                select(model)
                .where(model.analysis_id.in_(analysis_ids))
                .order_by(model.created_at.asc(), model.id.asc())
            )
        ).all()
    )
    grouped: dict[uuid.UUID, list[VisibilityMentionEvidence]] = {}
    for row in rows:
        grouped.setdefault(row.analysis_id, []).append(
            VisibilityMentionEvidence(
                kind=kind,
                name=getattr(row, name_attr.key) or "",
                first_offset=getattr(row, "first_offset", None),
                artifact_id=row.artifact_id,
                analyzer_version=row.analyzer_version or "",
            )
        )
    return grouped


async def _citations_by_analysis(
    session: AsyncSession, *, analysis_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[CitationEvidence]]:
    """Batch-load persisted classified citation rows grouped by analysis id."""
    if not analysis_ids:
        return {}
    rows = list(
        (
            await session.scalars(
                select(Citation)
                .where(Citation.analysis_id.in_(analysis_ids))
                .order_by(Citation.analysis_id.asc(), Citation.ordinal.asc())
            )
        ).all()
    )
    grouped: dict[uuid.UUID, list[CitationEvidence]] = {}
    for row in rows:
        grouped.setdefault(row.analysis_id, []).append(
            CitationEvidence.model_validate(row)
        )
    return grouped


def _normalize_events(raw: object) -> list[VisibilityEvidenceSearchEvent]:
    """Tolerantly normalize a stored search-event list.

    Ignores non-list payloads and malformed entries; preserves empty query
    strings; never invents query text/call ids. An entry must be a mapping to
    contribute an event.
    """
    if not isinstance(raw, list):
        return []
    known_keys = {
        "sequence",
        "query",
        "call_id",
        "call_sequence",
        "query_sequence",
    }
    events: list[VisibilityEvidenceSearchEvent] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        # A well-formed event carries at least one recognized field; entries
        # with none (e.g. ``{}`` or ``{"foo": "bar"}``) are malformed and would
        # otherwise surface as phantom all-zero placeholder events.
        if not known_keys.intersection(entry):
            continue
        events.append(
            VisibilityEvidenceSearchEvent(
                sequence=_as_int(entry.get("sequence")),
                query=_as_str(entry.get("query")),
                call_id=_as_str(entry.get("call_id")),
                call_sequence=_as_int(entry.get("call_sequence")),
                query_sequence=_as_int(entry.get("query_sequence")),
            )
        )
    return events


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def _as_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _select_events(
    artifact: RawResponseArtifact | None, task: AuditTask
) -> tuple[list[VisibilityEvidenceSearchEvent], str]:
    """Prefer non-empty artifact events; fall back to the task copy.

    Never merges the two copies. Returns ``(events, event_source)`` where
    ``event_source`` is ``raw_artifact`` / ``audit_task`` / ``none``.
    """
    if artifact is not None:
        artifact_events = _normalize_events(artifact.search_events)
        if artifact_events:
            return artifact_events, "raw_artifact"
    task_events = _normalize_events(task.search_events)
    if task_events:
        return task_events, "audit_task"
    return [], "none"


def _fanout_state(
    *,
    events: list[VisibilityEvidenceSearchEvent],
    search_used: bool,
    search_query_count: int,
) -> tuple[bool, VisibilityFanoutState]:
    """Derive ``(query_text_available, state)`` from the persisted signals."""
    query_text_available = any(ev.query.strip() for ev in events)
    if query_text_available:
        return True, VisibilityFanoutState.QUERIES_AVAILABLE
    if search_used or search_query_count > 0:
        return False, VisibilityFanoutState.COUNT_ONLY
    return False, VisibilityFanoutState.NO_SEARCH


def _evidence_item(
    *,
    analysis: ResponseAnalysis,
    task: AuditTask,
    snapshot: AuditPromptSnapshot,
    audit: Audit,
    artifact: RawResponseArtifact | None,
    mentions: list[VisibilityMentionEvidence],
    citations: list[CitationEvidence],
) -> VisibilityExecutionEvidence:
    events, event_source = _select_events(artifact, task)
    query_text_available, state = _fanout_state(
        events=events,
        search_used=bool(analysis.search_used),
        search_query_count=int(analysis.search_query_count or 0),
    )
    return VisibilityExecutionEvidence(
        audit_id=analysis.audit_id,
        task_id=analysis.task_id,
        analysis_id=analysis.id,
        artifact_id=analysis.artifact_id,
        prompt_snapshot_id=snapshot.id,
        prompt_id=snapshot.prompt_id,
        prompt_index=analysis.prompt_index,
        prompt_text=snapshot.text or "",
        repetition=analysis.repetition,
        completed_at=_to_utc(audit.completed_at),
        logical_engine=analysis.logical_engine,
        transport_provider=analysis.transport_provider,
        transport_model=analysis.transport_model,
        search_used=bool(analysis.search_used),
        search_query_count=int(analysis.search_query_count or 0),
        query_text_available=query_text_available,
        state=state,
        search_events=events,
        event_source=event_source,
        mentions=mentions,
        citations=citations,
    )


async def _load_snapshot(
    session: AsyncSession, *, workspace_id: uuid.UUID, audit_id: uuid.UUID
) -> MetricSnapshot:
    snapshot = await session.scalar(
        select(MetricSnapshot).where(
            MetricSnapshot.audit_id == audit_id,
            MetricSnapshot.workspace_id == workspace_id,
        )
    )
    if snapshot is None:
        raise AnalysisNotFoundError("Metrics not available for audit")
    return snapshot


async def _latest_dashboard_audit_id(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> uuid.UUID | None:
    return await session.scalar(
        select(Audit.id)
        .where(
            Audit.workspace_id == workspace_id,
            Audit.project_id == project_id,
            Audit.status.in_(_DASHBOARD_STATUSES),
        )
        .order_by(Audit.completed_at.desc().nullslast(), Audit.created_at.desc())
        .limit(1)
    )


def _rankings(metrics: dict) -> list[RankingRow]:
    """Build the brand-vs-competitor rankings table from the aggregate.

    Visibility % (mention rate) + SOV are populated; sentiment + average
    position are present but null (decision B-2).
    """
    sov = metrics.get("share_of_voice") or {}
    share = sov.get("share") or {}
    counts = sov.get("mention_counts") or {}
    brand_name = _brand_name(counts, metrics)
    competitor_mention = metrics.get("competitor_mention_rate") or {}
    competitor_citation = metrics.get("competitor_citation_rate") or {}

    rows: list[RankingRow] = [
        RankingRow(
            name=brand_name,
            is_brand=True,
            mention_rate=metrics.get("brand_mention_rate"),
            citation_rate=metrics.get("owned_citation_rate"),
            share_of_voice=share.get(brand_name),
            mention_count=int(counts.get(brand_name, 0) or 0),
        )
    ]
    for name in competitor_mention:
        rows.append(
            RankingRow(
                name=name,
                is_brand=False,
                mention_rate=competitor_mention.get(name),
                citation_rate=competitor_citation.get(name),
                share_of_voice=share.get(name),
                mention_count=int(counts.get(name, 0) or 0),
            )
        )
    # Deterministic order: highest SOV first, then name for stable ties.
    rows.sort(key=lambda r: (-(r.share_of_voice or 0.0), r.name))
    return rows


def _brand_name(counts: dict, metrics: dict) -> str:
    # The SOV block keys the brand by its display name; the first non-competitor
    # entry is the brand. Fall back to a stable label.
    competitor_names = set(metrics.get("competitor_mention_rate") or {})
    for name in counts:
        if name not in competitor_names:
            return name
    return "Brand"


def _engine_rows(metrics: dict) -> list[EngineComparisonRow]:
    per_engine = metrics.get("per_engine") or {}
    rows: list[EngineComparisonRow] = []
    for engine, agg in sorted(per_engine.items()):
        rate = agg.get("brand_mention_rate")
        rows.append(
            EngineComparisonRow(
                logical_engine=engine,
                total_completed=int(agg.get("total_completed", 0) or 0),
                brand_mention_rate=rate,
                owned_citation_rate=agg.get("owned_citation_rate"),
                search_use_rate=agg.get("search_use_rate"),
                visibility_score=round(float(rate) * 100, 2)
                if rate is not None
                else None,
            )
        )
    return rows


# --- Cross-run Visibility trend projection helpers (pure, invariant 7) -----
#
# Every helper below reads only the already-persisted ``MetricSnapshot.metrics``
# dict (the same shape the single-run dashboard reads) and the owning ``Audit``
# timestamp/status. None of them re-score, re-extract, or call a provider.


@dataclass
class _TrendSource:
    """One dashboard-ready snapshot projected into trend-ready primitives.

    A raw point folds exactly one of these; a bucket folds many. ``metrics`` is
    the persisted per-run metrics dict, or the engine slice
    (``metrics.per_engine[engine]``) when the request is engine-filtered.
    """

    snapshot_id: uuid.UUID
    audit_id: uuid.UUID
    completed_at: datetime
    logical_engine: str | None
    analyzer_version: str
    scoring_rule_version: str
    total_completed: int
    visibility_score: float | None
    metrics: dict


@dataclass
class _RankingAccumulator:
    """Running mention/rate sums for one entity across a bucket's snapshots."""

    name: str
    is_brand: bool
    mention_count: int = 0
    # Completion-weighted rate numerators (rate * completions) with a SEPARATE
    # denominator per rate, so a snapshot that reports one rate but not the
    # other does not dilute the missing one as though it were zero.
    mention_rate_weight: float = 0.0
    mention_rate_denom: int = 0
    citation_rate_weight: float = 0.0
    citation_rate_denom: int = 0


@dataclass
class _RateAccumulator:
    """Completion-weighted numerator/denominator for a single headline rate."""

    weighted: float = 0.0
    weight: int = 0

    def add(self, rate: float | None, completions: int) -> None:
        if rate is None or completions <= 0:
            return
        self.weighted += float(rate) * completions
        self.weight += completions

    def value(self) -> float | None:
        if self.weight <= 0:
            return None
        return round(self.weighted / self.weight, 4)


def _validate_engine_and_range(
    *,
    logical_engine: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
) -> None:
    """Shared engine + inclusive-UTC-range validation (trends + evidence).

    Raises ``TrendQueryError`` (mapped to HTTP 422) for an unknown logical
    engine, a naive timestamp, or a reversed range. Kept identical to the
    original trend validation so the trend contract is unchanged.
    """
    if logical_engine is not None and logical_engine not in LOGICAL_ENGINES:
        raise TrendQueryError(f"Unknown logical engine: {logical_engine!r}")
    _require_aware("from", from_at)
    _require_aware("to", to_at)
    if from_at is not None and to_at is not None:
        if _to_utc(from_at) > _to_utc(to_at):
            raise TrendQueryError("'from' must not be after 'to'")


def _validate_trend_query(
    *,
    logical_engine: str | None,
    from_at: datetime | None,
    to_at: datetime | None,
    granularity: str,
) -> None:
    if granularity not in VISIBILITY_TREND_GRANULARITIES:
        raise TrendQueryError(f"Unsupported granularity: {granularity!r}")
    _validate_engine_and_range(
        logical_engine=logical_engine, from_at=from_at, to_at=to_at
    )


def _require_aware(label: str, value: datetime | None) -> None:
    if value is not None and value.tzinfo is None:
        raise TrendQueryError(
            f"'{label}' must be a timezone-aware timestamp"
        )


def _to_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # Defensive: the query layer already rejects naive datetimes.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _load_trend_rows(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    from_at: datetime | None,
    to_at: datetime | None,
) -> list[tuple[MetricSnapshot, Audit]]:
    """Load (snapshot, audit) pairs for the project's dashboard-ready audits.

    Workspace/project scoped (invariant 5), restricted to dashboard-ready
    statuses with a non-null ``completed_at`` and the requested inclusive UTC
    window, ordered chronologically.
    """
    stmt = (
        select(MetricSnapshot, Audit)
        .join(Audit, Audit.id == MetricSnapshot.audit_id)
        .where(
            MetricSnapshot.workspace_id == workspace_id,
            MetricSnapshot.project_id == project_id,
            Audit.workspace_id == workspace_id,
            Audit.project_id == project_id,
            Audit.status.in_(_DASHBOARD_STATUSES),
            Audit.completed_at.is_not(None),
        )
    )
    if from_at is not None:
        stmt = stmt.where(Audit.completed_at >= from_at)
    if to_at is not None:
        stmt = stmt.where(Audit.completed_at <= to_at)
    stmt = stmt.order_by(Audit.completed_at.asc(), Audit.created_at.asc())
    result = await session.execute(stmt)
    return list(result.all())


def _trend_source(
    snapshot: MetricSnapshot,
    audit: Audit,
    logical_engine: str | None,
) -> _TrendSource | None:
    """Project one snapshot into a trend source, or ``None`` to skip it.

    An engine-filtered request reads the same snapshot's
    ``metrics.per_engine[engine]``; a snapshot that did not measure that engine
    emits no point (invariant 10).
    """
    metrics = snapshot.metrics or {}
    if logical_engine is None:
        engine_metrics = metrics
        visibility_score = snapshot.visibility_score
    else:
        per_engine = metrics.get("per_engine") or {}
        engine_metrics = per_engine.get(logical_engine)
        if not engine_metrics:
            return None
        rate = engine_metrics.get("brand_mention_rate")
        visibility_score = (
            round(float(rate) * 100, 2) if rate is not None else None
        )
    return _TrendSource(
        snapshot_id=snapshot.id,
        audit_id=snapshot.audit_id,
        completed_at=_to_utc(audit.completed_at),
        logical_engine=logical_engine,
        analyzer_version=snapshot.analyzer_version,
        scoring_rule_version=snapshot.scoring_rule_version,
        total_completed=int(engine_metrics.get("total_completed", 0) or 0),
        visibility_score=visibility_score,
        metrics=engine_metrics,
    )


def _response_sov(metrics: dict) -> float | None:
    """Response-level SOV: brand presence share vs competitor presence rates.

    Deterministically derived from the persisted brand/competitor
    response-presence rates already in the snapshot — no re-read of responses.
    """
    brand_rate = metrics.get("brand_mention_rate")
    competitor_rate = metrics.get("competitor_mention_rate") or {}
    if brand_rate is None:
        return None
    total = float(brand_rate) + sum(
        float(v) for v in competitor_rate.values() if v is not None
    )
    if total <= 0:
        return 0.0
    return round(float(brand_rate) / total, 4)


def _mention_sov_of(counts: dict, names: set[str]) -> float | None:
    """Mention-level SOV summed over every brand key present in the bucket.

    Brand naming can change across snapshots in one bucket, so the numerator
    aggregates counts across all brand keys rather than a single name.
    """
    total = sum(int(v or 0) for v in counts.values())
    if total <= 0:
        return None
    brand_total = sum(int(counts.get(name, 0) or 0) for name in names)
    return round(brand_total / total, 4)


def _trend_rankings(metrics: dict) -> list[VisibilityTrendRankingRow]:
    """Brand-vs-competitor ranking rows for a raw point (persisted counts)."""
    sov = metrics.get("share_of_voice") or {}
    counts = sov.get("mention_counts") or {}
    share = sov.get("share") or {}
    brand_name = _brand_name(counts, metrics)
    competitor_mention = metrics.get("competitor_mention_rate") or {}
    competitor_citation = metrics.get("competitor_citation_rate") or {}

    rows: list[VisibilityTrendRankingRow] = [
        VisibilityTrendRankingRow(
            name=brand_name,
            is_brand=True,
            mention_rate=metrics.get("brand_mention_rate"),
            citation_rate=metrics.get("owned_citation_rate"),
            share_of_voice=share.get(brand_name),
            mention_count=int(counts.get(brand_name, 0) or 0),
        )
    ]
    for name in competitor_mention:
        rows.append(
            VisibilityTrendRankingRow(
                name=name,
                is_brand=False,
                mention_rate=competitor_mention.get(name),
                citation_rate=competitor_citation.get(name),
                share_of_voice=share.get(name),
                mention_count=int(counts.get(name, 0) or 0),
            )
        )
    rows.sort(key=lambda r: (-(r.share_of_voice or 0.0), r.name))
    return rows


def _raw_point(source: _TrendSource) -> VisibilityTrendPoint:
    metrics = source.metrics
    sov = metrics.get("share_of_voice") or {}
    counts = sov.get("mention_counts") or {}
    brand_name = _brand_name(counts, metrics)
    return VisibilityTrendPoint(
        audit_id=source.audit_id,
        completed_at=source.completed_at,
        logical_engine=source.logical_engine,
        visibility_score=source.visibility_score,
        brand_mention_rate=metrics.get("brand_mention_rate"),
        owned_citation_rate=metrics.get("owned_citation_rate"),
        sov=VisibilityTrendSov(
            response=_response_sov(metrics),
            mention=_mention_sov_of(counts, {brand_name}),
        ),
        rankings=_trend_rankings(metrics),
        sentiment=None,
        avg_position=None,
        source_snapshot_ids=[source.snapshot_id],
        analyzer_versions=[source.analyzer_version],
        scoring_rule_versions=[source.scoring_rule_version],
        spans_version_boundary=False,
    )


def _bucket_key(completed_at: datetime, granularity: str) -> datetime:
    """UTC bucket-start boundary for a completion timestamp."""
    at = _to_utc(completed_at)
    if granularity == "month":
        return datetime(at.year, at.month, 1, tzinfo=UTC)
    # Week: ISO Monday 00:00 UTC.
    day = datetime(at.year, at.month, at.day, tzinfo=UTC)
    return day - timedelta(days=at.weekday())


def _bucket_points(
    sources: list[_TrendSource], granularity: str
) -> list[VisibilityTrendPoint]:
    """Fold sources into deterministic UTC week/month buckets.

    Under strict version bucketing, if any bucket in the selected range would
    mix analyzer/scoring versions the whole range falls back to raw points so
    no bucket ever blends incompatible formulas.
    """
    grouped: dict[datetime, list[_TrendSource]] = {}
    for source in sources:
        grouped.setdefault(_bucket_key(source.completed_at, granularity), []).append(
            source
        )

    if VISIBILITY_TRENDS_STRICT_VERSION_BUCKETS and any(
        _is_mixed_version(bucket) for bucket in grouped.values()
    ):
        return [_raw_point(source) for source in sources]

    points: list[VisibilityTrendPoint] = []
    for key in sorted(grouped):
        points.append(_fold_bucket(key, grouped[key]))
    return points


def _is_mixed_version(bucket: list[_TrendSource]) -> bool:
    analyzers = {s.analyzer_version for s in bucket}
    scorings = {s.scoring_rule_version for s in bucket}
    return len(analyzers) > 1 or len(scorings) > 1


def _fold_bucket(
    key: datetime, bucket: list[_TrendSource]
) -> VisibilityTrendPoint:
    logical_engine = bucket[0].logical_engine
    visibility = _RateAccumulator()
    brand_rate = _RateAccumulator()
    owned_rate = _RateAccumulator()
    response_sov = _RateAccumulator()
    # Summed persisted mention counts across the bucket (mention-level SOV +
    # ranking counts sum before division — never an average of shares).
    mention_counts: dict[str, int] = {}
    rankings: dict[str, _RankingAccumulator] = {}
    brand_names: set[str] = set()

    for source in bucket:
        metrics = source.metrics
        completions = source.total_completed
        visibility.add(source.visibility_score, completions)
        brand_rate.add(metrics.get("brand_mention_rate"), completions)
        owned_rate.add(metrics.get("owned_citation_rate"), completions)
        response_sov.add(_response_sov(metrics), completions)

        sov = metrics.get("share_of_voice") or {}
        counts = sov.get("mention_counts") or {}
        brand_name = _brand_name(counts, metrics)
        brand_names.add(brand_name)
        competitor_mention = metrics.get("competitor_mention_rate") or {}
        competitor_citation = metrics.get("competitor_citation_rate") or {}

        _accumulate_entity(
            rankings,
            name=brand_name,
            is_brand=True,
            mention_count=int(counts.get(brand_name, 0) or 0),
            mention_rate=metrics.get("brand_mention_rate"),
            citation_rate=metrics.get("owned_citation_rate"),
            completions=completions,
        )
        mention_counts[brand_name] = mention_counts.get(brand_name, 0) + int(
            counts.get(brand_name, 0) or 0
        )
        for name in competitor_mention:
            _accumulate_entity(
                rankings,
                name=name,
                is_brand=False,
                mention_count=int(counts.get(name, 0) or 0),
                mention_rate=competitor_mention.get(name),
                citation_rate=competitor_citation.get(name),
                completions=completions,
            )
            mention_counts[name] = mention_counts.get(name, 0) + int(
                counts.get(name, 0) or 0
            )

    total_mentions = sum(mention_counts.values())
    ranking_rows = _fold_ranking_rows(rankings, mention_counts, total_mentions)
    # Aggregate every brand key seen in the bucket for mention-level SOV so a
    # brand rename across snapshots does not undercount brand share.
    brand_keys = brand_names or {"Brand"}

    return VisibilityTrendPoint(
        audit_id=None,
        completed_at=key,
        logical_engine=logical_engine,
        visibility_score=visibility.value(),
        brand_mention_rate=brand_rate.value(),
        owned_citation_rate=owned_rate.value(),
        sov=VisibilityTrendSov(
            response=response_sov.value(),
            mention=_mention_sov_of(mention_counts, brand_keys),
        ),
        rankings=ranking_rows,
        sentiment=None,
        avg_position=None,
        source_snapshot_ids=[source.snapshot_id for source in bucket],
        analyzer_versions=sorted({s.analyzer_version for s in bucket}),
        scoring_rule_versions=sorted({s.scoring_rule_version for s in bucket}),
        spans_version_boundary=_is_mixed_version(bucket),
    )


def _accumulate_entity(
    rankings: dict[str, _RankingAccumulator],
    *,
    name: str,
    is_brand: bool,
    mention_count: int,
    mention_rate: float | None,
    citation_rate: float | None,
    completions: int,
) -> None:
    acc = rankings.get(name)
    if acc is None:
        acc = _RankingAccumulator(name=name, is_brand=is_brand)
        rankings[name] = acc
    acc.is_brand = acc.is_brand or is_brand
    acc.mention_count += mention_count
    if completions > 0:
        if mention_rate is not None:
            acc.mention_rate_weight += float(mention_rate) * completions
            acc.mention_rate_denom += completions
        if citation_rate is not None:
            acc.citation_rate_weight += float(citation_rate) * completions
            acc.citation_rate_denom += completions


def _fold_ranking_rows(
    rankings: dict[str, _RankingAccumulator],
    mention_counts: dict[str, int],
    total_mentions: int,
) -> list[VisibilityTrendRankingRow]:
    rows: list[VisibilityTrendRankingRow] = []
    for name, acc in rankings.items():
        share = (
            round(mention_counts.get(name, 0) / total_mentions, 4)
            if total_mentions > 0
            else None
        )
        rows.append(
            VisibilityTrendRankingRow(
                name=name,
                is_brand=acc.is_brand,
                mention_rate=(
                    round(acc.mention_rate_weight / acc.mention_rate_denom, 4)
                    if acc.mention_rate_denom > 0
                    else None
                ),
                citation_rate=(
                    round(acc.citation_rate_weight / acc.citation_rate_denom, 4)
                    if acc.citation_rate_denom > 0
                    else None
                ),
                share_of_voice=share,
                mention_count=acc.mention_count,
            )
        )
    rows.sort(key=lambda r: (-(r.share_of_voice or 0.0), r.name))
    return rows
