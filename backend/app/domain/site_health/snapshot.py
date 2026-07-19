# Canonical crawl-aggregate snapshot/summary (single algorithm, no duplication).
#
# The ONE place the crawl-level ``SiteHealthSnapshot`` + the crawl's rolled-up
# ``score_summary`` are computed from persisted analyses. It aggregates only the
# LATEST completed analysis per ACTIVE monitored URL (missing/errored URLs are
# never fabricated as zero), rolls up the issue severity/category counts, and
# writes both the immutable snapshot row and the crawl projection field.
#
# Two callers share this exact algorithm:
#   - the worker's ``_reconcile_crawl_status`` on clean analysis terminalization;
#   - ``service.cancel_crawl`` when a cooperative cancel stops a run that has
#     already produced completed analyses — so a partial cancel still surfaces a
#     dashboard (partial scores + inventory) instead of a null ``score_summary``.
#
# Idempotent per crawl: the ``site_health_snapshots`` table is unique on
# ``crawl_id``, so this uses ``ON CONFLICT DO NOTHING`` for the immutable row and
# always (re)writes the crawl ``score_summary`` projection.
#
# The single fetched aggregate row set is authoritative — when it is empty the
# helper writes nothing and returns ``False`` (cancel), unless the caller passes
# ``persist_empty=True`` to force a canonical empty/null-score snapshot (the
# worker's clean terminalization). There is deliberately no separate precheck
# (that would be a TOCTOU race against membership/analysis changes).
from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import Row, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.site_health.scoring import (
    AnalysisScoreInput,
    aggregate_scores,
)
from app.core.config.site_health import (
    ANALYZER_VERSION,
    PAGE_ANALYSIS_STATUS_COMPLETED,
    SCORING_VERSION,
)
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteHealthSnapshot,
    SiteIssue,
    SitePageAnalysis,
)

__all__ = ["persist_crawl_snapshot"]


