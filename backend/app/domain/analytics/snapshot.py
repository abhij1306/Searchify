# AnalyticsSnapshot builder + ``analytics_snapshot_refresh`` executor (A8).
#
# The C5 referral chain's third link: rebuild the LLM-Analytics projection
# for one (project, window) from PERSISTED evidence only — the
# ``ReferralClassification`` + ``ReferralEvent`` rows landed by the
# ingest/classify links (each event joined to the ``IntegrationMetricRow``
# it was projected from) and the per-run ``MetricSnapshot`` / per-execution
# ``ResponseAnalysis`` rows the audit pipeline already persists. NO provider
# I/O anywhere (invariant 7) and NO LLM (invariant 9): every number below is
# a deterministic fold of persisted rows.
#
# FORMULAS (all folds share ONE session measure, documented so a reader can
# reproduce every number):
#   - Referral facts: one fact per classification whose event still resolves
#     to a metric row, keeping only the LATEST ``resync_seq`` per metric-row
#     identity ``(property_ref, provider, dataset, date, dimension_key)`` —
#     a fact backed by a superseded revision is stale evidence and never
#     folds in (its replacement enters via its own event). An event whose
#     ``source_metric_row_id`` is NULL (the row was deleted) carries no
#     session measure and is excluded.
#   - ``sessions`` per fact = the metric row's ``metrics["sessions"]`` (a
#     missing/non-numeric value counts as 0).
#   - ai_sessions(bucket)   = Σ sessions over the bucket's AI facts.
#     total_sessions(bucket) = Σ sessions over ALL the bucket's facts (AI +
#     non-AI) — numerator and denominator are drawn from the IDENTICAL
#     latest-revision row set (the C1 referral datasets), so the ratio is
#     internally consistent.
#   - referral_volume point = ai_sessions when the bucket has ANY folded
#     referral fact (a measured zero is 0), else ``None`` (no measurement —
#     a chart gap, never a coerced zero).
#   - referral_share point  = ai_sessions / total_sessions when
#     total_sessions > 0, else ``None``.
#   - sources breakdown (window-level): per ``ai_source`` Σ sessions over AI
#     facts; ``share`` = source sessions / window total_sessions (the same
#     denominator as the share series). Only sources with sessions > 0 are
#     listed, ordered sessions desc then ``ai_source`` asc. Non-AI referrals
#     (``other``) never appear — the breakdown is over AI sources only.
#   - engine_visibility: per logical engine, the per-bucket
#     completion-weighted mean of the folded ``MetricSnapshot`` per-engine
#     visibility scores (``per_engine[engine].brand_mention_rate * 100``,
#     mirroring ``visibility_score``), rounded to 2 decimals; ``None`` for a
#     bucket with no snapshot covering that engine.
#   - correlation: DAY-aligned (granularity-independent). x = the day's
#     completion-weighted mean ``visibility_score``; y = the day's AI
#     sessions. Aligned pairs = days having BOTH values, sorted by day.
#     Pearson product-moment over the aligned pairs; fewer than
#     ``CORRELATION_MIN_SAMPLE`` pairs — or a zero-variance axis, where
#     Pearson is undefined — reports ``insufficient_data`` with a NULL
#     coefficient, NEVER a fabricated number.
#   - themes (window-level): per-execution ``ResponseAnalysis`` rows joined
#     to the frozen ``AuditPromptSnapshot`` on ``(audit_id, prompt_index)``,
#     grouped by ``(theme, intent)``: ``total_completed`` = executions;
#     ``brand_mention_rate`` = brand-mentioned executions / total (rounded
#     to 4 like the run aggregate); ``visibility_score`` = rate * 100
#     (rounded to 2, mirroring the run-level formula);
#     ``share_of_voice`` = brand mentions / (brand + competitor mention
#     incidences), ``None`` when the group has no mentions at all.
#
# Visibility + theme inputs come only from audits in the dashboard statuses
# (completed / partially_completed — the ONE owner tuple in
# ``domain/analysis/service.py``) completed inside the window.
#
# Idempotent: recomputing from the same persisted rows rewrites the SAME
# snapshot rows in place via ``INSERT ... ON CONFLICT (project_id,
# window_start, window_end, granularity) DO UPDATE`` (precedent:
# ``domain/traffic/service.py``), so a re-run never duplicates. Provenance
# (invariant 4): ``source_classification_ids`` = the folded classification
# ids (AI and non-AI — both feed the share), ``source_snapshot_ids`` = the
# folded ``MetricSnapshot`` ids; analyzer/formula versions reuse the
# config/analysis.py constants (llm-analytics.md section 8, invariant 2).
# Cooperative cancel is honored at every classification batch boundary
# (invariant 9) — the write phase is a single transaction, so a cancelled
# run leaves no partial projection behind.
from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ``ANALYZER_VERSION`` / ``SCORING_RULE_VERSION`` are OWNED by
# config/analysis.py and reused for the snapshot provenance stamps
# (invariant 2) — never the same-named site-health constants.
from app.core.config.analysis import ANALYZER_VERSION, SCORING_RULE_VERSION
from app.core.config.analytics import (
    ANALYTICS_SNAPSHOT_GRANULARITIES,
    CORRELATION_MIN_SAMPLE,
    CORRELATION_STATE_INSUFFICIENT_DATA,
    CORRELATION_STATE_OK,
)
from app.core.config.task_queue import TASK_TERMINAL_STATUSES

