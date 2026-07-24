# LLM Analytics read services (A9): the three projections behind the
# ``/projects/{id}/llm-analytics`` endpoints.
#
# Every function reads PERSISTED rows only (invariant 7): the headline +
# themes surfaces serve the persisted ``AnalyticsSnapshot`` rows built by
# the A8 refresh executor — an absent snapshot yields an EMPTY payload /
# empty list, NEVER a read-time recomputation — and the referrals
# drill-down pages the persisted ``ReferralClassification`` +
# ``ReferralEvent`` rows directly (keyset per contract C4). No provider is
# ever called. All queries are workspace-scoped (invariant 5).
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.analysis import ANALYZER_VERSION, SCORING_RULE_VERSION
from app.core.config.analytics import (
    AI_SOURCES,
    ANALYTICS_DEFAULT_GRANULARITY,
    ANALYTICS_MAX_WINDOW_DAYS,
    ANALYTICS_REFERRALS_PAGE_SIZE,
    ANALYTICS_SNAPSHOT_GRANULARITIES,
    CONFIDENCE_EXACT,
    CORRELATION_STATE_INSUFFICIENT_DATA,
)
from app.domain.analytics.ingest import metric_row_not_superseded
from app.domain.analytics.schemas import (
    AnalyticsCorrelation,
    AnalyticsEngineVisibility,
    AnalyticsReferralRow,
    AnalyticsReferralsPage,
    AnalyticsSourceBreakdownRow,
    LlmAnalyticsResponse,
    LlmAnalyticsThemeRow,
    metric_series_points,
)
from app.domain.site_health.normalization import (
    decode_keyset_cursor,
    encode_keyset_cursor,
)
from app.models.analytics import (
    AnalyticsSnapshot,
    ReferralClassification,
    ReferralEvent,
)
from app.models.integrations import IntegrationMetricRow


class AnalyticsQueryError(ValueError):
    """Raised for an invalid analytics query (bad granularity/range/source).

    The API layer maps this to HTTP 422; it is never a not-found condition.
    Mirrors the trends surface's ``TrendQueryError`` contract without
    reusing that trends-specific class (one owner per surface).
    """


class AnalyticsCursorError(ValueError):
    """A referrals cursor failed decode/scope verification (API maps to 400).

    Mirrors the site-health ``InvalidCursorError`` contract: any typed-cursor
    failure (scope/filter mismatch, tamper, malformed payload) is a client
    error, never a server fault.
    """


# Cursor endpoint scope label (the keyset fingerprint binds the cursor to
# this endpoint + the active filters — site-health convention, C4).
_REFERRALS_CURSOR_SCOPE = "llm-analytics-referrals"


def _validate_window(from_date: date | None, to_date: date | None) -> None:
    """The from/to contract: both-or-neither, ordered, within the max span."""
    if (from_date is None) != (to_date is None):
        raise AnalyticsQueryError("'from' and 'to' must be supplied together")
    if from_date is None or to_date is None:
        return
    if to_date < from_date:
        raise AnalyticsQueryError("'to' must not be before 'from'")
    if (to_date - from_date).days + 1 > ANALYTICS_MAX_WINDOW_DAYS:
        raise AnalyticsQueryError(
            f"window exceeds ANALYTICS_MAX_WINDOW_DAYS ({ANALYTICS_MAX_WINDOW_DAYS})"
        )


def _validate_granularity(granularity: str) -> str:
    granularity = granularity or ANALYTICS_DEFAULT_GRANULARITY
    if granularity not in ANALYTICS_SNAPSHOT_GRANULARITIES:
        raise AnalyticsQueryError(f"unknown granularity: {granularity!r}")
    return granularity


def _validate_source(source: str | None) -> None:
    if source is not None and source not in AI_SOURCES:
        raise AnalyticsQueryError(f"unknown ai_source: {source!r}")


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


async def _load_snapshot(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    granularity: str,
) -> AnalyticsSnapshot | None:
    """The persisted snapshot serving the request, or ``None``.

    An explicit ``from``/``to`` selects the snapshot persisted for exactly
    that window (read endpoints serve persisted snapshot windows only —
    arbitrary custom windows are never recomputed). Without a window the
    project's LATEST persisted snapshot at the granularity is served, so a
    default landing still renders the freshest projection.
    """
    stmt = (
        select(AnalyticsSnapshot)
        .where(AnalyticsSnapshot.workspace_id == workspace_id)
        .where(AnalyticsSnapshot.project_id == project_id)
        .where(AnalyticsSnapshot.granularity == granularity)
    )
    if from_date is not None and to_date is not None:
        stmt = stmt.where(AnalyticsSnapshot.window_start == from_date)
        stmt = stmt.where(AnalyticsSnapshot.window_end == to_date)
    else:
        stmt = stmt.order_by(
            AnalyticsSnapshot.window_end.desc(),
            AnalyticsSnapshot.window_start.desc(),
        )
    return await session.scalar(stmt.limit(1))


