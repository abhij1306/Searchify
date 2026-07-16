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
