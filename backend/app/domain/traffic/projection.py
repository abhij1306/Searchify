# Traffic projection math (A7) — PURE functions over reduced metric-row
# inputs: no DB, no network, no clock (invariants 7 + 9).
#
# The snapshot refresh executor (``service.py``) reads the project's
# ``IntegrationMetricRow`` rows and reduces them to ``TrafficMetricRowInput``;
# everything from that point on — latest-``resync_seq`` selection, the GA4
# inclusion rule, page/query keying, window bucketing, and the totals / CTR /
# position / trend math — lives here so it is unit-testable without a
# database.
#
# FORMULAS (stamped on every snapshot via ``TRAFFIC_FORMULA_VERSION``):
#   - GSC totals come from ``gsc_page_daily`` ONLY. ``gsc_query_daily`` feeds
#     the per-query stats but NEVER the totals: the query dataset is a
#     privacy-truncated re-dimensioning of the same searches, so adding it
#     would double-count.
#   - GA4 totals come from ``ga4_channel_daily`` (Organic Search rows) plus
#     ``ga4_source_medium_daily`` (AI-referrer rows). ``ga4_landing_daily``
#     feeds per-page GA4 metrics ONLY (it re-dimensions the same sessions).
#     The three GA4 datasets are thereby disjoint per level — no GA4 session
#     is ever counted twice.
#   - GA4 inclusion (organic + AI-driven only, traffic.md section 3): a row
#     folds in iff its channel dim is in ``TRAFFIC_GA4_ORGANIC_CHANNEL_GROUPS``
#     (channel dataset) OR its source/medium dims match the deterministic A4
#     AI-referral classifier (source-medium / landing datasets — they carry
#     no channel dim, so the classifier arm is their only inclusion rule).
#   - ctr = clicks / impressions, ``None`` when impressions == 0 (a bucket
#     with zero impressions has no meaningful CTR — never a fake 0).
#   - position = Σ(position_i × impressions_i) / Σ(impressions_i) over the
#     rows carrying a NUMERIC position (impression-weighted mean; the row's
#     own ``ctr``/``position`` ratios are never averaged directly). ``None``
#     when no position-bearing impressions exist.
#   - sessions / conversions are plain sums over included GA4 rows, ``None``
#     when NO included GA4 row feeds the total/bucket (the frontend renders
#     null as "no GA4 connection", never an invented zero).
#   - Trend = the per-bucket series over the window: day buckets are the
#     dates themselves, week buckets start on the ISO Monday, month buckets
#     on the 1st; the first bucket's label is clamped to ``window_start`` so
#     the series stays aligned to the window. A bucket with no source rows
#     reports ``None`` (a chart gap), never a coerced zero.
from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.connectors.web_evidence.url_policy import UrlPolicyError
from app.core.config.integrations import (
    DATASET_GA4_CHANNEL_DAILY,
    DATASET_GA4_LANDING_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    DATASET_GSC_PAGE_DAILY,
    DATASET_GSC_QUERY_DAILY,
    unpack_dimension_key,
)
from app.core.config.traffic import (
    TRAFFIC_GA4_ORGANIC_CHANNEL_GROUPS,
    TRAFFIC_GRANULARITY_DAY,
    TRAFFIC_GRANULARITY_MONTH,
    TRAFFIC_GRANULARITY_WEEK,
    TRAFFIC_SNAPSHOT_GRANULARITIES,
)
from app.domain.analytics.classification import classify_referral_signals
from app.domain.site_health.normalization import canonical_identity

# The six persisted series names of the headline projection — this module
# writes exactly these into ``TrafficSnapshot.metrics["series"]`` and the
# read service (``domain/traffic/service.py``) imports the one owner.
TRAFFIC_SERIES_NAMES: tuple[str, ...] = (
    "impressions",
    "clicks",
    "ctr",
    "position",
    "sessions",
    "conversions",
)


@dataclass(frozen=True)
class TrafficMetricRowInput:
    """One ``IntegrationMetricRow`` reduced to what the projection reads.

    The executor fills this from the ORM row; the pure math never sees the
    model. ``metrics`` carries the provider metric keys declared by the C1
    dataset template (GSC: clicks/impressions/ctr/position; GA4:
    sessions/engagedSessions/conversions).
    """

    id: uuid.UUID
    property_ref: str
    provider: str
    dataset: str
    date: date
    dimension_key: str
    metrics: Mapping[str, Any] | None
    source_artifact_id: uuid.UUID
    resync_seq: int


@dataclass(frozen=True)
class PageProjection:
    """One per-page stat: canonical key, aggregate metrics, provenance."""

    canonical_url: str
    url_hash: str
    metrics: dict[str, Any]
    source_metric_row_ids: list[str]
    source_artifact_ids: list[str]


