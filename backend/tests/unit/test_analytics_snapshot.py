"""Pure analytics snapshot math (A8): per-source session bucketing, the
referral-share formula, latest-``resync_seq`` selection, the visibility
fold, the theme rollup, and the deterministic Pearson correlation —
including the ``insufficient_data`` boundary (never a fabricated number).

No database: every test drives ``domain/analytics/snapshot.py``'s pure
builder with in-memory fact inputs.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from app.core.config.analytics import (
    AI_SOURCE_CHATGPT,
    AI_SOURCE_GEMINI,
    AI_SOURCE_OTHER,
    CORRELATION_MIN_SAMPLE,
    CORRELATION_STATE_INSUFFICIENT_DATA,
    CORRELATION_STATE_OK,
)
from app.domain.analytics.snapshot import (
    ReferralFactInput,
    ThemeFactInput,
    VisibilityFactInput,
    build_analytics_projection,
    correlation_summary,
    pearson_coefficient,
    select_latest_referral_facts,
)

WINDOW = (date(2026, 7, 20), date(2026, 7, 22))  # Mon 20 -> Wed 22
_PROPERTY = "properties/123456789"
_GA4 = "ga4"
_REFERRER_DATASET = "ga4_referrer_daily"


def _fact(
    occurred: date,
    *,
    is_ai: bool = True,
    ai_source: str = AI_SOURCE_CHATGPT,
    sessions: int = 1,
    resync_seq: int = 0,
    dimension_key: str = "https://chatgpt.com/ | 20260720",
    row_date: date | None = None,
    classification_id: uuid.UUID | None = None,
    with_row: bool = True,
) -> ReferralFactInput:
    return ReferralFactInput(
        classification_id=classification_id or uuid.uuid4(),
        is_ai_referral=is_ai,
        ai_source=ai_source,
        occurred_date=occurred,
        sessions=sessions,
        row_identity=(
            (_PROPERTY, _GA4, _REFERRER_DATASET, row_date or occurred, dimension_key)
            if with_row
            else None
        ),
        resync_seq=resync_seq,
    )


def _visibility(
    completed: date,
    *,
    score: float,
    total_completed: int = 3,
    engine_scores: tuple[tuple[str, float], ...] = (),
    snapshot_id: uuid.UUID | None = None,
) -> VisibilityFactInput:
    return VisibilityFactInput(
        snapshot_id=snapshot_id or uuid.uuid4(),
        completed_date=completed,
        visibility_score=score,
        total_completed=total_completed,
        engine_scores=engine_scores,
    )


def _theme(
    theme: str,
    intent: str,
    *,
    brand: bool = False,
    competitors: int = 0,
) -> ThemeFactInput:
    return ThemeFactInput(
        theme=theme,
        intent=intent,
        brand_mentioned=brand,
        competitors_mentioned=competitors,
    )


def _build(
    *,
    referral_facts=(),
    visibility_facts=(),
    theme_facts=(),
    granularity: str = "day",
    window: tuple[date, date] = WINDOW,
):
    return build_analytics_projection(
        referral_facts=list(referral_facts),
        visibility_facts=list(visibility_facts),
        theme_facts=list(theme_facts),
        window_start=window[0],
        window_end=window[1],
        granularity=granularity,
    )


# --- Latest-resync selection --------------------------------------------------


def test_select_latest_keeps_only_the_newest_revision_per_row_identity() -> None:
    stale = _fact(date(2026, 7, 20), sessions=5, resync_seq=0)
    fresh = _fact(date(2026, 7, 20), sessions=9, resync_seq=1)
    other_day = _fact(date(2026, 7, 21), sessions=2, resync_seq=0)
    no_row = _fact(date(2026, 7, 21), sessions=99, with_row=False)

    latest = select_latest_referral_facts([stale, fresh, other_day, no_row])

    assert {f.classification_id for f in latest} == {
        fresh.classification_id,
        other_day.classification_id,
    }
    # Deterministic order: by occurred date, then classification id.
    assert [f.occurred_date for f in latest] == [date(2026, 7, 20), date(2026, 7, 21)]


def test_select_latest_deterministic_regardless_of_input_order() -> None:
    a = _fact(date(2026, 7, 20), sessions=5, resync_seq=0)
    b = _fact(date(2026, 7, 20), sessions=9, resync_seq=1)
    assert select_latest_referral_facts([a, b]) == select_latest_referral_facts([b, a])


# --- Referral volume / share series + source breakdown -------------------------


def test_referral_volume_share_and_sources_by_bucket() -> None:
    facts = [
        # Day 1: 5 AI (4 chatgpt + 1 gemini) out of 13 total.
        _fact(date(2026, 7, 20), ai_source=AI_SOURCE_CHATGPT, sessions=4,
              dimension_key="https://chatgpt.com/ | 20260720"),
        _fact(date(2026, 7, 20), ai_source=AI_SOURCE_GEMINI, sessions=1,
              dimension_key="https://gemini.google.com/ | 20260720"),
        _fact(date(2026, 7, 20), is_ai=False, ai_source=AI_SOURCE_OTHER, sessions=8,
              dimension_key="https://example.com/ | 20260720"),
        # Day 2: measured, but NO AI referrals -> measured zero, share 0.
        _fact(date(2026, 7, 21), is_ai=False, ai_source=AI_SOURCE_OTHER, sessions=6,
              dimension_key="https://example.com/ | 20260721"),
        # Day 3: no rows at all -> gap (None), never a coerced zero.
    ]
    projection = _build(referral_facts=facts)

    volume = projection.metrics["referral_volume"]
    assert [p["date"] for p in volume] == ["2026-07-20", "2026-07-21", "2026-07-22"]
    assert [p["value"] for p in volume] == [5, 0, None]
    share = projection.metrics["referral_share"]
    assert share[0]["value"] == pytest.approx(5 / 13)
    assert share[1]["value"] == 0.0
    assert share[2]["value"] is None

    # Window-level breakdown: AI sources only, sessions desc then name asc;
    # the same total (19) as the share denominator.
    sources = projection.metrics["sources"]
    assert sources == [
        {"ai_source": AI_SOURCE_CHATGPT, "sessions": 4, "share": pytest.approx(4 / 19)},
        {"ai_source": AI_SOURCE_GEMINI, "sessions": 1, "share": pytest.approx(1 / 19)},
    ]
    # Provenance: every folded classification (AI + non-AI), sorted ids.
    assert projection.source_classification_ids == sorted(
        str(fact.classification_id) for fact in facts
    )
    assert projection.source_snapshot_ids == []


def test_stale_revisions_never_fold_into_volume_or_provenance() -> None:
    stale = _fact(date(2026, 7, 20), sessions=5, resync_seq=0)
    fresh = _fact(date(2026, 7, 20), sessions=9, resync_seq=1)
    projection = _build(referral_facts=[stale, fresh])

    assert projection.metrics["referral_volume"][0]["value"] == 9
    assert projection.source_classification_ids == [str(fresh.classification_id)]


def test_week_and_month_granularities_bucket_like_traffic() -> None:
    facts = [
        _fact(date(2026, 7, 20), sessions=2, dimension_key="a | 20260720"),
        _fact(date(2026, 7, 22), sessions=3, dimension_key="a | 20260722"),
    ]
    for granularity in ("week", "month"):
        projection = _build(referral_facts=facts, granularity=granularity)
        volume = projection.metrics["referral_volume"]
        # One bucket, first label clamped to the window start.
        assert [p["date"] for p in volume] == ["2026-07-20"]
        assert volume[0]["value"] == 5
        assert projection.metrics["referral_share"][0]["value"] == 1.0


def test_unknown_granularity_and_inverted_window_raise() -> None:
    with pytest.raises(ValueError, match="unknown analytics granularity"):
        _build(granularity="hour")
    with pytest.raises(ValueError, match="window_end before window_start"):
        _build(window=(date(2026, 7, 22), date(2026, 7, 20)))


def test_empty_evidence_yields_gaps_and_zero_sample_correlation() -> None:
    projection = _build()
    assert [p["value"] for p in projection.metrics["referral_volume"]] == [
        None,
        None,
        None,
    ]
    assert projection.metrics["sources"] == []
    assert projection.metrics["engine_visibility"] == []
    assert projection.metrics["correlation"] == {
        "state": CORRELATION_STATE_INSUFFICIENT_DATA,
        "coefficient": None,
        "sample_size": 0,
    }
    assert projection.metrics["themes"] == []


# --- Visibility series ----------------------------------------------------------


def test_engine_visibility_folds_completion_weighted_means() -> None:
    facts = [
        # Day 1: two audits covering chatgpt; weighted by completions.
        _visibility(date(2026, 7, 20), score=50.0, total_completed=2,
                    engine_scores=(("chatgpt", 50.0),)),
        _visibility(date(2026, 7, 20), score=80.0, total_completed=6,
                    engine_scores=(("chatgpt", 70.0),)),
        # Day 2: one audit covering both engines.
        _visibility(date(2026, 7, 21), score=25.0, total_completed=4,
                    engine_scores=(("chatgpt", 25.0), ("gemini", 75.0))),
    ]
    projection = _build(visibility_facts=facts)

    series_by_engine = {
        row["logical_engine"]: row["series"]
        for row in projection.metrics["engine_visibility"]
    }
    assert set(series_by_engine) == {"chatgpt", "gemini"}  # sorted by engine
    chatgpt = series_by_engine["chatgpt"]
    # (50*2 + 70*6) / 8 = 65.0; then 25.0; then a gap (no snapshot).
    assert [p["value"] for p in chatgpt] == [65.0, 25.0, None]
    gemini = series_by_engine["gemini"]
    assert [p["value"] for p in gemini] == [None, 75.0, None]
    # Provenance: every folded MetricSnapshot id.
    assert projection.source_snapshot_ids == sorted(
        str(fact.snapshot_id) for fact in facts
    )


# --- Correlation ------------------------------------------------------------------


def test_pearson_perfect_and_negative_and_zero_variance() -> None:
    assert pearson_coefficient([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert pearson_coefficient([1, 2, 3], [6, 4, 2]) == pytest.approx(-1.0)
    # Zero variance on either axis: undefined -> None, never a number.
    assert pearson_coefficient([1, 1, 1], [2, 4, 6]) is None
    assert pearson_coefficient([1, 2, 3], [5, 5, 5]) is None
    assert pearson_coefficient([], []) is None
    with pytest.raises(ValueError, match="equal lengths"):
        pearson_coefficient([1, 2], [1])


def test_correlation_summary_min_sample_boundary() -> None:
    base = [(float(i), float(i * 2)) for i in range(CORRELATION_MIN_SAMPLE)]
    below = base[: CORRELATION_MIN_SAMPLE - 1]
    summary = correlation_summary(below)
    assert summary["state"] == CORRELATION_STATE_INSUFFICIENT_DATA
    assert summary["coefficient"] is None
    assert summary["sample_size"] == CORRELATION_MIN_SAMPLE - 1

    at_floor = correlation_summary(base)
    assert at_floor["state"] == CORRELATION_STATE_OK
    assert at_floor["coefficient"] == pytest.approx(1.0)
    assert at_floor["sample_size"] == CORRELATION_MIN_SAMPLE


def test_correlation_summary_zero_variance_reports_insufficient() -> None:
    pairs = [(10.0, float(i)) for i in range(CORRELATION_MIN_SAMPLE + 2)]
    summary = correlation_summary(pairs)
    assert summary == {
        "state": CORRELATION_STATE_INSUFFICIENT_DATA,
        "coefficient": None,
        "sample_size": CORRELATION_MIN_SAMPLE + 2,
    }


def test_projection_correlation_aligns_only_days_with_both_series() -> None:
    # 8 days of visibility, but only 7 days carry referral facts -> the
    # unmatched visibility day drops out and the sample is 7 (< 8).
    visibility = [
        _visibility(date(2026, 7, 1) + timedelta(days=i),
                    score=float(10 + i))
        for i in range(8)
    ]
    referrals = [
        _fact(date(2026, 7, 1) + timedelta(days=i),
              sessions=i + 1, dimension_key=f"a | 2026070{i + 1}")
        for i in range(7)  # day 8 has NO referral measurement
    ]
    projection = _build(
        referral_facts=referrals,
        visibility_facts=visibility,
        window=(date(2026, 7, 1), date(2026, 7, 8)),
    )
    correlation = projection.metrics["correlation"]
    assert correlation["state"] == CORRELATION_STATE_INSUFFICIENT_DATA
    assert correlation["coefficient"] is None
    assert correlation["sample_size"] == 7

    # One more aligned day (strictly rising both axes) reaches the floor:
    # a perfect positive correlation.
    referrals.append(
        _fact(date(2026, 7, 8), sessions=8, dimension_key="a | 20260708")
    )
    projection = _build(
        referral_facts=referrals,
        visibility_facts=visibility,
        window=(date(2026, 7, 1), date(2026, 7, 8)),
    )
    correlation = projection.metrics["correlation"]
    assert correlation["state"] == CORRELATION_STATE_OK
    assert correlation["coefficient"] == pytest.approx(1.0)
    assert correlation["sample_size"] == 8


def test_correlation_is_day_aligned_even_for_weekly_snapshots() -> None:
    visibility = [
        _visibility(date(2026, 7, 1) + timedelta(days=i),
                    score=float(10 + i))
        for i in range(8)
    ]
    referrals = [
        _fact(date(2026, 7, 1) + timedelta(days=i),
              sessions=i + 1, dimension_key=f"a | 2026070{i + 1}")
        for i in range(8)
    ]
    projection = _build(
        referral_facts=referrals,
        visibility_facts=visibility,
        window=(date(2026, 7, 1), date(2026, 7, 8)),
        granularity="week",
    )
    # The correlation summary is granularity-independent (always day buckets).
    assert projection.metrics["correlation"]["state"] == CORRELATION_STATE_OK
    assert projection.metrics["correlation"]["sample_size"] == 8


# --- Theme rollup -------------------------------------------------------------------


def test_theme_rollup_groups_by_frozen_theme_and_intent() -> None:
    facts = [
        _theme("pricing", "comparison", brand=True, competitors=1),
        _theme("pricing", "comparison", brand=False, competitors=2),
        _theme("pricing", "comparison", brand=True, competitors=0),
        _theme("onboarding", "", brand=False, competitors=0),
    ]
    projection = _build(theme_facts=facts)
    themes = projection.metrics["themes"]

    # Deterministic order: (theme, intent) ascending.
    assert [(row["theme"], row["intent"]) for row in themes] == [
        ("onboarding", ""),
        ("pricing", "comparison"),
    ]
    pricing = themes[1]
    assert pricing["total_completed"] == 3
    # 2 / 3 brand-mentioned; visibility = rate * 100 (aggregate rounding).
    assert pricing["brand_mention_rate"] == pytest.approx(round(2 / 3, 4))
    assert pricing["visibility_score"] == pytest.approx(round(round(2 / 3, 4) * 100, 2))
    # SOV = brand mentions (2) / (brand 2 + competitor incidences 3).
    assert pricing["share_of_voice"] == pytest.approx(round(2 / 5, 4))

    onboarding = themes[0]
    assert onboarding["total_completed"] == 1
    assert onboarding["brand_mention_rate"] == 0.0
    assert onboarding["visibility_score"] == 0.0
    # No mentions at all -> SOV is null, never a fabricated number.
    assert onboarding["share_of_voice"] is None