# The dashboard-status audit tuple (completed | partially_completed) is
# OWNED by the analysis projections service — imported, never re-derived
# (invariant 2; the visibility/theme folds must measure the same audit
# population the Visibility dashboard serves).
from app.domain.analysis.service import _DASHBOARD_STATUSES
from app.domain.analytics.tasks import TaskCancelledError

# Calendar bucketing (day | ISO-Monday week | 1st-of-month, first label
# clamped to the window) is OWNED by the Traffic projection — the two
# projections share the granularity vocabulary, so the bucket math has one
# owner too (invariant 2).
from app.domain.traffic.projection import bucket_labels, bucket_start
from app.models.analysis import MetricSnapshot, ResponseAnalysis
from app.models.analytics import (
    AnalyticsSnapshot,
    AnalyticsTask,
    ReferralClassification,
    ReferralEvent,
)
from app.models.audit import Audit, AuditPromptSnapshot
from app.models.integrations import IntegrationMetricRow

# Bounded work per read batch: each batch is one cooperative-cancel boundary
# (the WRITE phase is a single transaction). Module constant (not config) —
# the same precedent as A6's ``_CLASSIFY_BATCH_SIZE``; tests monkeypatch it
# down to 1 to exercise the boundary per row.
_CLASSIFICATION_BATCH_SIZE = 1000

# Rounding conventions mirrored from the run-level aggregate
# (``analysis/scoring.py``): rates round to 4, visibility scores to 2. The
# correlation coefficient rounds to 6 so re-runs serialize identically.
_RATE_DECIMALS = 4
_SCORE_DECIMALS = 2
_CORRELATION_DECIMALS = 6


# --- Pure projection inputs (the executor reduces ORM rows to these) ---------


@dataclass(frozen=True)
class ReferralFactInput:
    """One classification + event (+ source metric row) reduced for the fold.

    ``row_identity`` is the metric row's
    ``(property_ref, provider, dataset, date, dimension_key)`` revision
    identity — ``None`` when the event's ``source_metric_row_id`` is NULL
    (no session measure; the fact is excluded by latest-selection).
    ``occurred_date`` is the EVENT's UTC date (the referral evidence's own
    bucket key); ``sessions`` is the row's measured sessions (0 when the
    metric payload lacks a numeric ``sessions``).
    """

    classification_id: uuid.UUID
    is_ai_referral: bool
    ai_source: str
    occurred_date: date
    sessions: int
    row_identity: tuple[str, str, str, date, str] | None
    resync_seq: int


@dataclass(frozen=True)
class VisibilityFactInput:
    """One folded ``MetricSnapshot`` reduced for the visibility series.

    ``engine_scores`` carries the snapshot's per-engine visibility scores
    (0-100) as sorted ``(logical_engine, score)`` pairs; ``visibility_score``
    is the project-level 0-100 score used by the correlation fold.
    """

    snapshot_id: uuid.UUID
    completed_date: date
    visibility_score: float
    total_completed: int
    engine_scores: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class ThemeFactInput:
    """One per-execution analysis reduced for the theme rollup.

    ``competitors_mentioned`` is the count of DISTINCT competitors credited
    with a mention in this execution (each competitor contributes at most
    one mention incidence per execution — the run-level SOV definition).
    """

    theme: str
    intent: str
    brand_mentioned: bool
    competitors_mentioned: int


