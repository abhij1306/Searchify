"""Opportunities config: catalog shape, vocabularies, weights, validation."""

from __future__ import annotations

import pytest

from app.core.config.opportunities import (
    GAP_COMPETITOR_CAP,
    GAP_COMPETITOR_WEIGHT,
    GAP_OWNED_CITATION_WEIGHT,
    INTENT_VALUE_DEFAULT,
    INTENT_VALUE_WEIGHTS,
    LIST_DEFAULT_LIMIT,
    LIST_MAX_LIMIT,
    MAX_EXPORT_ITEMS,
    MIN_PRIORITY_TO_SURFACE,
    OPPORTUNITY_ACTIVE_STATUSES,
    OPPORTUNITY_RULES,
    OPPORTUNITY_RULES_BY_ID,
    OPPORTUNITY_SEVERITIES,
    OPPORTUNITY_STATUSES,
    OPPORTUNITY_TYPES,
    PRIORITY_SCALE,
    RECOMPUTE_MAX_ANALYSES,
    RECOMPUTE_MAX_ISSUES,
    SEVERITY_WEIGHTS,
    SITE_STRUCTURED_DATA_RULE_IDS,
    SITE_THIN_CONTENT_RULE_IDS,
    validate_rule_id,
)
from app.core.config.projects import PROMPT_INTENTS
from app.core.config.site_health import SITE_HEALTH_RULES_BY_ID


def test_rule_ids_are_unique() -> None:
    rule_ids = [rule.rule_id for rule in OPPORTUNITY_RULES]
    assert len(rule_ids) == len(set(rule_ids))
    assert set(rule_ids) == set(OPPORTUNITY_RULES_BY_ID)


def test_catalog_uses_known_types_and_severities() -> None:
    assert OPPORTUNITY_RULES, "catalog must not be empty"
    for rule in OPPORTUNITY_RULES:
        assert rule.opportunity_type in OPPORTUNITY_TYPES
        assert rule.severity in OPPORTUNITY_SEVERITIES
        assert rule.title.strip()
        assert rule.remediation.strip()


def test_v1_enabled_rule_set() -> None:
    enabled = {rule.rule_id for rule in OPPORTUNITY_RULES if rule.enabled}
    assert enabled == {
        "brand_absent_high_value_prompt",
        "owned_page_not_cited",
        "missing_structured_data",
        "thin_content",
    }


def test_deferred_rules_ship_disabled() -> None:
    disabled = {rule.rule_id for rule in OPPORTUNITY_RULES if not rule.enabled}
    assert disabled == {"low_share_of_voice_theme", "high_traffic_low_visibility"}


def test_vocabulary_frozensets_non_empty() -> None:
    assert OPPORTUNITY_TYPES
    assert OPPORTUNITY_SEVERITIES
    assert OPPORTUNITY_STATUSES
    assert OPPORTUNITY_ACTIVE_STATUSES
    assert OPPORTUNITY_ACTIVE_STATUSES <= OPPORTUNITY_STATUSES
    assert OPPORTUNITY_TYPES == {"visibility", "site", "traffic", "topic"}
    assert OPPORTUNITY_SEVERITIES == {
        "critical",
        "high",
        "medium",
        "low",
        "info",
    }
    assert OPPORTUNITY_STATUSES == {
        "open",
        "in_progress",
        "dismissed",
        "resolved",
    }


def test_severity_weights_cover_vocabulary_and_are_positive() -> None:
    assert set(SEVERITY_WEIGHTS) == set(OPPORTUNITY_SEVERITIES)
    assert all(weight > 0 for weight in SEVERITY_WEIGHTS.values())


def test_intent_value_weights_cover_prompt_intents() -> None:
    assert set(INTENT_VALUE_WEIGHTS) == set(PROMPT_INTENTS)
    assert all(weight > 0 for weight in INTENT_VALUE_WEIGHTS.values())
    assert INTENT_VALUE_DEFAULT > 0


def test_scoring_constants_positive() -> None:
    assert PRIORITY_SCALE > 0
    assert MIN_PRIORITY_TO_SURFACE > 0
    assert GAP_COMPETITOR_WEIGHT > 0
    assert GAP_COMPETITOR_CAP >= 1
    assert GAP_OWNED_CITATION_WEIGHT > 0
    # Every enabled rule at base factors must clear the write-time floor,
    # otherwise the rule could never produce a persisted row.
    lowest_enabled_weight = min(
        SEVERITY_WEIGHTS[rule.severity] for rule in OPPORTUNITY_RULES if rule.enabled
    )
    assert lowest_enabled_weight * INTENT_VALUE_DEFAULT * PRIORITY_SCALE >= (
        MIN_PRIORITY_TO_SURFACE
    )


def test_bounds_positive() -> None:
    assert RECOMPUTE_MAX_ANALYSES > 0
    assert RECOMPUTE_MAX_ISSUES > 0
    assert 0 < LIST_DEFAULT_LIMIT <= LIST_MAX_LIMIT
    assert MAX_EXPORT_ITEMS > 0


def test_validate_rule_id_accepts_catalog_ids() -> None:
    for rule in OPPORTUNITY_RULES:
        assert validate_rule_id(rule.rule_id) == rule.rule_id


def test_validate_rule_id_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown opportunity rule_id"):
        validate_rule_id("no_such_rule")


def test_site_mapping_sets_reference_real_site_health_rules() -> None:
    assert SITE_STRUCTURED_DATA_RULE_IDS
    assert SITE_THIN_CONTENT_RULE_IDS
    assert SITE_STRUCTURED_DATA_RULE_IDS.isdisjoint(SITE_THIN_CONTENT_RULE_IDS)
    for rule_id in SITE_STRUCTURED_DATA_RULE_IDS | SITE_THIN_CONTENT_RULE_IDS:
        assert rule_id in SITE_HEALTH_RULES_BY_ID
