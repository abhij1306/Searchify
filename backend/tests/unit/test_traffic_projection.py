"""Pure traffic projection math (A7): bucketing, weighted position, CTR,
NFKC/casefold query keys, latest-``resync_seq`` selection, the GA4
inclusion rule, and the shared ``dimension_key`` unpack helper.

No database: every test drives ``domain/traffic/projection.py`` (and the
config-owned ``unpack_dimension_key``) with in-memory row inputs.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest

from app.core.config.integrations import (
    DATASET_GA4_CHANNEL_DAILY,
    DATASET_GA4_LANDING_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    DATASET_GSC_PAGE_DAILY,
    DATASET_GSC_QUERY_DAILY,
    pack_dimension_key,
    unpack_dimension_key,
)
from app.core.config.traffic import TRAFFIC_GA4_ORGANIC_CHANNEL_GROUPS
from app.domain.traffic.projection import (
    TrafficMetricRowInput,
    bucket_labels,
    bucket_start,
    build_traffic_projection,
    ga4_channel_included,
    ga4_source_medium_ai_match,
    normalize_query,
    select_latest_rows,
)

_GSC = "gsc"
_GA4 = "ga4"
_GSC_PROPERTY = "https://example.com/"
_GA4_PROPERTY = "properties/123456789"


def _row(
    *,
    dataset: str,
    row_date: date,
    dimension_values: list[str],
    metrics: dict[str, Any] | None = None,
    resync_seq: int = 0,
    property_ref: str | None = None,
    provider: str | None = None,
    row_id: uuid.UUID | None = None,
    artifact_id: uuid.UUID | None = None,
) -> TrafficMetricRowInput:
    if provider is None:
        provider = _GSC if dataset.startswith("gsc") else _GA4
    if property_ref is None:
        property_ref = _GSC_PROPERTY if provider == _GSC else _GA4_PROPERTY
    return TrafficMetricRowInput(
        id=row_id or uuid.uuid4(),
        property_ref=property_ref,
        provider=provider,
        dataset=dataset,
        date=row_date,
        dimension_key=pack_dimension_key(dimension_values),
        metrics=metrics,
        source_artifact_id=artifact_id or uuid.uuid4(),
        resync_seq=resync_seq,
    )


def _gsc_page(
    url: str,
    row_date: date,
    *,
    clicks: int,
    impressions: int,
    position: float | None = None,
    **kwargs: Any,
) -> TrafficMetricRowInput:
    metrics: dict[str, Any] = {"clicks": clicks, "impressions": impressions}
    if position is not None:
        metrics["position"] = position
    return _row(
        dataset=DATASET_GSC_PAGE_DAILY,
        row_date=row_date,
        dimension_values=[url, row_date.isoformat()],
        metrics=metrics,
        **kwargs,
    )


def _totals(rows: list[TrafficMetricRowInput], **kwargs: Any) -> dict[str, Any]:
    projection = build_traffic_projection(rows=rows, **kwargs)
    return projection.metrics["totals"]


# --- normalize_query ------------------------------------------------------------


def test_normalize_query_nfkc_casefold_whitespace() -> None:
    # Whitespace collapse + strip.
    assert normalize_query("  hello \t world \n") == "hello world"
    # Casefold (German sharp s -> ss).
    assert normalize_query("Straße") == "strasse"
    # NFKC compatibility: full-width latin folds to ASCII.
    assert normalize_query("ＦＵＬＬ ｗｉｄｔｈ") == "full width"
    # Whitespace-only collapses to nothing (caller skips the row).
    assert normalize_query("   ") == ""


# --- Bucketing --------------------------------------------------------------------


def test_bucket_start_day_week_month() -> None:
    wednesday = date(2026, 7, 22)
    assert bucket_start(wednesday, "day") == wednesday
    assert bucket_start(wednesday, "week") == date(2026, 7, 20)  # ISO Monday
    assert bucket_start(date(2026, 7, 26), "week") == date(2026, 7, 20)
    assert bucket_start(wednesday, "month") == date(2026, 7, 1)
    with pytest.raises(ValueError, match="granularity"):
        bucket_start(wednesday, "quarter")


def test_bucket_labels_aligned_to_window() -> None:
    # Day: every date in the inclusive window.
    assert bucket_labels(date(2026, 7, 20), date(2026, 7, 22), "day") == [
        date(2026, 7, 20),
        date(2026, 7, 21),
        date(2026, 7, 22),
    ]
    # Week: a window opening mid-week labels its first partial bucket with
    # the window start; later buckets keep their natural Monday starts.
    assert bucket_labels(date(2026, 7, 22), date(2026, 7, 29), "week") == [
        date(2026, 7, 22),
        date(2026, 7, 27),
    ]
    # Month: first partial bucket clamped, next month on the 1st.
    assert bucket_labels(date(2026, 6, 15), date(2026, 7, 10), "month") == [
        date(2026, 6, 15),
        date(2026, 7, 1),
    ]


# --- CTR / weighted position -----------------------------------------------------


def test_totals_ctr_and_impression_weighted_position() -> None:
    rows = [
        _gsc_page(
            "https://example.com/a",
            date(2026, 7, 20),
            clicks=10,
            impressions=100,
            position=10.0,
        ),
        _gsc_page(
            "https://example.com/a",
            date(2026, 7, 21),
            clicks=60,
            impressions=300,
            position=20.0,
        ),
    ]
    totals = _totals(
        rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 21),
        granularity="day",
    )
    assert totals["impressions"] == 400
    assert totals["clicks"] == 70
    assert totals["ctr"] == pytest.approx(70 / 400)
    # (10*100 + 20*300) / (100 + 300) — NOT the mean of the row positions.
    assert totals["position"] == pytest.approx(17.5)


def test_ctr_and_position_none_without_impressions() -> None:
    rows = [
        _gsc_page(
            "https://example.com/a",
            date(2026, 7, 20),
            clicks=0,
            impressions=0,
            position=5.0,
        )
    ]
    totals = _totals(
        rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 20),
        granularity="day",
    )
    assert totals["ctr"] is None
    # Zero-impression position rows carry no weight: no denominator.
    assert totals["position"] is None


def test_position_ignores_rows_without_position_in_both_terms() -> None:
    rows = [
        _gsc_page(
            "https://example.com/a",
            date(2026, 7, 20),
            clicks=5,
            impressions=100,
            position=8.0,
        ),
        # No position key: its impressions must NOT enter the denominator.
        _gsc_page(
            "https://example.com/b",
            date(2026, 7, 20),
            clicks=5,
            impressions=900,
        ),
    ]
    totals = _totals(
        rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 20),
        granularity="day",
    )
    assert totals["position"] == pytest.approx(8.0)


# --- Latest-resync_seq selection ---------------------------------------------------


def test_select_latest_rows_keeps_highest_resync_seq_per_identity() -> None:
    shared_id = uuid.uuid4()
    stale = _gsc_page(
        "https://example.com/a",
        date(2026, 7, 20),
        clicks=5,
        impressions=50,
        resync_seq=0,
        row_id=shared_id,
    )
    fresh = _gsc_page(
        "https://example.com/a",
        date(2026, 7, 20),
        clicks=9,
        impressions=90,
        resync_seq=1,
    )
    other_identity = _gsc_page(
        "https://example.com/b",
        date(2026, 7, 20),
        clicks=1,
        impressions=10,
        resync_seq=0,
    )
    latest = select_latest_rows([stale, fresh, other_identity])
    assert len(latest) == 2
    assert stale.id not in {row.id for row in latest}

    projection = build_traffic_projection(
        rows=[stale, fresh],
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 20),
        granularity="day",
    )
    assert projection.metrics["totals"]["clicks"] == 9
    # The superseded row is not folded in and not in the provenance.
    assert str(stale.id) not in projection.source_metric_row_ids
    assert str(fresh.id) in projection.source_metric_row_ids


def test_select_latest_rows_treats_property_ref_as_identity() -> None:
    # Same page/date under TWO mapped properties are distinct identities.
    first = _gsc_page(
        "https://example.com/a",
        date(2026, 7, 20),
        clicks=1,
        impressions=10,
        property_ref="https://example.com/",
    )
    second = _gsc_page(
        "https://example.com/a",
        date(2026, 7, 20),
        clicks=2,
        impressions=20,
        property_ref="sc-domain:example.com",
    )
    assert len(select_latest_rows([first, second])) == 2


# --- GA4 inclusion rule ---------------------------------------------------------------


def test_ga4_channel_inclusion_is_exactly_the_config_groups() -> None:
    assert "Organic Search" in TRAFFIC_GA4_ORGANIC_CHANNEL_GROUPS
    assert ga4_channel_included("Organic Search") is True
    assert ga4_channel_included("Paid Search") is False
    assert ga4_channel_included("Direct") is False
    assert ga4_channel_included("Referral") is False
    # Near-miss casings are NOT admitted — the config vocabulary is exact.
    assert ga4_channel_included("organic search") is False


def test_ga4_source_medium_inclusion_via_ai_classifier() -> None:
    assert ga4_source_medium_ai_match("chatgpt.com", "referral") is True
    assert ga4_source_medium_ai_match("perplexity.ai", "referral") is True
    # Organic/direct source-mediums are not AI referrals.
    assert ga4_source_medium_ai_match("google", "organic") is False
    assert ga4_source_medium_ai_match("newsletter", "email") is False


def test_ga4_totals_include_only_organic_channel_and_ai_source_medium() -> None:
    rows = [
        _row(
            dataset=DATASET_GA4_CHANNEL_DAILY,
            row_date=date(2026, 7, 20),
            dimension_values=["Organic Search", "20260720"],
            metrics={"sessions": 7, "conversions": 2},
        ),
        _row(
            dataset=DATASET_GA4_CHANNEL_DAILY,
            row_date=date(2026, 7, 20),
            dimension_values=["Paid Search", "20260720"],
            metrics={"sessions": 100, "conversions": 50},
        ),
        _row(
            dataset=DATASET_GA4_SOURCE_MEDIUM_DAILY,
            row_date=date(2026, 7, 20),
            dimension_values=["chatgpt.com", "referral", "20260720"],
            metrics={"sessions": 4, "conversions": 1},
        ),
        _row(
            dataset=DATASET_GA4_SOURCE_MEDIUM_DAILY,
            row_date=date(2026, 7, 20),
            dimension_values=["google", "organic", "20260720"],
            metrics={"sessions": 999, "conversions": 99},
        ),
    ]
    totals = _totals(
        rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 20),
        granularity="day",
    )
    # 7 organic-channel + 4 AI sessions; paid + google/organic excluded.
    assert totals["sessions"] == 11
    assert totals["conversions"] == 3


def test_sessions_and_conversions_null_without_included_ga4_rows() -> None:
    rows = [
        _gsc_page(
            "https://example.com/a",
            date(2026, 7, 20),
            clicks=1,
            impressions=10,
        )
    ]
    totals = _totals(
        rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 20),
        granularity="day",
    )
    # Null — never an invented zero when no GA4 connection feeds the window.
    assert totals["sessions"] is None
    assert totals["conversions"] is None


# --- build_traffic_projection: pages / queries / series / provenance ------------------


def test_query_rows_feed_query_stats_but_never_totals() -> None:
    rows = [
        _gsc_page(
            "https://example.com/a",
            date(2026, 7, 20),
            clicks=10,
            impressions=100,
        ),
        _row(
            dataset=DATASET_GSC_QUERY_DAILY,
            row_date=date(2026, 7, 20),
            dimension_values=["Best  CRM\tTools ", "2026-07-20"],
            metrics={"clicks": 7, "impressions": 70, "position": 4.0},
        ),
    ]
    projection = build_traffic_projection(
        rows=rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 20),
        granularity="day",
    )
    # Totals come from the page dataset only — no double counting.
    assert projection.metrics["totals"]["clicks"] == 10
    assert [q.normalized_query for q in projection.queries] == ["best crm tools"]
    assert projection.queries[0].metrics["clicks"] == 7
    # The query row IS provenance (it fed a query stat).
    assert str(rows[1].id) in projection.source_metric_row_ids


def test_page_key_canonicalizes_tracking_params_fragment_and_case() -> None:
    rows = [
        _gsc_page(
            "https://EXAMPLE.com:443/blog?utm_source=nl#top",
            date(2026, 7, 20),
            clicks=3,
            impressions=30,
        ),
        _gsc_page(
            "https://example.com/blog",
            date(2026, 7, 21),
            clicks=4,
            impressions=40,
        ),
    ]
    projection = build_traffic_projection(
        rows=rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 21),
        granularity="day",
    )
    assert len(projection.pages) == 1
    page = projection.pages[0]
    assert page.canonical_url == "https://example.com/blog"
    assert page.metrics["clicks"] == 7
    assert len(page.source_metric_row_ids) == 2


def test_page_ga4_landing_metrics_and_exclusion_rule() -> None:
    page_url = "https://example.com/blog"
    rows = [
        _row(
            dataset=DATASET_GA4_LANDING_DAILY,
            row_date=date(2026, 7, 20),
            dimension_values=[page_url, "chatgpt.com", "referral", "20260720"],
            metrics={"sessions": 2, "conversions": 0},
        ),
        _row(
            dataset=DATASET_GA4_LANDING_DAILY,
            row_date=date(2026, 7, 20),
            dimension_values=[page_url, "google", "organic", "20260720"],
            metrics={"sessions": 50, "conversions": 5},
        ),
    ]
    projection = build_traffic_projection(
        rows=rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 20),
        granularity="day",
    )
    assert len(projection.pages) == 1
    page = projection.pages[0]
    # Only the AI-referred landing row folds into the page's GA4 metrics.
    assert page.metrics["sessions"] == 2
    assert page.metrics["conversions"] == 0
    # Landing rows feed page stats ONLY — never the snapshot totals.
    assert projection.metrics["totals"]["sessions"] is None


def test_page_without_ga4_rows_has_null_sessions() -> None:
    rows = [
        _gsc_page(
            "https://example.com/a",
            date(2026, 7, 20),
            clicks=1,
            impressions=10,
        )
    ]
    projection = build_traffic_projection(
        rows=rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 20),
        granularity="day",
    )
    assert projection.pages[0].metrics["sessions"] is None
    assert projection.pages[0].metrics["conversions"] is None


def test_uncanonicalizable_page_skipped_from_stats_but_kept_in_totals() -> None:
    rows = [
        _gsc_page(
            "not a url",
            date(2026, 7, 20),
            clicks=6,
            impressions=60,
        )
    ]
    projection = build_traffic_projection(
        rows=rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 20),
        granularity="day",
    )
    assert projection.pages == ()
    # The measured traffic is real: it still counts in the totals and the
    # row stays in the snapshot provenance.
    assert projection.metrics["totals"]["clicks"] == 6
    assert str(rows[0].id) in projection.source_metric_row_ids


def test_unmappable_dimension_key_is_skipped() -> None:
    row = _row(
        dataset=DATASET_GSC_PAGE_DAILY,
        row_date=date(2026, 7, 20),
        dimension_values=["https://example.com/a", "2026-07-20"],
        metrics={"clicks": 5, "impressions": 50},
    )
    broken = TrafficMetricRowInput(
        **{**row.__dict__, "dimension_key": "https://example.com/a"}
    )
    projection = build_traffic_projection(
        rows=[broken],
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 20),
        granularity="day",
    )
    assert projection.metrics["totals"]["clicks"] == 0
    assert projection.source_metric_row_ids == []


def test_out_of_window_rows_are_ignored() -> None:
    rows = [
        _gsc_page(
            "https://example.com/a",
            date(2026, 7, 19),
            clicks=9,
            impressions=90,
        )
    ]
    totals = _totals(
        rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 22),
        granularity="day",
    )
    assert totals["clicks"] == 0
    assert totals["impressions"] == 0


def test_series_buckets_render_gaps_for_rows_free_buckets() -> None:
    rows = [
        _gsc_page(
            "https://example.com/a",
            date(2026, 7, 21),
            clicks=4,
            impressions=40,
            position=6.0,
        ),
        _row(
            dataset=DATASET_GA4_CHANNEL_DAILY,
            row_date=date(2026, 7, 21),
            dimension_values=["Organic Search", "20260721"],
            metrics={"sessions": 3, "conversions": 1},
        ),
    ]
    projection = build_traffic_projection(
        rows=rows,
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 22),
        granularity="day",
    )
    series = projection.metrics["series"]
    assert [p["date"] for p in series["clicks"]] == [
        "2026-07-20",
        "2026-07-21",
        "2026-07-22",
    ]
    # Rows-free buckets are gaps (None), never coerced zeros.
    assert [p["value"] for p in series["impressions"]] == [None, 40, None]
    assert [p["value"] for p in series["clicks"]] == [None, 4, None]
    assert [p["value"] for p in series["sessions"]] == [None, 3, None]
    assert [p["value"] for p in series["conversions"]] == [None, 1, None]
    # CTR/position computed per bucket.
    assert [p["value"] for p in series["ctr"]] == [None, 0.1, None]
    assert [p["value"] for p in series["position"]] == [None, 6.0, None]


def test_empty_window_projects_zeroed_totals_and_gap_series() -> None:
    projection = build_traffic_projection(
        rows=[],
        window_start=date(2026, 7, 20),
        window_end=date(2026, 7, 21),
        granularity="day",
    )
    totals = projection.metrics["totals"]
    assert totals == {
        "impressions": 0,
        "clicks": 0,
        "ctr": None,
        "position": None,
        "sessions": None,
        "conversions": None,
    }
    assert projection.pages == ()
    assert projection.queries == ()
    assert projection.source_metric_row_ids == []
    assert projection.source_artifact_ids == []
    assert len(projection.metrics["series"]["clicks"]) == 2
    assert all(
        point["value"] is None
        for series in projection.metrics["series"].values()
        for point in series
    )


def test_projection_is_order_independent() -> None:
    rows = [
        _gsc_page(
            "https://example.com/a",
            date(2026, 7, 20),
            clicks=3,
            impressions=30,
            position=1.5,
        ),
        _gsc_page(
            "https://example.com/b",
            date(2026, 7, 21),
            clicks=7,
            impressions=70,
            position=2.5,
        ),
        _row(
            dataset=DATASET_GSC_QUERY_DAILY,
            row_date=date(2026, 7, 21),
            dimension_values=["crm", "2026-07-21"],
            metrics={"clicks": 2, "impressions": 20, "position": 9.0},
        ),
    ]
    kwargs = {
        "window_start": date(2026, 7, 20),
        "window_end": date(2026, 7, 21),
        "granularity": "week",
    }
    forward = build_traffic_projection(rows=rows, **kwargs)
    reverse = build_traffic_projection(rows=list(reversed(rows)), **kwargs)
    assert forward.metrics == reverse.metrics
    assert forward.source_metric_row_ids == reverse.source_metric_row_ids
    assert forward.pages == reverse.pages
    assert forward.queries == reverse.queries
    # A week-granularity window inside one ISO week yields a single bucket.
    assert len(forward.metrics["series"]["clicks"]) == 1


def test_invalid_granularity_and_window_fail_loud() -> None:
    with pytest.raises(ValueError, match="granularity"):
        build_traffic_projection(
            rows=[],
            window_start=date(2026, 7, 20),
            window_end=date(2026, 7, 21),
            granularity="quarter",
        )
    with pytest.raises(ValueError, match="window_end"):
        build_traffic_projection(
            rows=[],
            window_start=date(2026, 7, 22),
            window_end=date(2026, 7, 21),
            granularity="day",
        )


# --- unpack_dimension_key (config-owned inverse of pack_dimension_key) -----


def test_unpack_dimension_key_round_trips_declared_arity() -> None:
    key = pack_dimension_key(["https://example.com/a", "2026-07-20"])
    assert unpack_dimension_key(DATASET_GSC_PAGE_DAILY, key) == (
        "https://example.com/a",
        "2026-07-20",
    )


def test_unpack_dimension_key_right_peels_separator_inside_leading_value() -> None:
    # A " | " inside the page value must survive: the split peels only the
    # trailing date dimension (declared arity 2).
    key = pack_dimension_key(["https://example.com/a | b", "2026-07-20"])
    assert unpack_dimension_key(DATASET_GSC_PAGE_DAILY, key) == (
        "https://example.com/a | b",
        "2026-07-20",
    )
    key = pack_dimension_key(
        ["https://example.com/lp", "chatgpt.com", "referral", "20260720"]
    )
    assert unpack_dimension_key(DATASET_GA4_LANDING_DAILY, key) == (
        "https://example.com/lp",
        "chatgpt.com",
        "referral",
        "20260720",
    )


def test_unpack_dimension_key_rejects_wrong_arity_and_unknown_dataset() -> None:
    assert unpack_dimension_key(DATASET_GSC_PAGE_DAILY, "https://example.com/a") is None
    assert unpack_dimension_key("bing_unknown_daily", "a | b") is None
