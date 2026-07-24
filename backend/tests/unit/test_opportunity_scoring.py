"""Opportunities scoring: determinism, monotonicity, rounding, fallbacks."""

from __future__ import annotations

from app.analysis.opportunities.scoring import (
    gap_factor_visibility,
    priority_score,
    value_factor_for_intent,
)
from app.core.config.opportunities import (
    GAP_COMPETITOR_CAP,
    INTENT_VALUE_DEFAULT,
    INTENT_VALUE_WEIGHTS,
    MIN_PRIORITY_TO_SURFACE,
    PRIORITY_ROUNDING_DECIMALS,
    PRIORITY_SCALE,
    SEVERITY_WEIGHT_DEFAULT,
    SEVERITY_WEIGHTS,
)


def test_priority_score_is_deterministic() -> None:
    kwargs = {"severity": "high", "value_factor": 1.5, "gap_factor": 2.0}
    assert priority_score(**kwargs) == priority_score(**kwargs)


def test_priority_score_matches_formula() -> None:
    expected = round(
        SEVERITY_WEIGHTS["medium"] * 1.25 * 1.5 * PRIORITY_SCALE,
        PRIORITY_ROUNDING_DECIMALS,
    )
    assert (
        priority_score(severity="medium", value_factor=1.25, gap_factor=1.5) == expected
    )


def test_priority_score_monotonic_in_severity() -> None:
    ordered = ["info", "low", "medium", "high", "critical"]
    scores = [
        priority_score(severity=s, value_factor=1.0, gap_factor=1.0) for s in ordered
    ]
    assert scores == sorted(scores)
    assert len(set(scores)) == len(scores)


def test_priority_score_monotonic_in_value_factor() -> None:
    low = priority_score(severity="high", value_factor=1.0, gap_factor=1.0)
    high = priority_score(severity="high", value_factor=2.0, gap_factor=1.0)
    assert high > low


def test_priority_score_monotonic_in_gap_factor() -> None:
    low = priority_score(severity="high", value_factor=1.0, gap_factor=1.0)
    high = priority_score(severity="high", value_factor=1.0, gap_factor=3.0)
    assert high > low


def test_priority_score_rounding() -> None:
    # 1.0 * 1.25 * 1.0 * 10 = 12.5 exactly (1 decimal).
    assert priority_score(severity="low", value_factor=1.25, gap_factor=1.0) == 12.5


def test_priority_score_unknown_severity_falls_back() -> None:
    expected = round(
        SEVERITY_WEIGHT_DEFAULT * 1.0 * 1.0 * PRIORITY_SCALE, PRIORITY_ROUNDING_DECIMALS
    )
    assert (
        priority_score(severity="no_such_severity", value_factor=1.0, gap_factor=1.0)
        == expected
    )


def test_value_factor_for_known_intents() -> None:
    for intent, weight in INTENT_VALUE_WEIGHTS.items():
        assert value_factor_for_intent(intent) == weight


def test_value_factor_fallbacks() -> None:
    assert value_factor_for_intent("") == INTENT_VALUE_DEFAULT
    assert value_factor_for_intent(None) == INTENT_VALUE_DEFAULT
    assert value_factor_for_intent("unknown-intent") == INTENT_VALUE_DEFAULT


def test_intent_value_monotonic_purchase_over_discovery() -> None:
    assert value_factor_for_intent("purchase") > value_factor_for_intent("discovery")


def test_gap_factor_floor_is_one() -> None:
    assert gap_factor_visibility(competitor_count=0, owned_citation_rate=0.0) == 1.0
    assert gap_factor_visibility(competitor_count=0, owned_citation_rate=1.0) == 1.0


def test_gap_factor_grows_with_competitors() -> None:
    one = gap_factor_visibility(competitor_count=1, owned_citation_rate=0.0)
    three = gap_factor_visibility(competitor_count=3, owned_citation_rate=0.0)
    assert three > one > 1.0


def test_gap_factor_caps_competitor_count() -> None:
    capped = gap_factor_visibility(
        competitor_count=GAP_COMPETITOR_CAP, owned_citation_rate=0.0
    )
    beyond = gap_factor_visibility(
        competitor_count=GAP_COMPETITOR_CAP + 10, owned_citation_rate=0.0
    )
    assert beyond == capped


def test_gap_factor_shrinks_with_owned_coverage() -> None:
    open_gap = gap_factor_visibility(competitor_count=3, owned_citation_rate=0.0)
    closing = gap_factor_visibility(competitor_count=3, owned_citation_rate=0.5)
    closed = gap_factor_visibility(competitor_count=3, owned_citation_rate=1.0)
    assert open_gap > closing > closed == 1.0


def test_gap_factor_clamps_out_of_range_inputs() -> None:
    assert gap_factor_visibility(competitor_count=-5, owned_citation_rate=2.0) == 1.0


def test_enabled_rules_clear_surface_floor_at_base_factors() -> None:
    # The write-time floor must not make any enabled catalog rule un-writable.
    assert (
        priority_score(severity="low", value_factor=1.0, gap_factor=1.0)
        >= MIN_PRIORITY_TO_SURFACE
    )
