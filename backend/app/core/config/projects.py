# Project + prompt configuration (invariant 1: all config lives in core/config).
#
# Owns the enumerations and defaults for the projects/prompts vertical slice
# (B3): benchmark modes, prompt intents, and prompt origins. Domain, schema,
# and service code READ these values; they never hard-code the literals inline.
# Ported/adapted from the reference ``config/ai_visibility.py``.
from __future__ import annotations

from typing import Final

# --- Benchmark modes ------------------------------------------------------
# How an audit frames its prompts to the answer engine.
BENCHMARK_MODE_CONSUMER_LIKE: Final = "consumer_like"
BENCHMARK_MODE_CONTROLLED_LOCALIZED: Final = "controlled_localized"
BENCHMARK_MODE_FORCED_GROUNDED: Final = "forced_grounded"
BENCHMARK_MODES: Final[frozenset[str]] = frozenset(
    {
        BENCHMARK_MODE_CONSUMER_LIKE,
        BENCHMARK_MODE_CONTROLLED_LOCALIZED,
        BENCHMARK_MODE_FORCED_GROUNDED,
    }
)
DEFAULT_BENCHMARK_MODE: Final = BENCHMARK_MODE_CONTROLLED_LOCALIZED

# --- Prompt intents -------------------------------------------------------
# The shopper-journey stage a prompt probes. Empty string means "unspecified".
PROMPT_INTENT_DISCOVERY: Final = "discovery"
PROMPT_INTENT_COMPARISON: Final = "comparison"
PROMPT_INTENT_PURCHASE: Final = "purchase"
PROMPT_INTENT_SERVICE: Final = "service"
PROMPT_INTENT_LOCAL: Final = "local"
PROMPT_INTENTS: Final[frozenset[str]] = frozenset(
    {
        PROMPT_INTENT_DISCOVERY,
        PROMPT_INTENT_COMPARISON,
        PROMPT_INTENT_PURCHASE,
        PROMPT_INTENT_SERVICE,
        PROMPT_INTENT_LOCAL,
    }
)

# --- Prompt origin --------------------------------------------------------
# How a prompt entered the library. ``generated`` is roadmap (B-4) — the
# ``/generate`` endpoint is a stub at MVP, but the origin value is defined here
# so the enum is complete and stable.
PROMPT_ORIGIN_MANUAL: Final = "manual"
PROMPT_ORIGIN_IMPORTED: Final = "imported"
PROMPT_ORIGIN_GENERATED: Final = "generated"
PROMPT_ORIGINS: Final[frozenset[str]] = frozenset(
    {
        PROMPT_ORIGIN_MANUAL,
        PROMPT_ORIGIN_IMPORTED,
        PROMPT_ORIGIN_GENERATED,
    }
)
DEFAULT_PROMPT_ORIGIN: Final = PROMPT_ORIGIN_MANUAL

# --- Repetition bounds ----------------------------------------------------
# Default + allowed range for a project's per-audit repetition count.
DEFAULT_REPETITIONS: Final = 3
MIN_REPETITIONS: Final = 1
MAX_REPETITIONS: Final = 10
