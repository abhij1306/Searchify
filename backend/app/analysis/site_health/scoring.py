# Deterministic Site Health scoring (Task 5).
#
# PURE scoring per the EXACT approved formula (no I/O, no ORM). One owner for
# the per-dimension score, the overall weighted score, and the crawl-level
# aggregation across analyses. Determinism is preserved so the same evaluations
# always produce the same scores (invariant 9), and every score is stamped with
# ``SCORING_VERSION``.
#
# FORMULA (verbatim scope):
#   dimension_score = 100 × passed_weight
#                     / (passed_weight + failed_weight + error_weight)
#   over APPLICABLE evaluations only (``not_applicable`` excluded); ``error``
#   is given ZERO credit but its weight stays in the denominator (it is a
#   distinct outcome, never silently dropped and never coerced to a pass).
#   Round ONCE to ``SCORE_ROUNDING_DECIMALS``.
#   overall_score = weighted mean of the AVAILABLE Technical/AEO dimension
#   scores using ``DIMENSION_WEIGHT_TECHNICAL`` / ``DIMENSION_WEIGHT_AEO``.
#
# A dimension with no applicable (pass/fail/error) evaluations has NO score
# (None) — it is excluded from the overall mean rather than counted as zero.
# Aggregation likewise ignores missing/error URLs: a URL with no completed
# analysis (or a None dimension score) never becomes a zero.
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.core.config.site_health import (
    DIMENSION_AEO,
    DIMENSION_TECHNICAL,
    DIMENSION_WEIGHT_AEO,
    DIMENSION_WEIGHT_TECHNICAL,
    RULE_OUTCOME_ERROR,
    RULE_OUTCOME_FAIL,
    RULE_OUTCOME_NOT_APPLICABLE,
    RULE_OUTCOME_PASS,
    SCORE_ROUNDING_DECIMALS,
    SCORING_VERSION,
)


@dataclass(frozen=True)
class _Scored:
    """A minimal (outcome, weight, dimension) triple scoring reads.

    Decouples scoring from the ORM: the worker adapts either
    ``rules.RuleEvaluation`` or a persisted ``SiteRuleEvaluation`` into this.
    """

    dimension: str
    outcome: str
    weight: float


@dataclass(frozen=True)
class DimensionScore:
    """The scored result for one dimension (None when not applicable)."""

    dimension: str
    score: float | None
    passed_weight: float
    failed_weight: float
    error_weight: float
    applicable_count: int


@dataclass(frozen=True)
class AnalysisScores:
    """The full per-analysis scoring result (dimension + overall scores)."""

    technical_score: float | None
    aeo_score: float | None
    overall_score: float | None
    scoring_version: str = SCORING_VERSION
    dimensions: dict[str, DimensionScore] = field(default_factory=dict)


def _round(value: float) -> float:
    """Round once to the config decimal places (deterministic)."""
    return round(value, SCORE_ROUNDING_DECIMALS)


def score_dimension(
    evaluations: Iterable[_Scored], *, dimension: str
) -> DimensionScore:
    """Score a single dimension, filtering ``evaluations`` to it first.

    ``not_applicable`` is excluded entirely. ``error`` contributes its weight
    to the denominator but ZERO to the numerator (distinct, zero-credit). A
    dimension with no applicable (pass/fail/error) evaluation has ``score=None``
    (not zero). Tracks the exact applicable row count so callers can tell
    "no applicable rules" from "all not_applicable".
    """
    passed = 0.0
    failed = 0.0
    errored = 0.0
    applicable = 0
    for ev in evaluations:
        if ev.dimension != dimension:
            continue
        outcome = ev.outcome
        weight = max(0.0, float(ev.weight))
        if outcome == RULE_OUTCOME_NOT_APPLICABLE:
            continue
        if outcome == RULE_OUTCOME_PASS:
            passed += weight
            applicable += 1
        elif outcome == RULE_OUTCOME_FAIL:
            failed += weight
            applicable += 1
        elif outcome == RULE_OUTCOME_ERROR:
            errored += weight
            applicable += 1
    denominator = passed + failed + errored
    score = None if denominator <= 0 else _round(100.0 * passed / denominator)
    return DimensionScore(
        dimension=dimension,
        score=score,
        passed_weight=passed,
        failed_weight=failed,
        error_weight=errored,
        applicable_count=applicable,
    )