def _empty_analytics(
    *,
    project_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    granularity: str,
) -> LlmAnalyticsResponse:
    """The empty payload for an absent snapshot (never a recomputation)."""
    return LlmAnalyticsResponse(
        project_id=project_id,
        window_start=from_date.isoformat() if from_date is not None else "",
        window_end=to_date.isoformat() if to_date is not None else "",
        granularity=granularity,
        referral_volume=[],
        referral_share=[],
        sources=[],
        engine_visibility=[],
        correlation=AnalyticsCorrelation(
            state=CORRELATION_STATE_INSUFFICIENT_DATA,
            coefficient=None,
            sample_size=0,
        ),
        analyzer_version=ANALYZER_VERSION,
        formula_version=SCORING_RULE_VERSION,
    )


async def get_llm_analytics(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    from_date: date | None = None,
    to_date: date | None = None,
    granularity: str = ANALYTICS_DEFAULT_GRANULARITY,
) -> LlmAnalyticsResponse:
    """Serve the headline AEO projection from the persisted snapshot.

    The persisted ``metrics`` JSONB already carries the exact DTO fragments
    (A8 writes them in the served shape); this maps them into the strict
    response model. An absent snapshot yields the empty payload.
    """
    granularity = _validate_granularity(granularity)
    _validate_window(from_date, to_date)
    snapshot = await _load_snapshot(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        from_date=from_date,
        to_date=to_date,
        granularity=granularity,
    )
    if snapshot is None:
        return _empty_analytics(
            project_id=project_id,
            from_date=from_date,
            to_date=to_date,
            granularity=granularity,
        )

    metrics = snapshot.metrics or {}
    correlation = metrics.get("correlation") or {}
    sources = [
        AnalyticsSourceBreakdownRow(
            ai_source=str(row.get("ai_source") or ""),
            sessions=int(row.get("sessions") or 0),
            share=row.get("share"),
        )
        for row in metrics.get("sources") or []
        if isinstance(row, dict)
    ]
    engine_visibility = [
        AnalyticsEngineVisibility(
            logical_engine=str(row.get("logical_engine") or ""),
            series=metric_series_points(row.get("series")),
        )
        for row in metrics.get("engine_visibility") or []
        if isinstance(row, dict)
    ]
    return LlmAnalyticsResponse(
        project_id=project_id,
        window_start=snapshot.window_start.isoformat(),
        window_end=snapshot.window_end.isoformat(),
        granularity=snapshot.granularity,
        referral_volume=metric_series_points(metrics.get("referral_volume")),
        referral_share=metric_series_points(metrics.get("referral_share")),
        sources=sources,
        engine_visibility=engine_visibility,
        correlation=AnalyticsCorrelation(
            state=str(correlation.get("state") or CORRELATION_STATE_INSUFFICIENT_DATA),
            coefficient=correlation.get("coefficient"),
            sample_size=int(correlation.get("sample_size") or 0),
        ),
        analyzer_version=snapshot.analyzer_version,
        formula_version=snapshot.formula_version,
    )


def _referrals_filters(
    *,
    project_id: uuid.UUID,
    source: str | None,
    from_date: date | None,
    to_date: date | None,
) -> dict[str, object]:
    """The active filter set the keyset cursor is fingerprint-bound to."""
    return {
        "project_id": str(project_id),
        "source": source or "",
        "from": from_date.isoformat() if from_date is not None else "",
        "to": to_date.isoformat() if to_date is not None else "",
    }


def _decode_referrals_cursor(
    cursor: str, *, filters: dict[str, object]
) -> tuple[datetime, uuid.UUID]:
    """Decode the ``(occurred_at, id)`` keyset cursor (400 on any failure)."""
    try:
        occurred_raw, id_raw = decode_keyset_cursor(
            cursor, scope=_REFERRALS_CURSOR_SCOPE, filters=filters
        )
        return datetime.fromisoformat(occurred_raw), uuid.UUID(id_raw)
    except ValueError as exc:
        # CursorScopeError is a ValueError subclass — one branch covers it.
        raise AnalyticsCursorError(str(exc)) from exc


