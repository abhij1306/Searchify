"""Unit tests for the EXACT Site Health scoring formula (Task 5).

Verifies: passed/failed/error weighting, not_applicable exclusion, error
zero-credit (distinct outcome, weight stays in the denominator), single
rounding, config-weighted overall, dimensions with no applicable rules excluded
(not zero), and aggregation ignoring missing/error URLs. Pure, offline.
"""
from __future__ import annotations

from app.analysis.site_health.scoring import (
    AnalysisScoreInput,
    _Scored,
    aggregate_scores,
    overall_score,
    score_analysis,
    score_dimension,
)
from app.core.config.site_health import (
    DIMENSION_AEO,
    DIMENSION_TECHNICAL,
    RULE_OUTCOME_ERROR,
    RULE_OUTCOME_FAIL,
    RULE_OUTCOME_NOT_APPLICABLE,
    RULE_OUTCOME_PASS,
    SCORING_VERSION,
)


def _s(outcome, weight, dimension=DIMENSION_TECHNICAL):
    return _Scored(dimension=dimension, outcome=outcome, weight=weight)


def test_all_pass_is_100():
    evals = [
        _s(RULE_OUTCOME_PASS, 3.0),
        _s(RULE_OUTCOME_PASS, 2.0),
    ]
    ds = score_dimension(evals, dimension=DIMENSION_TECHNICAL)
    assert ds.score == 100.0
    assert ds.applicable_count == 2


def test_pass_fail_weighting():
    # passed=3, failed=2 -> 100*3/5 = 60.0
    evals = [
        _s(RULE_OUTCOME_PASS, 3.0),
        _s(RULE_OUTCOME_FAIL, 2.0),
    ]
    ds = score_dimension(evals, dimension=DIMENSION_TECHNICAL)
    assert ds.score == 60.0


def test_error_gets_zero_credit_but_stays_in_denominator():
    # passed=3, failed=0, error=1 -> 100*3/(3+0+1) = 75.0 (error != pass, != drop)
    evals = [
        _s(RULE_OUTCOME_PASS, 3.0),
        _s(RULE_OUTCOME_ERROR, 1.0),
    ]
    ds = score_dimension(evals, dimension=DIMENSION_TECHNICAL)
    assert ds.score == 75.0
    assert ds.error_weight == 1.0
    assert ds.applicable_count == 2


def test_not_applicable_excluded_entirely():
    # not_applicable weight is neither numerator nor denominator.
    # passed=4, failed=0 (n/a=10 ignored) -> 100.0
    evals = [
        _s(RULE_OUTCOME_PASS, 4.0),
        _s(RULE_OUTCOME_NOT_APPLICABLE, 10.0),
    ]
    ds = score_dimension(evals, dimension=DIMENSION_TECHNICAL)
    assert ds.score == 100.0
    assert ds.applicable_count == 1


def test_no_applicable_rules_yields_none_not_zero():
    evals = [_s(RULE_OUTCOME_NOT_APPLICABLE, 5.0)]
    ds = score_dimension(evals, dimension=DIMENSION_TECHNICAL)
    assert ds.score is None
    assert ds.applicable_count == 0


def test_single_rounding_to_one_decimal():
    # passed=1, failed=2 -> 100/3 = 33.333... -> 33.3
    evals = [
        _s(RULE_OUTCOME_PASS, 1.0),
        _s(RULE_OUTCOME_FAIL, 2.0),
    ]
    ds = score_dimension(evals, dimension=DIMENSION_TECHNICAL)
    assert ds.score == 33.3


def test_overall_is_config_weighted_mean():
    # 50/50 weights -> mean of 80 and 60 = 70.0
    assert overall_score(
        {DIMENSION_TECHNICAL: 80.0, DIMENSION_AEO: 60.0}
    ) == 70.0


def test_overall_excludes_none_dimension():
    # AEO None -> overall is just technical (not dragged to zero/halved).
    assert overall_score(
        {DIMENSION_TECHNICAL: 90.0, DIMENSION_AEO: None}
    ) == 90.0


def test_overall_all_none_is_none():
    assert overall_score(
        {DIMENSION_TECHNICAL: None, DIMENSION_AEO: None}
    ) is None


def test_score_analysis_end_to_end():
    evals = [
        # Technical: passed=3, failed=1 -> 75.0
        _s(RULE_OUTCOME_PASS, 3.0, DIMENSION_TECHNICAL),
        _s(RULE_OUTCOME_FAIL, 1.0, DIMENSION_TECHNICAL),
        # AEO: passed=2, error=2 -> 100*2/4 = 50.0
        _s(RULE_OUTCOME_PASS, 2.0, DIMENSION_AEO),
        _s(RULE_OUTCOME_ERROR, 2.0, DIMENSION_AEO),
    ]
    scores = score_analysis(evals)
    assert scores.technical_score == 75.0
    assert scores.aeo_score == 50.0
    # overall = mean(75, 50) = 62.5
    assert scores.overall_score == 62.5
    assert scores.scoring_version == SCORING_VERSION


def test_score_analysis_missing_dimension_not_zero():
    evals = [
        _s(RULE_OUTCOME_PASS, 3.0, DIMENSION_TECHNICAL),
        # No applicable AEO rules.
        _s(RULE_OUTCOME_NOT_APPLICABLE, 3.0, DIMENSION_AEO),
    ]
    scores = score_analysis(evals)
    assert scores.technical_score == 100.0
    assert scores.aeo_score is None
    assert scores.overall_score == 100.0


# --- aggregation ----------------------------------------------------------


def test_aggregate_averages_latest_per_url():
    analyses = [
        AnalysisScoreInput("u1", 0, 100.0, 80.0, 90.0),
        AnalysisScoreInput("u2", 0, 60.0, 40.0, 50.0),
    ]
    agg = aggregate_scores(analyses)
    assert agg.technical_score == 80.0  # mean(100, 60)
    assert agg.aeo_score == 60.0  # mean(80, 40)
    assert agg.overall_score == 70.0  # mean(90, 50)
    assert agg.analyzed_url_count == 2


def test_aggregate_uses_latest_analysis_per_url():
    # Same url, two analyses; only the higher ordinal (latest) counts.
    analyses = [
        AnalysisScoreInput("u1", 0, 10.0, 10.0, 10.0),
        AnalysisScoreInput("u1", 1, 90.0, 90.0, 90.0),
    ]
    agg = aggregate_scores(analyses)
    assert agg.technical_score == 90.0
    assert agg.analyzed_url_count == 1


def test_aggregate_ignores_missing_and_error_urls():
    # A URL whose analysis errored (None scores) must NOT become zero: it is
    # simply excluded from the per-dimension mean. Missing URLs are never
    # passed in at all.
    analyses = [
        AnalysisScoreInput("u1", 0, 80.0, 80.0, 80.0),
        AnalysisScoreInput("u2", 0, None, None, None),  # errored analysis
    ]
    agg = aggregate_scores(analyses)
    # Mean over only the url with a real score -> 80.0, not (80+0)/2 = 40.
    assert agg.technical_score == 80.0
    assert agg.aeo_score == 80.0
    assert agg.overall_score == 80.0
    # Both URLs are counted as analyzed (u2 completed, just with no score).
    assert agg.analyzed_url_count == 2


def test_aggregate_empty_is_none():
    agg = aggregate_scores([])
    assert agg.technical_score is None
    assert agg.aeo_score is None
    assert agg.overall_score is None
    assert agg.analyzed_url_count == 0