@dataclass(frozen=True)
class AnalyticsProjection:
    """The full projection for one (window, granularity), ready to persist.

    ``metrics`` carries the exact DTO fragments the read API serves
    (series / sources / engine visibility / correlation / themes); the
    top-level provenance lists are the folded evidence ids (sorted string
    UUIDs, so re-runs serialize identically).
    """

    granularity: str
    metrics: dict[str, Any]
    source_classification_ids: list[str]
    source_snapshot_ids: list[str]


# --- Pure math ---------------------------------------------------------------


def select_latest_referral_facts(
    facts: Sequence[ReferralFactInput],
) -> list[ReferralFactInput]:
    """Keep the latest ``resync_seq`` fact per metric-row identity.

    A fact whose event points at a revision superseded by a later re-sync
    is stale evidence and never folds in (its replacement enters via its
    own classified event). Facts with NO metric row (``row_identity``
    ``None``) carry no session measure and are excluded. The result is
    sorted deterministically so downstream float aggregation is
    order-independent (invariant 9).
    """
    latest: dict[tuple[str, str, str, date, str], ReferralFactInput] = {}
    for fact in facts:
        if fact.row_identity is None:
            continue
        current = latest.get(fact.row_identity)
        if current is None or fact.resync_seq > current.resync_seq:
            latest[fact.row_identity] = fact
    return sorted(
        latest.values(),
        key=lambda fact: (fact.occurred_date, str(fact.classification_id)),
    )


def pearson_coefficient(
    xs: Sequence[float], ys: Sequence[float]
) -> float | None:
    """Deterministic Pearson product-moment correlation coefficient.

    Returns ``None`` when the coefficient is undefined: empty input or a
    zero-variance axis (the denominator would be 0 — never a fabricated
    number). Raises ``ValueError`` on mismatched lengths (a caller bug).
    """
    if len(xs) != len(ys):
        raise ValueError("pearson inputs must have equal lengths")
    n = len(xs)
    if n == 0:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    if sxx == 0.0 or syy == 0.0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return sxy / math.sqrt(sxx * syy)


def correlation_summary(
    pairs: Sequence[tuple[float, float]],
) -> dict[str, Any]:
    """The visibility<->referral correlation summary for aligned day-pairs.

    Below ``CORRELATION_MIN_SAMPLE`` aligned pairs — or an undefined
    (zero-variance) coefficient — the state is ``insufficient_data`` with a
    NULL coefficient; only a real, defined coefficient reports ``ok``.
    """
    sample_size = len(pairs)
    insufficient = {
        "state": CORRELATION_STATE_INSUFFICIENT_DATA,
        "coefficient": None,
        "sample_size": sample_size,
    }
    if sample_size < CORRELATION_MIN_SAMPLE:
        return insufficient
    coefficient = pearson_coefficient(
        [x for x, _y in pairs], [y for _x, y in pairs]
    )
    if coefficient is None:
        return insufficient
    return {
        "state": CORRELATION_STATE_OK,
        "coefficient": round(coefficient, _CORRELATION_DECIMALS),
        "sample_size": sample_size,
    }


def _series_point(label: date, value: int | float | None) -> dict[str, Any]:
    return {"date": label.isoformat(), "value": value}


def _weighted_mean(pairs: Sequence[tuple[float, int]]) -> float | None:
    """Completion-weighted mean of (value, weight); None when weightless."""
    total_weight = sum(weight for _value, weight in pairs)
    if total_weight == 0:
        return None
    return sum(value * weight for value, weight in pairs) / total_weight


