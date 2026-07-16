# Deterministic analysis + scoring configuration (invariant 1).
#
# Owns every tunable knob the B6 analysis/scoring pipeline reads: the
# ``analyzer_version`` + scoring-rule version stamped on every derived row
# (invariant 4), the ambiguous-alias guard list, and the public paid-list
# pricing constants used for the cost estimate. Analysis, scoring, and the
# finalize path READ these; they never hard-code the literals inline. Ported
# from the reference ``config/ai_visibility.py``.
from __future__ import annotations

from typing import Final

# --- Provenance versions (invariant 4) -----------------------------------
# Bumped whenever the deterministic scoring/aggregation logic changes so a
# derived row can always be traced to the exact rules that produced it. Stamped
# onto ``ResponseAnalysis`` / ``BrandMention`` / ``CompetitorMention`` /
# ``Citation`` / ``MetricSnapshot`` and the parent ``Audit`` at finalize.
ANALYZER_VERSION: Final = "b6-analysis-1"
# The per-execution/aggregate formula version (separate from the analyzer so a
# formula-only change can be tracked independently of an extraction change).
SCORING_RULE_VERSION: Final = "scoring-v1"

# --- Cross-run Visibility trend projection (roadmap: visibility-trends) ----
# The trends endpoint is a pure PROJECTION over the already-persisted per-run
# ``MetricSnapshot`` rows (invariant 7): it introduces NO new version constant
# (each point is stamped with the ``analyzer_version`` / ``scoring_rule_version``
# its source snapshot already carries, invariant 2/4). These knobs only tune
# how that projection is windowed and bucketed.
#
# Allowed ``granularity`` values: ``run`` returns one point per persisted
# snapshot; ``week`` / ``month`` fold snapshots into deterministic UTC buckets.
VISIBILITY_TREND_GRANULARITIES: Final[frozenset[str]] = frozenset(
    {"run", "week", "month"}
)
# Default when the request omits ``granularity``.
VISIBILITY_TREND_DEFAULT_GRANULARITY: Final = "run"
# Cap on the number of newest source snapshots a single request considers (the
# final response is still returned in chronological order).
VISIBILITY_TREND_MAX_POINTS: Final = 100
# When True, a requested week/month bucket that would fold snapshots produced
# under different analyzer/scoring versions is NOT emitted; the whole selected
# range falls back to raw per-run points so no bucket ever mixes versions.
# When False, such a bucket is emitted but flagged ``spans_version_boundary``
# with every contributing version listed.
VISIBILITY_TRENDS_STRICT_VERSION_BUCKETS: Final = True

# --- Execution-evidence projection (roadmap: visibility Mentions & Fanout) -
# The evidence endpoint is a pure READ-ONLY projection over already-persisted
# per-execution rows (``ResponseAnalysis`` + its mention/citation children +
# the frozen ``AuditTask``/immutable ``RawResponseArtifact`` search events). It
# introduces NO new version constant and NEVER calls a provider (invariant 7):
# every row already carries the analyzer/scoring versions its source persisted.
# These knobs only bound how many newest executions a single request returns.
#
# Default page size when the request omits ``limit``.
VISIBILITY_EVIDENCE_DEFAULT_LIMIT: Final = 100
# Hard cap on the ``limit`` a single request may ask for (422 above this).
VISIBILITY_EVIDENCE_MAX_LIMIT: Final = 500

# --- Ambiguous alias guard -----------------------------------------------
# Aliases that are also common English words; a bare occurrence is only counted
# as a retailer mention when disambiguated (e.g. "Target Australia" or a proper
# noun that is not an obvious semantic use). Ported verbatim.
AMBIGUOUS_ALIASES: Final[frozenset[str]] = frozenset({"target"})

# --- Cost estimate pricing (public paid-list, USD) -----------------------
# Gemini 2.5 Flash public list prices used for the paid-list token/grounding
# estimate. Estimates only — actual spend may be zero within free allowances.
GEMINI_25_FLASH_INPUT_PER_MILLION_USD: Final = 0.30
GEMINI_25_FLASH_OUTPUT_PER_MILLION_USD: Final = 2.50
GEMINI_25_GROUNDED_PROMPT_USD: Final = 0.035
