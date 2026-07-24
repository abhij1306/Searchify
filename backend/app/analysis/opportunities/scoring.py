# Opportunities deterministic priority scoring (pure, invariant 9).
#
# The priority score is a pure function of a detector hit's factors:
#
#   priority = SEVERITY_WEIGHTS[severity] * value_factor * gap_factor
#              * PRIORITY_SCALE        (rounded to PRIORITY_ROUNDING_DECIMALS)
#
# Every table + knob is read from ``core/config/opportunities.py`` (invariant
# 1); nothing here touches the DB, the network, or an LLM (invariants 7 + 9).
# Same inputs + same ``FORMULA_VERSION`` -> same score, always.
from __future__ import annotations

from app.core.config.opportunities import (
    GAP_COMPETITOR_CAP,
    GAP_COMPETITOR_WEIGHT,
    GAP_OWNED_CITATION_WEIGHT,
    INTENT_VALUE_DEFAULT,
    INTENT_VALUE_WEIGHTS,
    PRIORITY_ROUNDING_DECIMALS,
    PRIORITY_SCALE,
    SEVERITY_WEIGHT_DEFAULT,
    SEVERITY_WEIGHTS,
)


def value_factor_for_intent(intent: str | None) -> float:
    """Config-weighted value of the prompt's intent (unknown/empty -> default)."""
    key = (intent or "").strip().lower()
    return INTENT_VALUE_WEIGHTS.get(key, INTENT_VALUE_DEFAULT)


def gap_factor_visibility(
    *, competitor_count: int, owned_citation_rate: float
) -> float:
    """Bounded visibility gap factor (always >= 1.0).

    Grows with the number of distinct competitors present (capped at
    ``GAP_COMPETITOR_CAP``) and shrinks as the owned-citation rate approaches
    full coverage: at an owned rate of 1.0 the gap is the neutral 1.0 no
    matter how many competitors appear (there is no citation gap to close).
    """
    competitors = min(max(int(competitor_count), 0), GAP_COMPETITOR_CAP)
    owned_rate = min(max(float(owned_citation_rate), 0.0), 1.0)
    owned_gap = 1.0 - owned_rate
    return 1.0 + (
        GAP_COMPETITOR_WEIGHT * competitors * GAP_OWNED_CITATION_WEIGHT * owned_gap
    )


def priority_score(*, severity: str, value_factor: float, gap_factor: float) -> float:
    """The rounded deterministic priority score for one detector hit.

    An unknown severity fails safe to ``SEVERITY_WEIGHT_DEFAULT`` rather than
    raising (scoring never invents new severity semantics — the catalog owns
    the vocabulary; this only guards the arithmetic).
    """
    severity_weight = SEVERITY_WEIGHTS.get(severity, SEVERITY_WEIGHT_DEFAULT)
    return round(
        severity_weight * value_factor * gap_factor * PRIORITY_SCALE,
        PRIORITY_ROUNDING_DECIMALS,
    )
