"""Traffic + LLM Analytics config (A1): projection knobs, provenance
versions, C1 dataset-id consumption, deterministic rule-table integrity,
sanitization allowlists, and env-injected secrets/settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import _INSECURE_DEFAULTS, Settings
from app.core.config.analytics import (
    AI_REFERRAL_HOST_RULES,
    AI_REFERRAL_RULE_VERSION,
    AI_REFERRAL_UA_RULES,
    AI_REFERRAL_UTM_RULES,
    AI_SOURCE_OTHER,
    AI_SOURCE_TO_LOGICAL_ENGINE,
    AI_SOURCES,
    ANALYTICS_DEFAULT_GRANULARITY,
    ANALYTICS_MAX_WINDOW_DAYS,
    ANALYTICS_SNAPSHOT_GRANULARITIES,
    ANALYTICS_SNAPSHOT_TTL_S,
    CONFIDENCE_BUCKETS,
    CORRELATION_MIN_SAMPLE,
    MATCH_SIGNALS,
    REFERRAL_RAW_ALLOWLIST,
    REFERRAL_RETENTION_DAYS,
    REFERRAL_SANITIZE_VERSION,
    REFERRAL_SESSION_HASH_HEX_LENGTH,
    REFERRAL_URL_PARAM_ALLOWLIST,
    REFERRAL_URL_PARAM_ALLOWLIST_PREFIXES,
    AnalyticsSettings,
)
from app.core.config.integrations import INTEGRATION_DATASET_TEMPLATES
from app.core.config.provider_catalog import LOGICAL_ENGINES
from app.core.config.traffic import (
    TRAFFIC_DEFAULT_WINDOW_DAYS,
    TRAFFIC_FORMULA_VERSION,
    TRAFFIC_GA4_ORGANIC_CHANNEL_GROUPS,
    TRAFFIC_GA4_REFERRAL_DATASETS,
    TRAFFIC_MAX_WINDOW_DAYS,
    TRAFFIC_NORMALIZATION_VERSION,
    TRAFFIC_PAGE_SORT_WHITELIST,
    TRAFFIC_QUERY_SORT_WHITELIST,
    TRAFFIC_SNAPSHOT_GRANULARITIES,
)


def test_traffic_window_and_granularity_knobs() -> None:
    assert 0 < TRAFFIC_DEFAULT_WINDOW_DAYS <= TRAFFIC_MAX_WINDOW_DAYS
    assert TRAFFIC_SNAPSHOT_GRANULARITIES == frozenset({"day", "week", "month"})


def test_traffic_provenance_versions_are_distinct() -> None:
    # Formula and normalization versions stay SEPARATE (traffic.md section 8).
    assert TRAFFIC_FORMULA_VERSION
    assert TRAFFIC_NORMALIZATION_VERSION
    assert TRAFFIC_FORMULA_VERSION != TRAFFIC_NORMALIZATION_VERSION


def test_traffic_ga4_inclusion_vocabularies() -> None:
    assert TRAFFIC_GA4_ORGANIC_CHANNEL_GROUPS == frozenset({"Organic Search"})
    # The consumed C1 referral-dimension dataset ids (referral ingest reads
    # exactly these two) — and they must be ids the integrations config
    # actually owns (no drift from the C1 owner).
    assert TRAFFIC_GA4_REFERRAL_DATASETS == frozenset(
        {"ga4_referrer_daily", "ga4_source_medium_daily"}
    )
    assert TRAFFIC_GA4_REFERRAL_DATASETS <= set(INTEGRATION_DATASET_TEMPLATES)


def test_traffic_sort_whitelists() -> None:
    assert TRAFFIC_PAGE_SORT_WHITELIST == frozenset(
        {"impressions", "clicks", "ctr", "position", "sessions", "conversions"}
    )
    assert TRAFFIC_QUERY_SORT_WHITELIST == frozenset(
        {"impressions", "clicks", "ctr", "position"}
    )
    # Query stats carry no GA4 session aggregates — never sortable by them.
    assert "sessions" not in TRAFFIC_QUERY_SORT_WHITELIST
    assert "conversions" not in TRAFFIC_QUERY_SORT_WHITELIST


def test_rule_and_sanitize_versions_stamped() -> None:
    assert AI_REFERRAL_RULE_VERSION
    assert REFERRAL_SANITIZE_VERSION
    assert AI_REFERRAL_RULE_VERSION != REFERRAL_SANITIZE_VERSION


def test_ai_source_vocabulary_and_logical_engine_mapping() -> None:
    # Matches the frontend aiSourceSchema contract exactly.
    assert AI_SOURCES == frozenset(
        {
            "chatgpt",
            "gemini",
            "claude",
            "perplexity",
            "copilot",
            "google_ai_overview",
            "other",
        }
    )
    # Only the audited three map onto logical engines (invariant 10).
    assert AI_SOURCE_TO_LOGICAL_ENGINE == {
        "chatgpt": "chatgpt",
        "gemini": "gemini",
        "claude": "claude",
    }
    assert set(AI_SOURCE_TO_LOGICAL_ENGINE.values()) <= LOGICAL_ENGINES
    assert AI_SOURCE_OTHER not in AI_SOURCE_TO_LOGICAL_ENGINE


def test_match_signal_and_confidence_vocabularies() -> None:
    # Matches the frontend referralMatchSignal/Confidence contracts.
    assert MATCH_SIGNALS == frozenset({"referrer", "utm", "user_agent"})
    assert CONFIDENCE_BUCKETS == frozenset({"exact", "heuristic"})


def test_rule_tables_are_well_formed() -> None:
    all_rule_ids: list[str] = []
    for rule in AI_REFERRAL_HOST_RULES:
        all_rule_ids.append(rule.rule_id)
        assert rule.ai_source in AI_SOURCES - {AI_SOURCE_OTHER}
        assert rule.confidence in CONFIDENCE_BUCKETS
        # Bare normalized hosts only (suffix-safe matching needs no scheme).
        assert rule.host == rule.host.strip().casefold()
        assert "://" not in rule.host and "/" not in rule.host
        assert not rule.host.startswith("www.")
    for rule in AI_REFERRAL_UTM_RULES:
        all_rule_ids.append(rule.rule_id)
        assert rule.ai_source in AI_SOURCES - {AI_SOURCE_OTHER}
        assert rule.confidence in CONFIDENCE_BUCKETS
        assert rule.utm_source is not None or rule.utm_medium is not None
        for constraint in (rule.utm_source, rule.utm_medium):
            if constraint is not None:
                # Literals are pre-normalized so matching is a plain equality.
                assert constraint == constraint.strip().casefold()
    for rule in AI_REFERRAL_UA_RULES:
        all_rule_ids.append(rule.rule_id)
        assert rule.ai_source in AI_SOURCES - {AI_SOURCE_OTHER}
        assert rule.confidence in CONFIDENCE_BUCKETS
        assert rule.substring and rule.substring == rule.substring.casefold()
    # Rule ids are unique and stable (they persist as matched_rule_id).
    assert len(all_rule_ids) == len(set(all_rule_ids))


def test_every_ai_source_except_other_has_a_rule() -> None:
    covered = (
        {rule.ai_source for rule in AI_REFERRAL_HOST_RULES}
        | {rule.ai_source for rule in AI_REFERRAL_UTM_RULES}
        | {rule.ai_source for rule in AI_REFERRAL_UA_RULES}
    )
    # google_ai_overview has no host rule (AIO clicks arrive via google.com);
    # it is covered by its UTM rule instead.
    assert covered == AI_SOURCES - {AI_SOURCE_OTHER}


def test_analytics_snapshot_knobs() -> None:
    assert ANALYTICS_SNAPSHOT_GRANULARITIES == TRAFFIC_SNAPSHOT_GRANULARITIES
    assert ANALYTICS_DEFAULT_GRANULARITY in ANALYTICS_SNAPSHOT_GRANULARITIES
    assert ANALYTICS_MAX_WINDOW_DAYS > 0
    assert ANALYTICS_SNAPSHOT_TTL_S > 0
    # Pearson needs at least two aligned buckets; never report below this.
    assert CORRELATION_MIN_SAMPLE >= 2


def test_referral_sanitization_allowlists() -> None:
    assert "ref" in REFERRAL_URL_PARAM_ALLOWLIST
    assert "utm_" in REFERRAL_URL_PARAM_ALLOWLIST_PREFIXES
    assert REFERRAL_RAW_ALLOWLIST
    # Privacy guard: network/device identifiers must NEVER be allowlisted
    # into the persisted raw payload (invariant 6).
    forbidden = {"ip", "client_ip", "ip_address", "device_id", "session_id", "email"}
    assert REFERRAL_RAW_ALLOWLIST.isdisjoint(forbidden)
    # The session hash is a truncated sha256 hex (64 chars at most).
    assert 16 <= REFERRAL_SESSION_HASH_HEX_LENGTH <= 64
    assert REFERRAL_RETENTION_DAYS > 0


def test_analytics_settings_lease_ttl_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh = AnalyticsSettings(_env_file=None)
    assert fresh.lease_ttl_seconds > 0
    monkeypatch.setenv("ANALYTICS_LEASE_TTL_SECONDS", "45")
    configured = AnalyticsSettings(_env_file=None)
    assert configured.lease_ttl_seconds == 45
    monkeypatch.setenv("ANALYTICS_LEASE_TTL_SECONDS", "0")
    with pytest.raises(ValidationError):
        AnalyticsSettings(_env_file=None)


def test_referral_hash_salt_env_injected_with_insecure_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A developer's real salt must not leak into this test.
    monkeypatch.delenv("REFERRAL_HASH_SALT", raising=False)
    fresh = Settings(_env_file=None)
    # Ships an insecure placeholder that the startup secret check flags
    # (same pattern as jwt_secret_key / encryption_key).
    assert fresh.referral_hash_salt in _INSECURE_DEFAULTS
    monkeypatch.setenv("REFERRAL_HASH_SALT", "test-salt-value")
    configured = Settings(_env_file=None)
    assert configured.referral_hash_salt == "test-salt-value"