def build_analytics_projection(
    *,
    referral_facts: Sequence[ReferralFactInput],
    visibility_facts: Sequence[VisibilityFactInput],
    theme_facts: Sequence[ThemeFactInput],
    window_start: date,
    window_end: date,
    granularity: str,
) -> AnalyticsProjection:
    """Fold the reduced inputs into one snapshot's metrics + provenance.

    PURE: no DB, no network, no clock — the same inputs always yield
    byte-identical metrics and provenance (invariants 7 + 9).
    Latest-``resync_seq`` selection is applied INSIDE so a stale revision
    can never leak in, and the module docstring documents every formula.
    """
    if granularity not in ANALYTICS_SNAPSHOT_GRANULARITIES:
        raise ValueError(f"unknown analytics granularity: {granularity!r}")
    if window_end < window_start:
        raise ValueError("analytics window_end before window_start")

    latest = select_latest_referral_facts(referral_facts)
    labels = bucket_labels(window_start, window_end, granularity)

    # --- Referral volume / share series + the per-source breakdown -------
    bucket_ai: dict[date, int] = {}
    bucket_total: dict[date, int] = {}
    bucket_measured: dict[date, bool] = {}
    source_sessions: dict[str, int] = {}
    window_total = 0
    for fact in latest:
        if not (window_start <= fact.occurred_date <= window_end):
            continue  # defensive: the executor's query already scopes this
        bucket = bucket_start(fact.occurred_date, granularity)
        bucket_measured[bucket] = True
        bucket_total[bucket] = bucket_total.get(bucket, 0) + fact.sessions
        window_total += fact.sessions
        if fact.is_ai_referral:
            bucket_ai[bucket] = bucket_ai.get(bucket, 0) + fact.sessions
            source_sessions[fact.ai_source] = (
                source_sessions.get(fact.ai_source, 0) + fact.sessions
            )

    referral_volume: list[dict[str, Any]] = []
    referral_share: list[dict[str, Any]] = []
    for label in labels:
        # The label's natural bucket (the first label may be window-clamped).
        bucket = bucket_start(label, granularity)
        if not bucket_measured.get(bucket, False):
            referral_volume.append(_series_point(label, None))
            referral_share.append(_series_point(label, None))
            continue
        ai_sessions = bucket_ai.get(bucket, 0)
        total_sessions = bucket_total.get(bucket, 0)
        referral_volume.append(_series_point(label, ai_sessions))
        share = ai_sessions / total_sessions if total_sessions > 0 else None
        referral_share.append(_series_point(label, share))

    sources = [
        {
            "ai_source": ai_source,
            "sessions": sessions,
            "share": (sessions / window_total) if window_total > 0 else None,
        }
        for ai_source, sessions in source_sessions.items()
        if sessions > 0
    ]
    sources.sort(key=lambda row: (-row["sessions"], row["ai_source"]))

    # --- Per-engine visibility series --------------------------------------
    engines = sorted(
        {engine for fact in visibility_facts for engine, _s in fact.engine_scores}
    )
    bucket_engine: dict[tuple[date, str], list[tuple[float, int]]] = {}
    for fact in visibility_facts:
        if not (window_start <= fact.completed_date <= window_end):
            continue  # defensive: the executor's query already scopes this
        bucket = bucket_start(fact.completed_date, granularity)
        for engine, score in fact.engine_scores:
            bucket_engine.setdefault((bucket, engine), []).append(
                (score, fact.total_completed)
            )
    engine_visibility = [
        {
            "logical_engine": engine,
            "series": [
                _series_point(
                    label,
                    (
                        round(mean, _SCORE_DECIMALS)
                        if (
                            mean := _weighted_mean(
                                bucket_engine.get(
                                    (bucket_start(label, granularity), engine),
                                    [],
                                )
                            )
                        )
                        is not None
                        else None
                    ),
                )
                for label in labels
            ],
        }
        for engine in engines
    ]

    # --- Correlation (ALWAYS day-aligned, granularity-independent) --------
    day_visibility: dict[date, list[tuple[float, int]]] = {}
    for fact in visibility_facts:
        if window_start <= fact.completed_date <= window_end:
            day_visibility.setdefault(fact.completed_date, []).append(
                (fact.visibility_score, fact.total_completed)
            )
    day_ai: dict[date, int] = {}
    for fact in latest:
        if fact.is_ai_referral and window_start <= fact.occurred_date <= window_end:
            day_ai[fact.occurred_date] = (
                day_ai.get(fact.occurred_date, 0) + fact.sessions
            )
    aligned: list[tuple[float, float]] = []
    for day in sorted(day_visibility):
        mean = _weighted_mean(day_visibility[day])
        if mean is None or day not in day_ai:
            continue
        aligned.append((mean, float(day_ai[day])))
    correlation = correlation_summary(aligned)

    # --- Theme rollup (window-level) ---------------------------------------
    theme_groups: dict[tuple[str, str], list[ThemeFactInput]] = {}
    for fact in theme_facts:
        theme_groups.setdefault((fact.theme, fact.intent), []).append(fact)
    themes: list[dict[str, Any]] = []
    for (theme, intent), group in sorted(theme_groups.items()):
        total_completed = len(group)
        brand_mentions = sum(1 for fact in group if fact.brand_mentioned)
        competitor_incidences = sum(fact.competitors_mentioned for fact in group)
        mention_volume = brand_mentions + competitor_incidences
        brand_mention_rate = (
            round(brand_mentions / total_completed, _RATE_DECIMALS)
            if total_completed > 0
            else None
        )
        themes.append(
            {
                "theme": theme,
                "intent": intent,
                "total_completed": total_completed,
                "brand_mention_rate": brand_mention_rate,
                "visibility_score": (
                    round(brand_mention_rate * 100, _SCORE_DECIMALS)
                    if brand_mention_rate is not None
                    else None
                ),
                "share_of_voice": (
                    round(brand_mentions / mention_volume, _RATE_DECIMALS)
                    if mention_volume > 0
                    else None
                ),
            }
        )

    return AnalyticsProjection(
        granularity=granularity,
        metrics={
            "referral_volume": referral_volume,
            "referral_share": referral_share,
            "sources": sources,
            "engine_visibility": engine_visibility,
            "correlation": correlation,
            "themes": themes,
        },
        source_classification_ids=sorted(
            str(fact.classification_id) for fact in latest
        ),
        source_snapshot_ids=sorted(
            str(fact.snapshot_id) for fact in visibility_facts
        ),
    )