@dataclass(frozen=True)
class QueryProjection:
    """One per-query stat: normalized key, aggregate metrics, provenance."""

    normalized_query: str
    metrics: dict[str, Any]
    source_metric_row_ids: list[str]
    source_artifact_ids: list[str]


@dataclass(frozen=True)
class SnapshotProjection:
    """The full projection for one (window, granularity), ready to persist.

    ``metrics`` is the dashboard payload ``{"totals": ..., "series": ...}``;
    ``pages`` / ``queries`` are the per-page / per-query stat rows; the
    top-level provenance lists are the union of every contributing row's
    ids (sorted string UUIDs, so re-runs serialize identically).
    """

    granularity: str
    metrics: dict[str, Any]
    pages: tuple[PageProjection, ...]
    queries: tuple[QueryProjection, ...]
    source_metric_row_ids: list[str]
    source_artifact_ids: list[str]


# --- Small pure primitives ----------------------------------------------------


def normalize_query(raw: str) -> str:
    """The ``TrafficQueryStat`` key: NFKC, casefold, whitespace collapse.

    Deterministic and locale-independent (invariant 9): the same raw GSC
    query string always keys to the same stat row. An input that collapses
    to nothing returns ``""`` (the caller skips it — a stat row needs a
    non-empty key).
    """
    return " ".join(unicodedata.normalize("NFKC", raw).casefold().split())


def bucket_start(day: date, granularity: str) -> date:
    """The natural calendar bucket containing ``day``.

    ``day`` buckets are the date itself; ``week`` buckets start on the ISO
    Monday; ``month`` buckets on the 1st.
    """
    if granularity == TRAFFIC_GRANULARITY_DAY:
        return day
    if granularity == TRAFFIC_GRANULARITY_WEEK:
        return day - timedelta(days=day.weekday())
    if granularity == TRAFFIC_GRANULARITY_MONTH:
        return day.replace(day=1)
    raise ValueError(f"unknown traffic granularity: {granularity!r}")


def _bucket_starts(
    window_start: date, window_end: date, granularity: str
) -> list[date]:
    """Every natural bucket start intersecting the (inclusive) window."""
    starts: list[date] = []
    current = bucket_start(window_start, granularity)
    while current <= window_end:
        starts.append(current)
        if granularity == TRAFFIC_GRANULARITY_MONTH:
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        else:
            step = timedelta(days=1 if granularity == TRAFFIC_GRANULARITY_DAY else 7)
            current += step
    return starts


def bucket_labels(window_start: date, window_end: date, granularity: str) -> list[date]:
    """The series labels: natural starts, the first clamped to the window.

    A window opening mid-week/mid-month labels its first (partial) bucket
    with ``window_start`` so every series point stays inside the window —
    bucketing aligned to the window.
    """
    return [
        max(start, window_start)
        for start in _bucket_starts(window_start, window_end, granularity)
    ]


def select_latest_rows(
    rows: list[TrafficMetricRowInput],
) -> list[TrafficMetricRowInput]:
    """Keep the latest ``resync_seq`` per metric-row identity tuple.

    The identity is ``(property_ref, provider, dataset, date,
    dimension_key)`` — the ``uq_integration_metric_row_identity`` columns
    minus ``resync_seq`` (the selection runs per project, so ``project_id``
    is constant). A row superseded by a later re-sync is stale evidence and
    never folds into the projection (traffic.md section 3). The result is
    sorted deterministically so downstream float aggregation is
    order-independent (invariant 9).
    """
    latest: dict[tuple[object, ...], TrafficMetricRowInput] = {}
    for row in rows:
        identity = (
            row.property_ref,
            row.provider,
            row.dataset,
            row.date,
            row.dimension_key,
        )
        current = latest.get(identity)
        if current is None or row.resync_seq > current.resync_seq:
            latest[identity] = row
    return sorted(latest.values(), key=_row_sort_key)


def ga4_channel_included(channel: str) -> bool:
    """The channel arm of the GA4 inclusion rule (Organic Search only)."""
    return channel.strip() in TRAFFIC_GA4_ORGANIC_CHANNEL_GROUPS


def ga4_source_medium_ai_match(source: str, medium: str) -> bool:
    """The classifier arm of the GA4 inclusion rule (AI-referrer only).

    GA4 ``sessionSource``/``sessionMedium`` are the session's traffic-source
    tags, so they classify through the A4 deterministic classifier's UTM tier
    (inv. 2 — the one AI-referral taxonomy, owned by
    ``domain/analytics/classification.py``).
    """
    return classify_referral_signals(utm_source=source, utm_medium=medium) is not None


# --- Aggregation ---------------------------------------------------------------


def _row_sort_key(row: TrafficMetricRowInput) -> tuple[object, ...]:
    return (row.date, row.dataset, row.dimension_key, str(row.id))