_DIMENSION_WEIGHTS: dict[str, float] = {
    DIMENSION_TECHNICAL: DIMENSION_WEIGHT_TECHNICAL,
    DIMENSION_AEO: DIMENSION_WEIGHT_AEO,
}


def overall_score(dimension_scores: dict[str, float | None]) -> float | None:
    """Config-weighted mean of the AVAILABLE dimension scores.

    A dimension whose score is ``None`` (no applicable rules) is dropped from
    both the numerator and the denominator, so it is excluded rather than
    counted as zero. Returns ``None`` when no dimension has a score.
    """
    weighted_sum = 0.0
    weight_total = 0.0
    for dimension, score in dimension_scores.items():
        if score is None:
            continue
        weight = _DIMENSION_WEIGHTS.get(dimension, 0.0)
        if weight <= 0:
            continue
        weighted_sum += weight * score
        weight_total += weight
    if weight_total <= 0:
        return None
    return _round(weighted_sum / weight_total)


def score_analysis(evaluations: Iterable[_Scored]) -> AnalysisScores:
    """Score a whole page analysis: per-dimension scores + the overall score.

    ``evaluations`` is the full set for one analysis (both dimensions). Returns
    an ``AnalysisScores`` with each dimension's score (None when N/A) and the
    config-weighted overall (None when no dimension scored).
    """
    evals = list(evaluations)
    technical = score_dimension(evals, dimension=DIMENSION_TECHNICAL)
    aeo = score_dimension(evals, dimension=DIMENSION_AEO)
    overall = overall_score(
        {
            DIMENSION_TECHNICAL: technical.score,
            DIMENSION_AEO: aeo.score,
        }
    )
    return AnalysisScores(
        technical_score=technical.score,
        aeo_score=aeo.score,
        overall_score=overall,
        dimensions={
            DIMENSION_TECHNICAL: technical,
            DIMENSION_AEO: aeo,
        },
    )


@dataclass(frozen=True)
class AnalysisScoreInput:
    """One completed analysis's scores for crawl-level aggregation.

    ``url_key`` identifies the monitored URL (so only the LATEST completed
    analysis per URL is aggregated). A missing/errored URL is simply NOT passed
    in — the aggregator never fabricates a zero for it.
    """

    url_key: str
    ordinal: int
    technical_score: float | None
    aeo_score: float | None
    overall_score: float | None


@dataclass(frozen=True)
class AggregateScores:
    """The crawl-level aggregate over the latest completed analyses."""

    technical_score: float | None
    aeo_score: float | None
    overall_score: float | None
    analyzed_url_count: int
    scoring_version: str = SCORING_VERSION


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return _round(sum(values) / len(values))


def aggregate_scores(
    analyses: Iterable[AnalysisScoreInput],
) -> AggregateScores:
    """Aggregate the LATEST completed analysis per URL, ignoring missing/error.

    Deduplicates to the highest-``ordinal`` (latest) analysis per ``url_key``,
    then averages each dimension over ONLY the analyses that actually have a
    (non-None) score for it. A URL with no completed analysis is never present
    here, and a None dimension score is skipped rather than treated as zero, so
    missing/error URLs cannot drag an aggregate to zero.
    """
    latest: dict[str, AnalysisScoreInput] = {}
    for analysis in analyses:
        existing = latest.get(analysis.url_key)
        if existing is None or analysis.ordinal >= existing.ordinal:
            latest[analysis.url_key] = analysis

    technical = [
        a.technical_score
        for a in latest.values()
        if a.technical_score is not None
    ]
    aeo = [a.aeo_score for a in latest.values() if a.aeo_score is not None]
    overall = [
        a.overall_score for a in latest.values() if a.overall_score is not None
    ]
    return AggregateScores(
        technical_score=_mean(technical),
        aeo_score=_mean(aeo),
        overall_score=_mean(overall),
        analyzed_url_count=len(latest),
    )
