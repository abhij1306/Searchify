"""Integrations config: provider/transport vocabulary, OAuth endpoints,
pinned C1 dataset templates + dimension_key packing, sync-knob bounds, and
the queue spec (I1)."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.config.integrations import (
    DATASET_GA4_CHANNEL_DAILY,
    DATASET_GA4_LANDING_DAILY,
    DATASET_GA4_REFERRER_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    DATASET_GSC_PAGE_DAILY,
    DATASET_GSC_QUERY_DAILY,
    GA4_API_BASE_URL,
    GSC_API_BASE_URL,
    INTEGRATION_APPROVED_ENDPOINT_HOSTS,
    INTEGRATION_DATASET_TEMPLATES,
    INTEGRATION_GRANT_STATUSES,
    INTEGRATION_IMPORTER_VERSION,
    INTEGRATION_OAUTH_AUTHORIZE_URLS,
    INTEGRATION_OAUTH_REVOKE_URLS,
    INTEGRATION_OAUTH_SCOPES,
    INTEGRATION_OAUTH_TOKEN_URLS,
    INTEGRATION_PROVIDER_GA4,
    INTEGRATION_PROVIDER_GSC,
    INTEGRATION_PROVIDER_TRANSPORT,
    INTEGRATION_PROVIDERS,
    INTEGRATION_QUEUE_SPEC,
    INTEGRATION_SYNC_KINDS,
    INTEGRATION_TRANSPORT_GOOGLE,
    INTEGRATION_TRANSPORT_MICROSOFT,
    INTEGRATION_TRANSPORTS,
    IntegrationSettings,
    _integration_claim_order,
    pack_dimension_key,
)
from app.models.integrations import IntegrationSyncRun


def test_queue_spec_resolves_sync_run_model() -> None:
    assert INTEGRATION_QUEUE_SPEC.model is IntegrationSyncRun
    assert INTEGRATION_QUEUE_SPEC.lease_ttl() > 0
    assert INTEGRATION_QUEUE_SPEC.max_attempts_error == "max_attempts_exceeded"


def test_claim_order_mirrors_content_priority_fifo_position() -> None:
    order = _integration_claim_order(IntegrationSyncRun)
    assert len(order) == 3
    rendered = [str(clause) for clause in order]
    assert "priority DESC" in rendered[0]
    assert "available_at ASC" in rendered[1]
    assert "randomized_position ASC" in rendered[2]


def test_provider_transport_vocabulary_and_compatibility() -> None:
    assert INTEGRATION_PROVIDERS == frozenset({"gsc", "ga4", "bing"})
    assert INTEGRATION_TRANSPORTS == frozenset({"google_oauth", "microsoft_oauth"})
    # GSC + GA4 share the one Google grant; Bing rides the Microsoft grant.
    assert INTEGRATION_PROVIDER_TRANSPORT == {
        "gsc": INTEGRATION_TRANSPORT_GOOGLE,
        "ga4": INTEGRATION_TRANSPORT_GOOGLE,
        "bing": INTEGRATION_TRANSPORT_MICROSOFT,
    }
    # Every provider maps to a known transport (no orphan vocabulary).
    assert set(INTEGRATION_PROVIDER_TRANSPORT) == INTEGRATION_PROVIDERS


def test_status_and_kind_tokens() -> None:
    assert INTEGRATION_GRANT_STATUSES == frozenset(
        {"connected", "needs_reauth", "pending_revocation", "revoked", "error"}
    )
    assert INTEGRATION_SYNC_KINDS == frozenset(
        {"scheduled", "on_demand", "backfill"}
    )


def test_oauth_endpoints_per_transport_https_and_allow_listed() -> None:
    for urls in (
        INTEGRATION_OAUTH_AUTHORIZE_URLS,
        INTEGRATION_OAUTH_TOKEN_URLS,
        INTEGRATION_OAUTH_REVOKE_URLS,
    ):
        assert set(urls) == INTEGRATION_TRANSPORTS
        for url in urls.values():
            if not url:
                continue
            parts = urlsplit(url)
            assert parts.scheme == "https"
            assert parts.hostname in INTEGRATION_APPROVED_ENDPOINT_HOSTS
    # Google supports remote revoke; Microsoft does not (empty = local-only).
    assert INTEGRATION_OAUTH_REVOKE_URLS[INTEGRATION_TRANSPORT_GOOGLE]
    assert INTEGRATION_OAUTH_REVOKE_URLS[INTEGRATION_TRANSPORT_MICROSOFT] == ""


def test_google_grant_combines_gsc_and_ga4_scopes() -> None:
    google_scopes = INTEGRATION_OAUTH_SCOPES[INTEGRATION_TRANSPORT_GOOGLE]
    assert "https://www.googleapis.com/auth/webmasters.readonly" in google_scopes
    assert "https://www.googleapis.com/auth/analytics.readonly" in google_scopes
    assert len(google_scopes) == 2
    # The Microsoft grant stays refreshable ahead of the I12 scope pinning.
    assert "offline_access" in INTEGRATION_OAUTH_SCOPES[INTEGRATION_TRANSPORT_MICROSOFT]


def test_dataset_templates_match_pinned_c1() -> None:
    expected = {
        DATASET_GSC_PAGE_DAILY: (INTEGRATION_PROVIDER_GSC, ("page", "date")),
        DATASET_GSC_QUERY_DAILY: (INTEGRATION_PROVIDER_GSC, ("query", "date")),
        DATASET_GA4_CHANNEL_DAILY: (
            INTEGRATION_PROVIDER_GA4,
            ("sessionDefaultChannelGroup", "date"),
        ),
        DATASET_GA4_SOURCE_MEDIUM_DAILY: (
            INTEGRATION_PROVIDER_GA4,
            ("sessionSource", "sessionMedium", "date"),
        ),
        DATASET_GA4_REFERRER_DAILY: (
            INTEGRATION_PROVIDER_GA4,
            ("fullReferrer", "date"),
        ),
        DATASET_GA4_LANDING_DAILY: (
            INTEGRATION_PROVIDER_GA4,
            ("landingPage", "sessionSource", "sessionMedium", "date"),
        ),
    }
    assert set(INTEGRATION_DATASET_TEMPLATES) == set(expected)
    for dataset, (provider, dimensions) in expected.items():
        template = INTEGRATION_DATASET_TEMPLATES[dataset]
        assert template.dataset == dataset
        assert template.provider == provider
        assert template.dimensions == dimensions
        if provider == INTEGRATION_PROVIDER_GSC:
            assert template.metrics == ("clicks", "impressions", "ctr", "position")
        else:
            assert template.metrics == ("sessions", "engagedSessions", "conversions")


def test_pack_dimension_key_single_bare_multi_joined_in_order() -> None:
    # Single-dimension rows use the bare value.
    assert pack_dimension_key(["https://example.com/page"]) == (
        "https://example.com/page"
    )
    # Multi-dimension rows join in the declared template order with " | ".
    landing = INTEGRATION_DATASET_TEMPLATES[DATASET_GA4_LANDING_DAILY]
    row = dict(
        zip(landing.dimensions, ["/lp", "google", "organic", "20260723"], strict=True)
    )
    assert pack_dimension_key([row[dim] for dim in landing.dimensions]) == (
        "/lp | google | organic | 20260723"
    )
    source_medium = INTEGRATION_DATASET_TEMPLATES[DATASET_GA4_SOURCE_MEDIUM_DAILY]
    row = dict(
        zip(source_medium.dimensions, ["chatgpt", "referral", "20260723"], strict=True)
    )
    assert pack_dimension_key([row[dim] for dim in source_medium.dimensions]) == (
        "chatgpt | referral | 20260723"
    )


def test_allow_list_covers_provider_api_hosts_and_is_host_only() -> None:
    for host in INTEGRATION_APPROVED_ENDPOINT_HOSTS:
        assert "://" not in host and "/" not in host
    assert urlsplit(GSC_API_BASE_URL).hostname in INTEGRATION_APPROVED_ENDPOINT_HOSTS
    assert urlsplit(GA4_API_BASE_URL).hostname in INTEGRATION_APPROVED_ENDPOINT_HOSTS


def test_importer_version_token() -> None:
    assert INTEGRATION_IMPORTER_VERSION
    assert isinstance(INTEGRATION_IMPORTER_VERSION, str)


def test_settings_env_prefix_and_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTEGRATION_SYNC_PAGE_SIZE", "5000")
    configured = IntegrationSettings(_env_file=None)
    assert configured.sync_page_size == 5000
    assert configured.sync_max_attempts >= 1
    assert configured.lease_ttl_seconds > configured.heartbeat_interval_seconds

    # A heartbeat not strictly below the lease TTL fails at startup.
    monkeypatch.setenv("INTEGRATION_LEASE_TTL_SECONDS", "120")
    monkeypatch.setenv("INTEGRATION_HEARTBEAT_INTERVAL_SECONDS", "120")
    with pytest.raises(ValidationError):
        IntegrationSettings(_env_file=None)

    # A default window wider than the backfill clamp is nonsensical.
    monkeypatch.setenv("INTEGRATION_HEARTBEAT_INTERVAL_SECONDS", "30")
    monkeypatch.setenv("INTEGRATION_SYNC_DEFAULT_WINDOW_DAYS", "999")
    monkeypatch.setenv("INTEGRATION_SYNC_BACKFILL_MAX_DAYS", "30")
    with pytest.raises(ValidationError):
        IntegrationSettings(_env_file=None)


def test_requests_per_minute_per_provider() -> None:
    settings = IntegrationSettings(_env_file=None)
    for provider in INTEGRATION_PROVIDERS:
        assert settings.requests_per_minute(provider) > 0
    with pytest.raises(ValueError, match="unknown integration provider"):
        settings.requests_per_minute("not-a-provider")


def test_settings_oauth_client_fields_env_injected_default_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A developer's real credentials must not leak into this test.
    for var in (
        "INTEGRATION_GOOGLE_CLIENT_ID",
        "INTEGRATION_GOOGLE_CLIENT_SECRET",
        "INTEGRATION_MICROSOFT_CLIENT_ID",
        "INTEGRATION_MICROSOFT_CLIENT_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    fresh = Settings(_env_file=None)
    assert fresh.integration_google_client_id == ""
    assert fresh.integration_google_client_secret == ""
    assert fresh.integration_microsoft_client_id == ""
    assert fresh.integration_microsoft_client_secret == ""

    monkeypatch.setenv("INTEGRATION_GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("INTEGRATION_GOOGLE_CLIENT_SECRET", "gsecret")
    monkeypatch.setenv("INTEGRATION_MICROSOFT_CLIENT_ID", "mid")
    monkeypatch.setenv("INTEGRATION_MICROSOFT_CLIENT_SECRET", "msecret")
    configured = Settings(_env_file=None)
    assert configured.integration_google_client_id == "gid"
    assert configured.integration_google_client_secret == "gsecret"
    assert configured.integration_microsoft_client_id == "mid"
    assert configured.integration_microsoft_client_secret == "msecret"
