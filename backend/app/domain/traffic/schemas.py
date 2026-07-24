# Traffic API DTOs (A10) — projections only (invariant 7).
#
# These response models are the backend source of truth for the C6 schema
# reconcile: every shape mirrors the frontend zod schemas in
# ``frontend/lib/api/schemas.ts`` (the Traffic section) EXACTLY — no missing
# keys, no extra keys (the frontend ``strictValidate`` fails loud on any
# drift). Nullability is contractual: a null series value is an UNMEASURED
# bucket (chart gap), null ``ctr``/``position`` is a zero-impression
# aggregate, and null ``sessions``/``conversions`` means no included GA4
# rows fed the window — never an invented zero.
from __future__ import annotations

import uuid

from pydantic import BaseModel

# The dated metric-series point is the ONE chart-point contract shared with
# LLM Analytics — imported from its owner (invariant 2), never forked.
from app.domain.analytics.schemas import MetricSeriesPoint


class TrafficTotals(BaseModel):
    """Window totals. ``sessions``/``conversions`` are null when no GA4
    connection feeds the window; ``ctr``/``position`` are null when
    undefined (zero impressions)."""

    impressions: int
    clicks: int
    ctr: float | None
    position: float | None
    sessions: int | None
    conversions: int | None


class TrafficSeries(BaseModel):
    """The six dated series of the headline projection (nullable points)."""

    impressions: list[MetricSeriesPoint]
    clicks: list[MetricSeriesPoint]
    ctr: list[MetricSeriesPoint]
    position: list[MetricSeriesPoint]
    sessions: list[MetricSeriesPoint]
    conversions: list[MetricSeriesPoint]


class TrafficDashboardResponse(BaseModel):
    """``GET /projects/{id}/traffic`` — the headline projection.

    Served from the persisted ``TrafficSnapshot`` matching
    ``(window, granularity)``; an absent snapshot yields an empty payload
    (empty series, zeroed/null totals), never a recomputation
    (invariant 7).
    """

    project_id: uuid.UUID
    window_start: str
    window_end: str
    granularity: str
    totals: TrafficTotals
    series: TrafficSeries
    formula_version: str
    normalization_version: str


class TrafficPageRow(BaseModel):
    """One persisted per-page stat row (``TrafficPageStat``).

    ``site_url_id`` is the optional join to the crawled ``SiteUrl``
    (unmatched pages are still valid measured pages); the metrics carry the
    same nullability as the totals.
    """

    canonical_url: str
    site_url_id: uuid.UUID | None
    impressions: int
    clicks: int
    ctr: float | None
    position: float | None
    sessions: int | None
    conversions: int | None


class TrafficPagesPage(BaseModel):
    """The keyset envelope (contract C4) for the paged page stats."""

    items: list[TrafficPageRow]
    next_cursor: str | None


class TrafficQueryRow(BaseModel):
    """One persisted per-query stat row (``TrafficQueryStat``; GSC-only
    measures — queries carry no GA4 sessions/conversions)."""

    normalized_query: str
    impressions: int
    clicks: int
    ctr: float | None
    position: float | None


class TrafficQueriesPage(BaseModel):
    """The keyset envelope (contract C4) for the paged query stats."""

    items: list[TrafficQueryRow]
    next_cursor: str | None
