# Product Visibility (Agentic Commerce) configuration (invariant 1).
#
# Owns EVERY tunable knob, enum, and version string for the product-visibility
# surface: the catalog origin vocabulary, the deterministic analyzer/rule
# versions stamped on every product derived row (invariant 4), the price
# extraction/match knobs, the rank-distribution buckets, the per-SKU
# completeness required-attribute matrix, the evidence projection bounds, and
# the CSV import cap. Domain, analysis, worker, and API code READS these; it
# never hard-codes the literals inline.
from __future__ import annotations

from typing import Final

# --- Provenance versions (invariant 4) -----------------------------------
# Bumped whenever the deterministic product scoring/aggregation logic changes
# so a derived row can always be traced to the exact rules that produced it.
# Stamped onto ``ProductResponseAnalysis`` / ``ProductMention`` /
# ``ProductMetricSnapshot``. Separate from the brand-level
# ``config/analysis.py`` versions — the product pass is a sibling analyzer.
PRODUCT_ANALYZER_VERSION: Final = "product-analysis-1"
PRODUCT_SCORING_RULE_VERSION: Final = "product-scoring-v1"

# --- Catalog origin vocabulary --------------------------------------------
PRODUCT_ORIGIN_MANUAL: Final = "manual"
PRODUCT_ORIGIN_IMPORTED: Final = "imported"
PRODUCT_ORIGINS: Final[frozenset[str]] = frozenset(
    {PRODUCT_ORIGIN_MANUAL, PRODUCT_ORIGIN_IMPORTED}
)
DEFAULT_PRODUCT_ORIGIN: Final = PRODUCT_ORIGIN_MANUAL

# --- Price extraction + match knobs (deterministic, invariant 9) ----------
# Currency detection: ISO 4217 code -> accepted literal markers (symbol or
# code), longest-first so ``US$`` wins over ``$``. A price mention is only
# extracted when a known marker is present, so every mention carries a
# resolved currency.
PRICE_CURRENCY_PATTERNS: Final[dict[str, tuple[str, ...]]] = {
    "USD": ("US$", "$", "USD"),
    "EUR": ("€", "EUR"),
    "GBP": ("£", "GBP"),
    "AUD": ("A$", "AU$", "AUD"),
    "CAD": ("C$", "CA$", "CAD"),
}
# Character window scanned for a price mention around a product mention's
# first offset (the price usually sits next to the product in a list item).
PRODUCT_PRICE_WINDOW_CHARS: Final = 160
# A mentioned price matches the catalog price when
# ``abs(mentioned - catalog) <= max(catalog * PCT, ABS)`` — the absolute floor
# keeps tiny catalog prices from demanding an exact match.
PRODUCT_PRICE_TOLERANCE_PCT: Final = 0.05
PRODUCT_PRICE_TOLERANCE_ABS: Final = 1.0

# --- Rank distribution buckets --------------------------------------------
# (label, min_rank, max_rank) inclusive; ``None`` max = unbounded. Mentions
# with no detected enumeration land in the extra ``unranked`` bucket.
PRODUCT_RANK_BUCKETS: Final[tuple[tuple[str, int, int | None], ...]] = (
    ("top_1", 1, 1),
    ("top_2_3", 2, 3),
    ("top_4_5", 4, 5),
    ("rank_6_plus", 6, None),
)
PRODUCT_RANK_BUCKET_UNRANKED: Final = "unranked"

# --- Per-SKU data-quality completeness matrix (computed on read) ----------
# Top-level Product fields that must be populated...
PRODUCT_REQUIRED_ATTRIBUTES: Final[tuple[str, ...]] = (
    "name",
    "sku",
    "price",
    "currency",
    "url",
)
# ...plus these keys inside the ``attributes`` JSONB bag.
PRODUCT_COMPLETENESS_ATTRIBUTE_KEYS: Final[tuple[str, ...]] = (
    "brand",
    "category",
    "gtin",
    "mpn",
    "availability",
    "condition",
    "description",
)

# --- Evidence projection bounds (mirror config/analysis.py evidence) ------
# Default page size when the request omits ``limit``.
PRODUCT_EVIDENCE_DEFAULT_LIMIT: Final = 100
# Hard cap on the ``limit`` a single request may ask for (422 above this).
PRODUCT_EVIDENCE_MAX_LIMIT: Final = 500

# --- CSV import cap --------------------------------------------------------
# Maximum rows accepted by a single catalog import (422 above this).
PRODUCT_IMPORT_MAX_ROWS: Final = 500
