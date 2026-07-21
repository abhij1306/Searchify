"""Brand-profile knowledge-base contract and guardrails.

The domain and schema layers import these tokens and bounds so assisted
features share one stable contract (invariant 1).
"""

from __future__ import annotations

from typing import Final

BRAND_PROFILE_FIELD_DESCRIPTION: Final = "description"
BRAND_PROFILE_FIELD_POSITIONING: Final = "positioning"
BRAND_PROFILE_FIELD_PRODUCTS_SERVICES: Final = "products_services"
BRAND_PROFILE_FIELD_TARGET_AUDIENCE: Final = "target_audience"
BRAND_PROFILE_FIELDS: Final[tuple[str, ...]] = (
    BRAND_PROFILE_FIELD_DESCRIPTION,
    BRAND_PROFILE_FIELD_POSITIONING,
    BRAND_PROFILE_FIELD_PRODUCTS_SERVICES,
    BRAND_PROFILE_FIELD_TARGET_AUDIENCE,
)

BRAND_PROFILE_SOURCE_MANUAL: Final = "manual"
BRAND_PROFILE_SOURCE_WEB_EVIDENCE: Final = "web_evidence"
BRAND_PROFILE_SOURCE_AI_SUGGESTED: Final = "ai_suggested"
BRAND_PROFILE_SOURCE_TOKENS: Final[frozenset[str]] = frozenset(
    {
        BRAND_PROFILE_SOURCE_MANUAL,
        BRAND_PROFILE_SOURCE_WEB_EVIDENCE,
        BRAND_PROFILE_SOURCE_AI_SUGGESTED,
    }
)

BRAND_PROFILE_TEXT_MAX_CHARS: Final = 4_000
BRAND_PROFILE_PRODUCT_MAX_CHARS: Final = 255
BRAND_PROFILE_PRODUCTS_MAX_COUNT: Final = 100

BRAND_KNOWLEDGE_CONTEXT_VERSION: Final = "brand-kb-v1"
BRAND_PROFILE_SUGGESTER_VERSION: Final = "brand-profile-suggest-v1"

BRAND_PROFILE_SUGGESTION_SYSTEM_PROMPT: Final = (
    "You draft a concise brand knowledge profile for a human to review. "
    "Use the supplied brand identity and market context as reference data. "
    "Use only facts you confidently know; do not claim to have browsed the "
    "website and do not invent unsupported details. Leave a field empty when "
    "the available context is insufficient.\n"
    "The positioning must focus on the actual competitive segment: price "
    "tier, target customer, product breadth, and meaningful differentiation. "
    "Do not collapse value, mid-market, premium, specialist, and fast-fashion "
    "brands into one generic category.\n"
    "Products/services must be short category labels, not marketing prose.\n"
    'Respond with ONLY JSON shaped as: {"description": str, "positioning": '
    'str, "products_services": [str], "target_audience": str}. No markdown.'
)
