# Opportunities configuration (invariant 1: all config lives in core/config).
#
# Owns EVERY tunable knob, enum, catalog entry, and version string for the
# Opportunities subsystem: the deterministic rule catalog, the site-issue ->
# opportunity-rule mapping sets, the priority-scoring formula weights, the
# analyzer/rule/formula versions stamped on every derived row (invariant 4),
# and the read/write bounds. Detection + scoring are deterministic
# projections over already-persisted visibility analysis + Site Health issue
# rows — no provider calls, no LLM (invariants 7 + 9). Domain, analysis, and
# API code READS these; it never hard-codes the literals inline.
from __future__ import annotations

from typing import Final

from app.core.config.projects import (
    PROMPT_INTENT_COMPARISON,
    PROMPT_INTENT_DISCOVERY,
    PROMPT_INTENT_LOCAL,
    PROMPT_INTENT_PURCHASE,
    PROMPT_INTENT_SERVICE,
)

# =========================================================================
# Provenance versions (invariant 4)
# =========================================================================
# Stamped on every ``Opportunity`` row + ``OpportunitySnapshot``. Bump
# ``ANALYZER_VERSION`` on any detector-logic change, ``RULE_VERSION`` on any
# catalog change, and ``FORMULA_VERSION`` on any scoring change so a derived
# row is always traceable to the exact logic that produced it (mirrors
# ``SCORING_RULE_VERSION`` in ``config/analysis.py``).
ANALYZER_VERSION: Final = "opp-analyzer-1"
RULE_VERSION: Final = "opp-rules-1"
FORMULA_VERSION: Final = "opp-formula-1"

# =========================================================================
# Vocabularies
# =========================================================================
# Opportunity type: which subsystem family the rule's evidence comes from.
OPPORTUNITY_TYPE_VISIBILITY: Final = "visibility"
OPPORTUNITY_TYPE_SITE: Final = "site"
OPPORTUNITY_TYPE_TRAFFIC: Final = "traffic"
OPPORTUNITY_TYPE_TOPIC: Final = "topic"
OPPORTUNITY_TYPES: Final[frozenset[str]] = frozenset(
    {
        OPPORTUNITY_TYPE_VISIBILITY,
        OPPORTUNITY_TYPE_SITE,
        OPPORTUNITY_TYPE_TRAFFIC,
        OPPORTUNITY_TYPE_TOPIC,
    }
)

# Severity vocabulary: the same five tokens as Site Health (D2) so the
# frontend badge palette helpers apply unchanged. Owned per-subsystem (do NOT
# import the site-health frozenset).
SEVERITY_CRITICAL: Final = "critical"
SEVERITY_HIGH: Final = "high"
SEVERITY_MEDIUM: Final = "medium"
SEVERITY_LOW: Final = "low"
SEVERITY_INFO: Final = "info"
OPPORTUNITY_SEVERITIES: Final[frozenset[str]] = frozenset(
    {
        SEVERITY_CRITICAL,
        SEVERITY_HIGH,
        SEVERITY_MEDIUM,
        SEVERITY_LOW,
        SEVERITY_INFO,
    }
)

# Human workflow status — the ONLY mutable field on an ``Opportunity`` row.
STATUS_OPEN: Final = "open"
STATUS_IN_PROGRESS: Final = "in_progress"
STATUS_DISMISSED: Final = "dismissed"
STATUS_RESOLVED: Final = "resolved"
OPPORTUNITY_STATUSES: Final[frozenset[str]] = frozenset(
    {
        STATUS_OPEN,
        STATUS_IN_PROGRESS,
        STATUS_DISMISSED,
        STATUS_RESOLVED,
    }
)
# Default list view: the triage queue (not yet closed by the human).
OPPORTUNITY_ACTIVE_STATUSES: Final[frozenset[str]] = frozenset(
    {STATUS_OPEN, STATUS_IN_PROGRESS}
)

# =========================================================================
# Coded API failures (stable tokens returned to the client)
# =========================================================================
CODE_OPPORTUNITY_SUPERSEDED: Final = "opportunity_superseded"

# =========================================================================
# Rule catalog
# =========================================================================