# --- Executor ----------------------------------------------------------------


async def _raise_if_task_terminal(
    session_factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID | None
) -> None:
    """Cooperative-cancel boundary check (invariant 9).

    Mirrors the A6 idiom (``domain/analytics/tasks.py``): re-read the queue
    row in a FRESH session (never the work session's possibly-stale identity
    map) and stop if it reached a terminal status. The refresh writes
    nothing before its single write transaction, so stopping here leaves no
    partial projection behind. A row that does not resolve (unpersisted
    direct-invocation fixture) has nothing to cancel against.
    """
    if task_id is None:  # unpersisted fixture row: nothing to cancel against
        return
    async with session_factory() as session:
        row = await session.get(AnalyticsTask, task_id)
        status = row.status if row is not None else None
    if status is not None and status in TASK_TERMINAL_STATUSES:
        raise TaskCancelledError(
            f"analytics task {task_id} reached terminal status {status!r}; "
            "stopping at the classification batch boundary"
        )


def _payload_window(task: AnalyticsTask) -> tuple[date, date]:
    payload = task.payload or {}
    raw_start = payload.get("window_start")
    raw_end = payload.get("window_end")
    if not raw_start or not raw_end:
        raise ValueError(
            "analytics_snapshot_refresh payload missing window_start/window_end"
        )
    window_start = date.fromisoformat(str(raw_start))
    window_end = date.fromisoformat(str(raw_end))
    if window_end < window_start:
        raise ValueError(
            "analytics_snapshot_refresh window_end before window_start"
        )
    return window_start, window_end


def _window_bounds(
    window_start: date, window_end: date
) -> tuple[datetime, datetime]:
    """The inclusive-window UTC datetimes [start 00:00, end+1day 00:00)."""
    start_dt = datetime.combine(window_start, time.min, tzinfo=UTC)
    end_dt = datetime.combine(window_end + timedelta(days=1), time.min, tzinfo=UTC)
    return start_dt, end_dt


async def _classification_batch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    window_start: date,
    window_end: date,
    after_id: uuid.UUID | None,
    limit: int,
) -> list[tuple[ReferralClassification, ReferralEvent, IntegrationMetricRow | None]]:
    """One keyset batch of classification+event+metric-row triples.

    Workspace + project scoped (invariant 5); the classification-id keyset
    order keeps the scan stable across batches. The metric row is an OUTER
    join (the event survives its deletion as a NULL link); latest-
    ``resync_seq`` selection is applied by the pure projection (one owner
    of the rule), not here.
    """
    start_dt, end_dt = _window_bounds(window_start, window_end)
    stmt = (
        select(ReferralClassification, ReferralEvent, IntegrationMetricRow)
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
        .where(ReferralEvent.occurred_at >= start_dt)
        .where(ReferralEvent.occurred_at < end_dt)
        .order_by(ReferralClassification.id.asc())
        .limit(limit)
    )
    if after_id is not None:
        stmt = stmt.where(ReferralClassification.id > after_id)
    return list((await session.execute(stmt)).tuples().all())


def _sessions(metrics: dict | None) -> int:
    """The row's session measure: a missing/non-numeric value counts as 0."""
    value = (metrics or {}).get("sessions")
    return int(value) if isinstance(value, (int, float)) else 0