def metric_count(metrics: Mapping[str, Any] | None, key: str) -> int:
    """An additive measure: a missing/non-numeric key counts as 0."""
    value = (metrics or {}).get(key)
    return int(value) if isinstance(value, (int, float)) else 0


def _number(metrics: Mapping[str, Any] | None, key: str) -> float | None:
    """A non-additive measure (position): absent when not numeric."""
    value = (metrics or {}).get(key)
    return float(value) if isinstance(value, (int, float)) else None


@dataclass
class _GscAccum:
    """Running GSC aggregate (clicks/impressions sums + weighted position)."""

    impressions: int = 0
    clicks: int = 0
    position_weighted_sum: float = 0.0
    position_impressions: int = 0
    has_rows: bool = False
    row_ids: set[str] = field(default_factory=set)
    artifact_ids: set[str] = field(default_factory=set)

    def add(self, row: TrafficMetricRowInput) -> None:
        self.has_rows = True
        impressions = metric_count(row.metrics, "impressions")
        self.impressions += impressions
        self.clicks += metric_count(row.metrics, "clicks")
        position = _number(row.metrics, "position")
        if position is not None:
            self.position_weighted_sum += position * impressions
            self.position_impressions += impressions
        self.row_ids.add(str(row.id))
        self.artifact_ids.add(str(row.source_artifact_id))

    def ctr(self) -> float | None:
        # ctr = clicks / impressions; undefined with zero impressions.
        if self.impressions == 0:
            return None
        return self.clicks / self.impressions

    def position(self) -> float | None:
        # Impression-weighted mean over position-bearing rows only.
        if self.position_impressions == 0:
            return None
        return self.position_weighted_sum / self.position_impressions

    def measures(self) -> dict[str, Any]:
        return {
            "impressions": self.impressions,
            "clicks": self.clicks,
            "ctr": self.ctr(),
            "position": self.position(),
        }


@dataclass
class _Ga4Accum:
    """Running GA4 aggregate (sessions/conversions sums)."""

    sessions: int = 0
    conversions: int = 0
    has_rows: bool = False
    row_ids: set[str] = field(default_factory=set)
    artifact_ids: set[str] = field(default_factory=set)

    def add(self, row: TrafficMetricRowInput) -> None:
        self.has_rows = True
        self.sessions += metric_count(row.metrics, "sessions")
        self.conversions += metric_count(row.metrics, "conversions")
        self.row_ids.add(str(row.id))
        self.artifact_ids.add(str(row.source_artifact_id))

    def measures(self) -> dict[str, Any]:
        # Null (not 0) when no included GA4 row fed this total/bucket.
        return {
            "sessions": self.sessions if self.has_rows else None,
            "conversions": self.conversions if self.has_rows else None,
        }


@dataclass
class _PageAccum:
    """One canonical page's combined GSC + GA4 aggregate."""

    url_hash: str
    gsc: _GscAccum = field(default_factory=_GscAccum)
    ga4: _Ga4Accum = field(default_factory=_Ga4Accum)


def _page_accum(pages: dict[str, _PageAccum], raw_page_value: str) -> _PageAccum | None:
    """The page's accumulator keyed by its canonical URL identity.

    The page dimension value is canonicalized with the ONE canonical-form
    owner (``canonical_identity``, invariant 2) so GSC/GA4 page rows join
    the crawled ``SiteUrl`` identity by ``(project_id, url_hash)``. A value
    the URL policy rejects (e.g. a bare GA4 landing path with no site
    origin to resolve it against — the pinned C1 landing template carries
    none) cannot form a page key and is skipped from page stats; its
    totals-level contribution is unaffected.
    """
    try:
        canonical, url_hash = canonical_identity(raw_page_value)
    except UrlPolicyError:
        return None
    accum = pages.get(canonical)
    if accum is None:
        accum = _PageAccum(url_hash=url_hash)
        pages[canonical] = accum
    return accum


def series_point(label: date, value: int | float | None) -> dict[str, Any]:
    """One persisted series fragment point (shared with analytics snapshot)."""
    return {"date": label.isoformat(), "value": value}