class OpportunityRule:
    """One deterministic opportunity rule (frozen catalog entry).

    The catalog is config, not a table, so a persisted ``Opportunity.rule_id``
    is a validated string, never a DB FK: the write path validates against
    this catalog (an unknown id is rejected) and stamps ``RULE_VERSION`` onto
    the row for provenance (invariants 1 + 4). ``title`` + ``remediation``
    are persisted on the row at write time (mirrors ``SiteIssue.remediation``
    snapshot semantics — a catalog relabel never rewrites history). A
    disabled rule (``enabled=False``) ships config-only: its shape is stable
    but no detector emits it.
    """

    __slots__ = (
        "rule_id",
        "opportunity_type",
        "severity",
        "title",
        "remediation",
        "enabled",
    )

    def __init__(
        self,
        *,
        rule_id: str,
        opportunity_type: str,
        severity: str,
        title: str,
        remediation: str,
        enabled: bool = True,
    ) -> None:
        self.rule_id = rule_id
        self.opportunity_type = opportunity_type
        self.severity = severity
        self.title = title
        self.remediation = remediation
        self.enabled = enabled


# The v1 catalog. The two visibility rules + the two site-sourced rules are
# enabled; ``low_share_of_voice_theme`` (no persisted per-topic SOV aggregate)
# and ``high_traffic_low_visibility`` (no Traffic surface) ship disabled as
# documented config-only entries.
OPPORTUNITY_RULES: Final[tuple[OpportunityRule, ...]] = (
    OpportunityRule(
        rule_id="brand_absent_high_value_prompt",
        opportunity_type=OPPORTUNITY_TYPE_VISIBILITY,
        severity=SEVERITY_HIGH,
        title="Brand absent on high-value prompt",
        remediation=(
            "Publish or update an owned page that directly answers this"
            " prompt: lead with a clear, quotable definition, then add"
            " structured data so answer engines can attribute it. Re-run an"
            " audit to confirm owned citations appear."
        ),
    ),
    OpportunityRule(
        rule_id="owned_page_not_cited",
        opportunity_type=OPPORTUNITY_TYPE_VISIBILITY,
        severity=SEVERITY_MEDIUM,
        title="Owned page not cited for target prompt",
        remediation=(
            "Strengthen the owned page that should win this prompt: align its"
            " title, headings, and opening answer with the prompt intent so"
            " answer engines have a citable owned source."
        ),
    ),
    OpportunityRule(
        rule_id="missing_structured_data",
        opportunity_type=OPPORTUNITY_TYPE_SITE,
        severity=SEVERITY_MEDIUM,
        title="Missing structured data on owned page",
        remediation=(
            "Add schema.org structured data (JSON-LD preferred) so answer"
            " engines can parse and attribute the page's content."
        ),
    ),
    OpportunityRule(
        rule_id="thin_content",
        opportunity_type=OPPORTUNITY_TYPE_SITE,
        severity=SEVERITY_LOW,
        title="Thin content on owned page",
        remediation=(
            "Add substantive, answer-oriented body content to the page so"
            " answer engines have enough text to quote and cite."
        ),
    ),
    OpportunityRule(
        rule_id="low_share_of_voice_theme",
        opportunity_type=OPPORTUNITY_TYPE_TOPIC,
        severity=SEVERITY_MEDIUM,
        title="Low share of voice in theme",
        remediation=(
            "Increase owned coverage across this theme: publish"
            " answer-oriented pages for the theme's highest-value prompts."
        ),
        # DEFERRED (delta 3): no persisted per-topic SOV aggregate yet.
        enabled=False,
    ),
    OpportunityRule(
        rule_id="high_traffic_low_visibility",
        opportunity_type=OPPORTUNITY_TYPE_TRAFFIC,
        severity=SEVERITY_MEDIUM,
        title="High-traffic page with low answer-engine visibility",
        remediation=(
            "Prioritize this high-traffic page for AEO improvements: add"
            " quotable answers and structured data where engines already send"
            " visitors."
        ),
        # DEFERRED (delta 4): the Traffic surface is not implemented.
        enabled=False,
    ),
)

