# LLM Analytics API DTOs (A9) — projections only (invariant 7).
#
# These response models are the backend source of truth for the C6 schema
# reconcile: every shape mirrors the frontend zod schemas in
# ``frontend/lib/api/schemas.ts`` (the LLM-Analytics section) EXACTLY — no
# missing keys, no extra keys (the frontend ``strictValidate`` fails loud on
# any drift). Nullability is contractual: a null series value is an
# UNMEASURED bucket (chart gap), a null correlation coefficient is the
# ``insufficient_data`` state, and a null theme rate/score is an absent
# metric — never a fabricated number (invariant 9).
from __future__ import annotations

import uuid

from pydantic import BaseModel


class MetricSeriesPoint(BaseModel):
    """One dated point of a metric series (``None`` = unmeasured bucket)."""

    date: str
    value: float | None


class AnalyticsSourceBreakdownRow(BaseModel):
    """One per-``ai_source`` referral breakdown row (window-level)."""

    ai_source: str
    sessions: int
    share: float | None


class AnalyticsEngineVisibility(BaseModel):
    """One logical engine's visibility series over the window."""

    logical_engine: str
    series: list[MetricSeriesPoint]


class AnalyticsCorrelation(BaseModel):
    """The visibility<->referral correlation summary.

    ``state`` is ``ok`` only with a real, defined coefficient; below the
    minimum aligned-sample size (or a zero-variance axis) it is
    ``insufficient_data`` with a NULL coefficient — never a fabricated
    number (invariant 9).
    """

    state: str
    coefficient: float | None
    sample_size: int


class LlmAnalyticsResponse(BaseModel):
    """``GET /projects/{id}/llm-analytics`` — the headline AEO projection.

    Served from the persisted ``AnalyticsSnapshot`` matching
    ``(window, granularity)``; an absent snapshot yields an empty payload
    (empty series/breakdowns + ``insufficient_data`` correlation), never a
    recomputation (invariant 7).
    """

    project_id: uuid.UUID
    window_start: str
    window_end: str
    granularity: str
    referral_volume: list[MetricSeriesPoint]
    referral_share: list[MetricSeriesPoint]
    sources: list[AnalyticsSourceBreakdownRow]
    engine_visibility: list[AnalyticsEngineVisibility]
    correlation: AnalyticsCorrelation
    analyzer_version: str
    formula_version: str


class AnalyticsReferralRow(BaseModel):
    """One classified referral drill-down row (classification + its event).

    ``referrer_host`` / ``logical_engine`` / ``match_signal`` are null when
    the sanitized event carries no host / the source maps to no audited
    engine / no rule fired (a non-AI referral). The persisted empty
    ``confidence`` of a non-AI row surfaces as ``exact``: the deterministic
    rule table's no-match verdict is itself an exact determination, never a
    heuristic guess (the frontend contract requires the enum).
    """

    id: uuid.UUID
    occurred_at: str
    landing_url: str
    referrer_host: str | None
    is_ai_referral: bool
    ai_source: str
    logical_engine: str | None
    confidence: str
    match_signal: str | None


class AnalyticsReferralsPage(BaseModel):
    """The keyset envelope (contract C4) for the referrals drill-down."""

    items: list[AnalyticsReferralRow]
    next_cursor: str | None


class LlmAnalyticsThemeRow(BaseModel):
    """One theme-level visibility rollup row (frozen theme/intent axes).

    Rates/score are null when the underlying metric is absent (a group with
    no executions forms no row; SOV is null when the group has no brand or
    competitor mentions at all) — no fabricated numbers.
    """

    theme: str
    intent: str
    total_completed: int
    brand_mention_rate: float | None
    visibility_score: float | None
    share_of_voice: float | None