def _to_referral_input(
    classification: ReferralClassification,
    event: ReferralEvent,
    row: IntegrationMetricRow | None,
) -> ReferralFactInput:
    return ReferralFactInput(
        classification_id=classification.id,
        is_ai_referral=bool(classification.is_ai_referral),
        ai_source=classification.ai_source,
        occurred_date=event.occurred_at.date(),
        sessions=_sessions(row.metrics if row is not None else None),
        row_identity=(
            (
                row.property_ref,
                row.provider,
                row.dataset,
                row.date,
                row.dimension_key,
            )
            if row is not None
            else None
        ),
        resync_seq=row.resync_seq if row is not None else 0,
    )


def _engine_scores(metrics: dict | None) -> tuple[tuple[str, float], ...]:
    """The snapshot's per-engine visibility scores (0-100), sorted.

    Mirrors the run-level headline (``visibility_score`` =
    ``brand_mention_rate * 100``) per engine; an engine with no numeric
    rate in this snapshot's ``per_engine`` block contributes nothing.
    """
    per_engine = (metrics or {}).get("per_engine") or {}
    scores: list[tuple[str, float]] = []
    for engine, aggregate in per_engine.items():
        rate = (aggregate or {}).get("brand_mention_rate")
        if isinstance(rate, (int, float)):
            scores.append((str(engine), float(rate) * 100.0))
    return tuple(sorted(scores))


async def _visibility_facts(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    window_start: date,
    window_end: date,
) -> list[VisibilityFactInput]:
    """The window's folded ``MetricSnapshot`` rows (dashboard audits only).

    Mirrors the trends query pattern (``domain/analysis/service.py``):
    workspace/project scoped, dashboard statuses, non-null completion,
    inclusive UTC window — a pure read of persisted rows (invariant 7).
    """
    start_dt, end_dt = _window_bounds(window_start, window_end)
    stmt = (
        select(MetricSnapshot, Audit.completed_at)
        .join(Audit, Audit.id == MetricSnapshot.audit_id)
        .where(MetricSnapshot.workspace_id == workspace_id)
        .where(MetricSnapshot.project_id == project_id)
        .where(Audit.workspace_id == workspace_id)
        .where(Audit.project_id == project_id)
        .where(Audit.status.in_(_DASHBOARD_STATUSES))
        .where(Audit.completed_at.is_not(None))
        .where(Audit.completed_at >= start_dt)
        .where(Audit.completed_at < end_dt)
        .order_by(Audit.completed_at.asc(), MetricSnapshot.id.asc())
    )
    facts: list[VisibilityFactInput] = []
    for snapshot, completed_at in (await session.execute(stmt)).tuples().all():
        facts.append(
            VisibilityFactInput(
                snapshot_id=snapshot.id,
                # Bucket by the AUDIT's completion day (the run's measured
                # instant), never the snapshot row's write time.
                completed_date=completed_at.date(),
                visibility_score=float(snapshot.visibility_score or 0.0),
                total_completed=int(snapshot.total_completed or 0),
                engine_scores=_engine_scores(snapshot.metrics),
            )
        )
    return facts


async def _theme_facts(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    window_start: date,
    window_end: date,
) -> list[ThemeFactInput]:
    """The window's per-execution rows joined to their frozen prompt axes.

    ``ResponseAnalysis`` carries ``(audit_id, prompt_index)``; the frozen
    ``AuditPromptSnapshot`` (unique per the same tuple) supplies the
    theme/intent axes the rollup groups by — a later edit to the source
    prompt never rewrites what the audit measured (invariant 3).
    """
    start_dt, end_dt = _window_bounds(window_start, window_end)
    stmt = (
        select(ResponseAnalysis, AuditPromptSnapshot)
        .join(Audit, Audit.id == ResponseAnalysis.audit_id)
        .join(
            AuditPromptSnapshot,
            and_(
                AuditPromptSnapshot.audit_id == ResponseAnalysis.audit_id,
                AuditPromptSnapshot.prompt_index == ResponseAnalysis.prompt_index,
            ),
        )
        .where(ResponseAnalysis.workspace_id == workspace_id)
        .where(Audit.workspace_id == workspace_id)
        .where(Audit.project_id == project_id)
        .where(Audit.status.in_(_DASHBOARD_STATUSES))
        .where(Audit.completed_at.is_not(None))
        .where(Audit.completed_at >= start_dt)
        .where(Audit.completed_at < end_dt)
        .order_by(ResponseAnalysis.id.asc())
    )
    facts: list[ThemeFactInput] = []
    for analysis, prompt_snapshot in (await session.execute(stmt)).tuples().all():
        score = analysis.score or {}
        competitors = score.get("competitors_mentioned") or []
        facts.append(
            ThemeFactInput(
                theme=prompt_snapshot.theme or "",
                intent=prompt_snapshot.intent or "",
                brand_mentioned=bool(analysis.brand_mentioned),
                competitors_mentioned=len(set(competitors)),
            )
        )
    return facts