# Fast lookup by rule id.
OPPORTUNITY_RULES_BY_ID: Final[dict[str, OpportunityRule]] = {
    rule.rule_id: rule for rule in OPPORTUNITY_RULES
}


def validate_rule_id(rule_id: str) -> str:
    """Return ``rule_id`` when it is a known catalog id; reject unknown ids.

    The write path calls this before persisting any derived row so a row can
    never reference a rule the catalog does not own (invariants 1 + 4).
    """
    if rule_id not in OPPORTUNITY_RULES_BY_ID:
        raise ValueError(f"unknown opportunity rule_id: {rule_id!r}")
    return rule_id


# =========================================================================
# Site-issue -> opportunity-rule mapping sets (config-owned, invariant 1)
# =========================================================================
# ``SiteIssue`` rule ids (from the Site Health catalog) that project into
# each site-type opportunity rule. Owned here so the detector never
# hard-codes the mapping.
SITE_STRUCTURED_DATA_RULE_IDS: Final[frozenset[str]] = frozenset(
    {"aeo.structured_data_present"}
)
SITE_THIN_CONTENT_RULE_IDS: Final[frozenset[str]] = frozenset(
    {"aeo.sufficient_text"}
)

# =========================================================================
# Deterministic scoring formula (config-owned tables, invariants 1 + 9)
# =========================================================================
# priority = SEVERITY_WEIGHTS[severity] * value_factor * gap_factor
#            * PRIORITY_SCALE, rounded to PRIORITY_ROUNDING_DECIMALS.
SEVERITY_WEIGHTS: Final[dict[str, float]] = {
    SEVERITY_CRITICAL: 4.0,
    SEVERITY_HIGH: 3.0,
    SEVERITY_MEDIUM: 2.0,
    SEVERITY_LOW: 1.0,
    SEVERITY_INFO: 0.5,
}
# Fallback for a severity outside the known vocabulary (fail-safe, neutral).
SEVERITY_WEIGHT_DEFAULT: Final = 1.0

# Value factor by prompt intent (covers every ``PROMPT_INTENTS`` token).
INTENT_VALUE_WEIGHTS: Final[dict[str, float]] = {
    PROMPT_INTENT_DISCOVERY: 1.0,
    PROMPT_INTENT_COMPARISON: 1.5,
    PROMPT_INTENT_PURCHASE: 2.0,
    PROMPT_INTENT_SERVICE: 1.5,
    PROMPT_INTENT_LOCAL: 1.25,
}
# Fallback for an empty/unknown intent.
INTENT_VALUE_DEFAULT: Final = 1.0

# Gap factor: competitor pressure (bounded) + owned-citation shortfall.
GAP_COMPETITOR_WEIGHT: Final = 1.0
GAP_COMPETITOR_CAP: Final = 3
GAP_OWNED_CITATION_WEIGHT: Final = 1.0

# Site-sourced rules carry no intent/gap modulation: their factors are the
# neutral base (the severity weight already encodes their importance).
SITE_VALUE_FACTOR: Final = 1.0
SITE_GAP_FACTOR: Final = 1.0

PRIORITY_SCALE: Final = 10.0
PRIORITY_ROUNDING_DECIMALS: Final = 1
# Write-time floor: hits below this score are never persisted. Set so a
# ``low``-severity hit at base factors (1.0 * 1.0 * 1.0 * 10 = 10.0) still
# surfaces — every enabled catalog rule can produce rows — while ``info``
# hits at base factors (5.0) stay below the floor.
MIN_PRIORITY_TO_SURFACE: Final = 10.0

# =========================================================================
# Bounds (recompute reads, list pagination, exports)
# =========================================================================
# Bounded recompute reads (deterministic truncation order: prompt_index, id).
RECOMPUTE_MAX_ANALYSES: Final = 5000
RECOMPUTE_MAX_ISSUES: Final = 5000
# List pagination bounds.
LIST_DEFAULT_LIMIT: Final = 50
LIST_MAX_LIMIT: Final = 200
# Hard cap on rows materialized for one export request.
MAX_EXPORT_ITEMS: Final = 20000
