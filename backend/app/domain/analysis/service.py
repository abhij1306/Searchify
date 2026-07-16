# Analysis/metrics projections (B6, invariant 7 — read persisted analysis only).
#
# Every function here reads persisted rows (``MetricSnapshot`` /
# ``ResponseAnalysis`` / ``Citation`` / ``Audit`` / ``AuditTask``) and NEVER
# calls a provider. They back the metrics/dashboard/evidence/export endpoints.
# All queries are workspace-scoped (invariant 5).
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.audits import (
    AUDIT_STATUS_COMPLETED,
    AUDIT_STATUS_PARTIALLY_COMPLETED,
)
from app.domain.analysis.schemas import (
    CitationEvidence,
    EngineComparisonRow,
    ExecutionEvidenceResponse,
    MetricsResponse,
    RankingRow,
    VisibilityResponse,
)
from app.models.analysis import Citation, MetricSnapshot, ResponseAnalysis
from app.models.audit import Audit, AuditTask

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


async def get_execution_evidence(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    analysis_id: uuid.UUID,
) -> ExecutionEvidenceResponse:
    """Serve one execution's persisted analysis + citation evidence."""
    analysis = await session.scalar(
        select(ResponseAnalysis).where(
            ResponseAnalysis.id == analysis_id,
            ResponseAnalysis.workspace_id == workspace_id,
        )
    )
    if analysis is None:
        raise AnalysisNotFoundError("Execution analysis not found")
    citations = list(
        (
            await session.scalars(
                select(Citation)
                .where(Citation.analysis_id == analysis_id)
                .order_by(Citation.ordinal.asc())
            )
        ).all()
    )
    score = analysis.score or {}
    return ExecutionEvidenceResponse(
        id=analysis.id,
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
