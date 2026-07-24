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
    DATASET_GA4_CHANNEL_DAILY,
    DATASET_GA4_LANDING_DAILY,
    DATASET_GA4_REFERRER_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    DATASET_GSC_PAGE_DAILY,
    DATASET_GSC_QUERY_DAILY,
    INTEGRATION_PROVIDER_GA4,
    INTEGRATION_PROVIDER_GSC,
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
# The granularity served when a request omits ``granularity`` — and the
# snapshot the paged stat endpoints read: the per-page/per-query fold is
# granularity-independent (bucketing only shapes the series), so the
# default-granularity snapshot's stat rows serve every table request.
TRAFFIC_DEFAULT_GRANULARITY: Final = TRAFFIC_GRANULARITY_DAY

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

# --- Consumed datasets (the snapshot projection's read set, contract C1) -----
# The dataset ids the ``traffic_snapshot_refresh`` executor reads from
# ``IntegrationMetricRow`` (A7): the GSC page/query dailies (totals + page /
# query stats) and the GA4 channel / source-medium / landing dailies (the
# inclusion rule + page GA4 metrics). ``ga4_referrer_daily`` is OWNED by the
# A5 referral ingest and deliberately absent — folding it in would
# double-count the AI sessions already measured via the source-medium
# dataset.
TRAFFIC_CONSUMED_DATASETS: Final[frozenset[str]] = frozenset(
    {
        DATASET_GSC_PAGE_DAILY,
        DATASET_GSC_QUERY_DAILY,
        DATASET_GA4_CHANNEL_DAILY,
        DATASET_GA4_SOURCE_MEDIUM_DAILY,
        DATASET_GA4_LANDING_DAILY,
    }
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
# Sort direction idiom: a leading ``-`` requests descending (what the table
# sends for its default "top rows" view); a bare key is ascending. This is
# the effective sort when ``?sort=`` is omitted.
TRAFFIC_DEFAULT_SORT: Final = "-impressions"
# Page size for the keyset-paged stat tables (contract C4): every page reads
# at most this many persisted stat rows (+1 lookahead row for the
# ``next_cursor``), so a response is always bounded.
TRAFFIC_TABLE_PAGE_SIZE: Final = 50

# --- Sync pass-through (``POST /projects/{id}/traffic/sync``) -----------------
# The provider vocabulary of the traffic-sync fan-out: the project's ACTIVE
# mapped connections of these providers each get one on-demand
# ``IntegrationSyncRun`` (Bing carries no Traffic-consumed dataset). The
# enqueue itself is OWNED by ``domain/integrations/sync.py`` (invariant 2) —
# only the fan-out vocabulary lives here.
TRAFFIC_SYNC_PROVIDERS: Final[frozenset[str]] = frozenset(
    {INTEGRATION_PROVIDER_GSC, INTEGRATION_PROVIDER_GA4}
)