def build_traffic_projection(
    *,
    rows: list[TrafficMetricRowInput],
    window_start: date,
    window_end: date,
    granularity: str,
) -> SnapshotProjection:
    """Project the latest metric rows into one snapshot + its stat rows.

    PURE: the caller supplies the candidate rows (already scoped to the
    project + window + consumed datasets); latest-``resync_seq`` selection
    is applied inside so a stale revision can never leak in. Deterministic:
    the same inputs always yield byte-identical metrics and provenance.
    """
    if granularity not in TRAFFIC_SNAPSHOT_GRANULARITIES:
        raise ValueError(f"unknown traffic granularity: {granularity!r}")
    if window_end < window_start:
        raise ValueError("traffic window_end before window_start")

    latest = select_latest_rows(rows)
    starts = _bucket_starts(window_start, window_end, granularity)
    bucket_gsc = {start: _GscAccum() for start in starts}
    bucket_ga4 = {start: _Ga4Accum() for start in starts}
    totals_gsc = _GscAccum()
    totals_ga4 = _Ga4Accum()
    pages: dict[str, _PageAccum] = {}
    queries: dict[str, _GscAccum] = {}

    for row in latest:
        if not (window_start <= row.date <= window_end):
            continue  # defensive: the executor's query already scopes this
        values = unpack_dimension_key(row.dataset, row.dimension_key)
        if values is None:
            continue  # un-mappable key — skipped, never guessed
        # The trailing element is the provider's date dimension value; the
        # parsed date already lives on ``row.date``.
        dimension_values = values[:-1]
        bucket = bucket_start(row.date, granularity)

        if row.dataset == DATASET_GSC_PAGE_DAILY:
            (page_value,) = dimension_values
            totals_gsc.add(row)
            bucket_gsc[bucket].add(row)
            page = _page_accum(pages, page_value)
            if page is not None:
                page.gsc.add(row)
        elif row.dataset == DATASET_GSC_QUERY_DAILY:
            (query_value,) = dimension_values
            normalized = normalize_query(query_value)
            if normalized:
                # Query rows feed ONLY the per-query stats — never the
                # totals (the page dataset is the complete GSC measure).
                queries.setdefault(normalized, _GscAccum()).add(row)
        elif row.dataset == DATASET_GA4_CHANNEL_DAILY:
            (channel,) = dimension_values
            if ga4_channel_included(channel):
                totals_ga4.add(row)
                bucket_ga4[bucket].add(row)
        elif row.dataset == DATASET_GA4_SOURCE_MEDIUM_DAILY:
            source, medium = dimension_values
            if ga4_source_medium_ai_match(source, medium):
                totals_ga4.add(row)
                bucket_ga4[bucket].add(row)
        elif row.dataset == DATASET_GA4_LANDING_DAILY:
            landing, source, medium = dimension_values
            if ga4_source_medium_ai_match(source, medium):
                page = _page_accum(pages, landing)
                if page is not None:
                    page.ga4.add(row)
        # Any other dataset id is not consumed by Traffic (the C1 referral
        # datasets belong to the A5 ingest) — defensive skip.

    labels = bucket_labels(window_start, window_end, granularity)
    series: dict[str, list[dict[str, Any]]] = {
        name: [] for name in TRAFFIC_SERIES_NAMES
    }
    for start, label in zip(starts, labels, strict=True):
        gsc = bucket_gsc[start]
        ga4 = bucket_ga4[start]
        series["impressions"].append(
            series_point(label, gsc.impressions if gsc.has_rows else None)
        )
        series["clicks"].append(
            series_point(label, gsc.clicks if gsc.has_rows else None)
        )
        series["ctr"].append(series_point(label, gsc.ctr()))
        series["position"].append(series_point(label, gsc.position()))
        ga4_measures = ga4.measures()
        series["sessions"].append(series_point(label, ga4_measures["sessions"]))
        series["conversions"].append(series_point(label, ga4_measures["conversions"]))

    totals = totals_gsc.measures() | totals_ga4.measures()

    page_projections = tuple(
        PageProjection(
            canonical_url=canonical_url,
            url_hash=accum.url_hash,
            metrics=accum.gsc.measures() | accum.ga4.measures(),
            source_metric_row_ids=sorted(accum.gsc.row_ids | accum.ga4.row_ids),
            source_artifact_ids=sorted(accum.gsc.artifact_ids | accum.ga4.artifact_ids),
        )
        for canonical_url, accum in sorted(pages.items())
    )
    query_projections = tuple(
        QueryProjection(
            normalized_query=normalized,
            metrics=accum.measures(),
            source_metric_row_ids=sorted(accum.row_ids),
            source_artifact_ids=sorted(accum.artifact_ids),
        )
        for normalized, accum in sorted(queries.items())
    )

    snapshot_row_ids = set(totals_gsc.row_ids) | set(totals_ga4.row_ids)
    snapshot_artifact_ids = set(totals_gsc.artifact_ids) | set(totals_ga4.artifact_ids)
    for page_projection in page_projections:
        snapshot_row_ids.update(page_projection.source_metric_row_ids)
        snapshot_artifact_ids.update(page_projection.source_artifact_ids)
    for query_projection in query_projections:
        snapshot_row_ids.update(query_projection.source_metric_row_ids)
        snapshot_artifact_ids.update(query_projection.source_artifact_ids)

    return SnapshotProjection(
        granularity=granularity,
        metrics={"totals": totals, "series": series},
        pages=page_projections,
        queries=query_projections,
        source_metric_row_ids=sorted(snapshot_row_ids),
        source_artifact_ids=sorted(snapshot_artifact_ids),
    )