async def get_llm_analytics_referrals(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    source: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    cursor: str | None = None,
) -> AnalyticsReferralsPage:
    """Page the persisted classified-referral rows (keyset, contract C4).

    A pure read of persisted ``ReferralClassification`` + ``ReferralEvent``
    rows (invariant 7) in deterministic newest-first order
    ``(occurred_at desc, id desc)``; the opaque cursor is fingerprint-bound
    to this endpoint + the active filters, so a replay against a different
    source/window is rejected (400) instead of silently skipping rows.
    Only events whose source metric row is at the LATEST ``resync_seq``
    per row identity are listed — a re-sync ingests a second copy of each
    logical referral, and the superseded revision is stale evidence (the
    snapshot builder folds the same way; events with no metric-row link
    pass — they are not re-sync duplicates).
    """
    _validate_source(source)
    _validate_window(from_date, to_date)
    filters = _referrals_filters(
        project_id=project_id, source=source, from_date=from_date, to_date=to_date
    )
    keyset: tuple[datetime, uuid.UUID] | None = None
    if cursor:
        keyset = _decode_referrals_cursor(cursor, filters=filters)

    stmt = (
        select(ReferralClassification, ReferralEvent)
        .join(
            ReferralEvent,
            ReferralEvent.id == ReferralClassification.referral_event_id,
        )
        .outerjoin(
            IntegrationMetricRow,
            IntegrationMetricRow.id == ReferralEvent.source_metric_row_id,
        )
        .where(ReferralClassification.workspace_id == workspace_id)
        .where(ReferralClassification.project_id == project_id)
        .where(metric_row_not_superseded())
    )
    if source is not None:
        stmt = stmt.where(ReferralClassification.ai_source == source)
    if from_date is not None:
        stmt = stmt.where(ReferralEvent.occurred_at >= _day_start(from_date))
    if to_date is not None:
        stmt = stmt.where(
            ReferralEvent.occurred_at < _day_start(to_date + timedelta(days=1))
        )
    if keyset is not None:
        cur_occurred, cur_id = keyset
        stmt = stmt.where(
            or_(
                ReferralEvent.occurred_at < cur_occurred,
                and_(
                    ReferralEvent.occurred_at == cur_occurred,
                    ReferralEvent.id < cur_id,
                ),
            )
        )
    stmt = stmt.order_by(
        ReferralEvent.occurred_at.desc(), ReferralEvent.id.desc()
    ).limit(ANALYTICS_REFERRALS_PAGE_SIZE + 1)

    rows = list((await session.execute(stmt)).tuples().all())
    next_cursor: str | None = None
    if len(rows) > ANALYTICS_REFERRALS_PAGE_SIZE:
        rows = rows[:ANALYTICS_REFERRALS_PAGE_SIZE]
        _last_classification, last_event = rows[-1]
        next_cursor = encode_keyset_cursor(
            scope=_REFERRALS_CURSOR_SCOPE,
            filters=filters,
            sort_values=[last_event.occurred_at.isoformat(), str(last_event.id)],
        )

    items = [
        AnalyticsReferralRow(
            id=classification.id,
            occurred_at=event.occurred_at.isoformat(),
            landing_url=event.landing_url,
            referrer_host=event.referrer_host or None,
            is_ai_referral=bool(classification.is_ai_referral),
            ai_source=classification.ai_source,
            logical_engine=classification.logical_engine or None,
            # A non-AI row persists an empty confidence: the no-match
            # verdict of the deterministic rule table is itself exact (DTO
            # contract — see schemas.py).
            confidence=classification.confidence or CONFIDENCE_EXACT,
            match_signal=classification.match_signal or None,
        )
        for classification, event in rows
    ]
    return AnalyticsReferralsPage(items=items, next_cursor=next_cursor)


async def get_llm_analytics_themes(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[LlmAnalyticsThemeRow]:
    """Serve the theme rollup folded into the persisted snapshot.

    The rollup is granularity-independent (a window-level fold stored
    identically in every granularity's snapshot), so the endpoint reads the
    default-granularity snapshot for the window. An absent snapshot yields
    an empty list — never a recomputation (invariant 7).
    """
    _validate_window(from_date, to_date)
    snapshot = await _load_snapshot(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        from_date=from_date,
        to_date=to_date,
        granularity=ANALYTICS_DEFAULT_GRANULARITY,
    )
    if snapshot is None:
        return []
    themes = (snapshot.metrics or {}).get("themes") or []
    return [
        LlmAnalyticsThemeRow(
            theme=str(row.get("theme") or ""),
            intent=str(row.get("intent") or ""),
            total_completed=int(row.get("total_completed") or 0),
            brand_mention_rate=row.get("brand_mention_rate"),
            visibility_score=row.get("visibility_score"),
            share_of_voice=row.get("share_of_voice"),
        )
        for row in themes
        if isinstance(row, dict)
    ]