async def _upsert_snapshot(
    session: AsyncSession,
    *,
    task: AnalyticsTask,
    window_start: date,
    window_end: date,
    granularity: str,
    projection: AnalyticsProjection,
) -> None:
    """The transactional upsert of the one current snapshot row.

    ``INSERT ... ON CONFLICT (project_id, window_start, window_end,
    granularity) DO UPDATE`` — concurrent refreshes serialize on the unique
    row and can never create a duplicate "current" snapshot (precedent:
    ``domain/traffic/service.py``). The conflict target's workspace cannot
    drift (one project lives in one workspace), so only the projection
    payload + provenance + version stamps are updated.
    """
    stmt = (
        pg_insert(AnalyticsSnapshot)
        .values(
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            window_start=window_start,
            window_end=window_end,
            granularity=granularity,
            metrics=projection.metrics,
            source_classification_ids=projection.source_classification_ids,
            source_snapshot_ids=projection.source_snapshot_ids,
            analyzer_version=ANALYZER_VERSION,
            formula_version=SCORING_RULE_VERSION,
        )
        .on_conflict_do_update(
            index_elements=[
                "project_id",
                "window_start",
                "window_end",
                "granularity",
            ],
            set_={
                "metrics": projection.metrics,
                "source_classification_ids": projection.source_classification_ids,
                "source_snapshot_ids": projection.source_snapshot_ids,
                "analyzer_version": ANALYZER_VERSION,
                "formula_version": SCORING_RULE_VERSION,
            },
        )
    )
    await session.execute(stmt)


async def refresh_analytics_snapshot(
    session_factory: async_sessionmaker[AsyncSession], task: AnalyticsTask
) -> None:
    """``analytics_snapshot_refresh`` executor: rebuild one window's snapshots.

    Read phase: the window's classification+event+metric-row triples in
    bounded keyset batches (cooperative cancel at every batch boundary),
    plus the visibility + theme inputs over the same dashboard-status audit
    window. Write phase: for each configured granularity
    (``ANALYTICS_SNAPSHOT_GRANULARITIES``) the pure projection is upserted —
    ALL of it in ONE transaction (one commit), so a refresh never leaves a
    half-written snapshot family. NO provider I/O (invariant 7).
    """
    if task.project_id is None:
        raise ValueError("analytics_snapshot_refresh task missing project_id")
    window_start, window_end = _payload_window(task)
    async with session_factory() as session:
        referral_facts: list[ReferralFactInput] = []
        after_id: uuid.UUID | None = None
        while True:
            await _raise_if_task_terminal(session_factory, task.id)
            batch = await _classification_batch(
                session,
                workspace_id=task.workspace_id,
                project_id=task.project_id,
                window_start=window_start,
                window_end=window_end,
                after_id=after_id,
                limit=_CLASSIFICATION_BATCH_SIZE,
            )
            if not batch:
                break
            referral_facts.extend(
                _to_referral_input(classification, event, row)
                for classification, event, row in batch
            )
            after_id = batch[-1][0].id
            if len(batch) < _CLASSIFICATION_BATCH_SIZE:
                break

        visibility_facts = await _visibility_facts(
            session,
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            window_start=window_start,
            window_end=window_end,
        )
        theme_facts = await _theme_facts(
            session,
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            window_start=window_start,
            window_end=window_end,
        )

        for granularity in sorted(ANALYTICS_SNAPSHOT_GRANULARITIES):
            projection = build_analytics_projection(
                referral_facts=referral_facts,
                visibility_facts=visibility_facts,
                theme_facts=theme_facts,
                window_start=window_start,
                window_end=window_end,
                granularity=granularity,
            )
            await _upsert_snapshot(
                session,
                task=task,
                window_start=window_start,
                window_end=window_end,
                granularity=granularity,
                projection=projection,
            )
        await session.commit()