async def persist_crawl_snapshot(
    session: AsyncSession, *, crawl: SiteCrawl, persist_empty: bool = False
) -> bool:
    """Compute + persist the crawl aggregate snapshot (unique per crawl).

    Aggregates only the LATEST completed analyses for ACTIVE monitored URLs
    (ignoring missing/errored URLs — never a fabricated zero), rolls up the
    issue severity/category counts, and writes both the immutable
    ``SiteHealthSnapshot`` (``ON CONFLICT DO NOTHING`` — one per crawl) and the
    crawl's rolled-up ``score_summary`` projection.

    The single fetched aggregate row set is authoritative — there is no separate
    precheck (which would be a TOCTOU race against membership/analysis changes).
    When that row set is empty (zero aggregatable active completed analyses) the
    behaviour depends on ``persist_empty``:

      - ``persist_empty=False`` (default; used by ``service.cancel_crawl``):
        write NEITHER the snapshot NOR the ``score_summary`` projection and
        return ``False``. A partial cancel with nothing aggregable (e.g. its
        only completed analysis belongs to a since-deactivated URL) keeps
        ``score_summary`` null so the UI shows its terminal/selection state
        instead of an empty dashboard from zero aggregated rows.
      - ``persist_empty=True`` (used by the worker's clean terminalization):
        still write the explicit empty/null-score snapshot + projection, so an
        empty-plan crawl terminalizes with a canonical (zeroed) snapshot.

    Returns ``True`` when a snapshot/projection was (re)written, ``False`` when
    persistence was skipped because the aggregate was empty.
    """
    # Exactly one latest completed analysis per ACTIVE monitored URL in this
    # crawl. Rank by the full timestamp, then UUID for a deterministic tie-break
    # (never truncate timestamps to whole seconds).
    ranked = (
        select(
            SitePageAnalysis.id.label("id"),
            SitePageAnalysis.site_url_id.label("site_url_id"),
            SitePageAnalysis.artifact_id.label("artifact_id"),
            SitePageAnalysis.technical_score.label("technical_score"),
            SitePageAnalysis.aeo_score.label("aeo_score"),
            SitePageAnalysis.overall_score.label("overall_score"),
            func.row_number()
            .over(
                partition_by=SitePageAnalysis.site_url_id,
                order_by=(
                    SitePageAnalysis.created_at.desc(),
                    SitePageAnalysis.id.desc(),
                ),
            )
            .label("latest_rank"),
        )
        .join(
            MonitoredSiteUrl,
            MonitoredSiteUrl.site_url_id == SitePageAnalysis.site_url_id,
        )
        .where(
            SitePageAnalysis.crawl_id == crawl.id,
            SitePageAnalysis.status == PAGE_ANALYSIS_STATUS_COMPLETED,
            MonitoredSiteUrl.project_id == crawl.project_id,
            MonitoredSiteUrl.active.is_(True),
        )
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                ranked.c.id,
                ranked.c.site_url_id,
                ranked.c.artifact_id,
                ranked.c.technical_score,
                ranked.c.aeo_score,
                ranked.c.overall_score,
            ).where(ranked.c.latest_rank == 1)
        )
    ).all()

    # The single fetched aggregate row set decides persistence — no separate
    # precheck (which would race membership/analysis changes). Zero aggregatable
    # active completed analyses => write nothing unless the caller explicitly
    # wants an empty/null-score snapshot (the worker's empty-plan terminalize).
    if not rows and not persist_empty:
        return False

    inputs: list[AnalysisScoreInput] = []
    analysis_ids: list[uuid.UUID] = []
    artifact_ids: list[uuid.UUID] = []
    for row in rows:
        analysis_ids.append(row.id)
        artifact_ids.append(row.artifact_id)
        inputs.append(
            AnalysisScoreInput(
                url_key=str(row.site_url_id),
                ordinal=0,
                technical_score=row.technical_score,
                aeo_score=row.aeo_score,
                overall_score=row.overall_score,
            )
        )
    aggregate = aggregate_scores(inputs)

    # Issue severity/category rollups for this crawl.
    severity_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    issue_total = 0
    evaluation_ids: list[uuid.UUID] = []
    issue_rows: Sequence[Row[tuple[str, str, uuid.UUID]]] = []
    if analysis_ids:
        issue_rows = (
            await session.execute(
                select(
                    SiteIssue.severity,
                    SiteIssue.category,
                    SiteIssue.evaluation_id,
                ).where(SiteIssue.analysis_id.in_(analysis_ids))
            )
        ).all()
    for severity, category, evaluation_id in issue_rows:
        issue_total += 1
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
        evaluation_ids.append(evaluation_id)

    selected_url_count = int(
        await session.scalar(
            select(func.count())
            .select_from(MonitoredSiteUrl)
            .where(
                MonitoredSiteUrl.project_id == crawl.project_id,
                MonitoredSiteUrl.active.is_(True),
            )
        )
        or 0
    )

    # One immutable snapshot per crawl. ``ON CONFLICT DO NOTHING`` makes this
    # safe if the worker and a cancel both reach terminalization (the earliest
    # writer wins; the crawl ``score_summary`` projection below is still
    # (re)written so the DTO reflects the same aggregate).
    await session.execute(
        pg_insert(SiteHealthSnapshot)
        .values(
            workspace_id=crawl.workspace_id,
            project_id=crawl.project_id,
            crawl_id=crawl.id,
            selected_url_count=selected_url_count,
            analyzed_url_count=aggregate.analyzed_url_count,
            technical_score=aggregate.technical_score,
            aeo_score=aggregate.aeo_score,
            overall_score=aggregate.overall_score,
            issue_count=issue_total,
            severity_counts=severity_counts,
            category_counts=category_counts,
            source_analysis_ids=analysis_ids,
            source_artifact_ids=artifact_ids,
            source_evaluation_ids=evaluation_ids,
            analyzer_version=crawl.analyzer_version or ANALYZER_VERSION,
            scoring_version=crawl.scoring_version or SCORING_VERSION,
        )
        .on_conflict_do_nothing(
            constraint="uq_site_health_snapshot_crawl",
        )
    )
    crawl.score_summary = {
        "technical_score": aggregate.technical_score,
        "aeo_score": aggregate.aeo_score,
        "overall_score": aggregate.overall_score,
        "analyzed_url_count": aggregate.analyzed_url_count,
        "selected_count": selected_url_count,
        "issue_count": issue_total,
        "scoring_version": aggregate.scoring_version,
    }
    return True
