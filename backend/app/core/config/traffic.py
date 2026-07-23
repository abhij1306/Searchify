# Traffic projection configuration (invariant 1: all config lives here).
#
# Owns every tunable knob + vocabulary token for the Traffic surface
# (docs/roadmap/traffic.md section 8): the projection window + granularity
# knobs, the formula/normalization provenance versions stamped on every
# ``TrafficSnapshot`` (invariant 4), the GA4 inclusion-rule vocabularies
# (organic channel groups + the C1 referral-dimension dataset ids the
# AI-referral ingest consumes), and the page/query sort whitelists for the
# paged stat endpoints.
#
# Traffic is a pure PROJECTION over ``IntegrationMetricRow`` (invariant 7):
# it performs NO provider fetch, so no provider-fetch knobs live here —
# those belong to ``config/integrations.py`` (invariant 2).
from __future__ import annotations

from typing import Final

# The GA4 dataset ids are OWNED by config/integrations.py (cross-workstream
# contract C1) and imported here — never re-literalized (invariant 2).
from app.core.config.integrations import (
    DATASET_GA4_REFERRER_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
)

# --- Projection window + granularity ----------------------------------------
# Default trailing window when a request omits ``from``/``to``, and the hard
# cap on any served window.
TRAFFIC_DEFAULT_WINDOW_DAYS: Final = 28
TRAFFIC_MAX_WINDOW_DAYS: Final = 480

# Snapshot bucket granularity vocabulary. Shared with the LLM Analytics
# projection (same concept, one owner — invariant 2): ``config/analytics.py``
# aliases this set instead of forking a second ``day|week|month`` vocabulary.
TRAFFIC_GRANULARITY_DAY: Final = "day"
TRAFFIC_GRANULARITY_WEEK: Final = "week"
TRAFFIC_GRANULARITY_MONTH: Final = "month"
TRAFFIC_SNAPSHOT_GRANULARITIES: Final[frozenset[str]] = frozenset(
    {TRAFFIC_GRANULARITY_DAY, TRAFFIC_GRANULARITY_WEEK, TRAFFIC_GRANULARITY_MONTH}
)

# --- Provenance versions (invariant 4) ---------------------------------------
# Stamped on ``TrafficSnapshot.formula_version`` / ``.normalization_version``.
# Kept SEPARATE (normalization is NOT folded into a generic analyzer version)
# so a consumer can tell a URL/normalization change apart from an
# analytics-formula change (traffic.md section 8).
TRAFFIC_FORMULA_VERSION: Final = "traffic-formula-1"
TRAFFIC_NORMALIZATION_VERSION: Final = "traffic-normalization-1"

# --- GA4 inclusion rule (organic + AI-driven only; traffic.md section 3) -----
# A GA4 row folds into Traffic totals only when its default channel grouping
# is in this set OR its source/medium dims classify as an AI referral (via
# the deterministic classifier in ``domain/analytics/classification.py``).
TRAFFIC_GA4_ORGANIC_CHANNEL_GROUPS: Final[frozenset[str]] = frozenset(
    {"Organic Search"}
)
# The C1 GA4 referral-dimension datasets the referral ingest (A5) reads.
TRAFFIC_GA4_REFERRAL_DATASETS: Final[frozenset[str]] = frozenset(
    {DATASET_GA4_REFERRER_DAILY, DATASET_GA4_SOURCE_MEDIUM_DAILY}
)

# --- Sort whitelists (``?sort=`` hits stored aggregates only, invariant 7) ---
# Paging/sorting the /traffic/pages and /traffic/queries endpoints is
# restricted to these persisted aggregate columns — never a free-form column.
TRAFFIC_PAGE_SORT_WHITELIST: Final[frozenset[str]] = frozenset(
    {"impressions", "clicks", "ctr", "position", "sessions", "conversions"}
)
TRAFFIC_QUERY_SORT_WHITELIST: Final[frozenset[str]] = frozenset(
    {"impressions", "clicks", "ctr", "position"}
)
