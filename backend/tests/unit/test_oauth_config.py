"""OAuth provider catalog + settings: defaults, flags, configured matrix."""

from __future__ import annotations

import pytest

from app.core.config.oauth import (
    OAUTH_APPLE,
    OAUTH_AUTHORIZE_URLS,
    OAUTH_GITHUB,
    OAUTH_GOOGLE,
    OAUTH_PROVIDER_LABELS,
    OAUTH_PROVIDERS,
    OAUTH_SCOPES,
    OAUTH_TOKEN_URLS,
    OAuthSettings,
    is_oauth_provider,
    oauth_provider_configured,
    oauth_settings,
)

_PROVIDERS = (OAUTH_GOOGLE, OAUTH_GITHUB, OAUTH_APPLE)
_FIELDS = ("client_id", "client_secret", "redirect_uri", "enabled")


@pytest.fixture
def _clean_oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate defaults tests from any developer OAUTH_* process env."""
    for provider in _PROVIDERS:
        for field in _FIELDS:
            monkeypatch.delenv(f"OAUTH_{provider}_{field}".upper(), raising=False)
    monkeypatch.delenv("OAUTH_STATE_TTL_SECONDS", raising=False)


def test_provider_catalog_covers_google_github_apple() -> None:
    assert OAUTH_PROVIDERS == frozenset({"google", "github", "apple"})
    # Labels + endpoint/scope defaults cover exactly the catalog.
    assert set(OAUTH_PROVIDER_LABELS) == set(OAUTH_PROVIDERS)
    assert set(OAUTH_AUTHORIZE_URLS) == set(OAUTH_PROVIDERS)
    assert set(OAUTH_TOKEN_URLS) == set(OAUTH_PROVIDERS)
    assert set(OAUTH_SCOPES) == set(OAUTH_PROVIDERS)
    for provider in OAUTH_PROVIDERS:
        assert OAUTH_PROVIDER_LABELS[provider]
        assert OAUTH_AUTHORIZE_URLS[provider].startswith("https://")
        assert OAUTH_TOKEN_URLS[provider].startswith("https://")
        assert OAUTH_SCOPES[provider]


def test_defaults_are_empty_and_disabled(_clean_oauth_env: None) -> None:
    fresh = OAuthSettings()
    for provider in _PROVIDERS:
        assert getattr(fresh, f"{provider}_client_id") == ""
        assert getattr(fresh, f"{provider}_client_secret") == ""
        assert getattr(fresh, f"{provider}_redirect_uri") == ""
        assert getattr(fresh, f"{provider}_enabled") is False
    assert fresh.state_ttl_seconds == 600


def test_is_oauth_provider() -> None:
    for provider in _PROVIDERS:
        assert is_oauth_provider(provider) is True
    assert is_oauth_provider("gitlab") is False
    assert is_oauth_provider("") is False


def test_oauth_provider_configured_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    # Enabled without credentials -> not configured.
    monkeypatch.setattr(oauth_settings, "github_enabled", True)
    monkeypatch.setattr(oauth_settings, "github_client_id", "")
    monkeypatch.setattr(oauth_settings, "github_client_secret", "")
    monkeypatch.setattr(oauth_settings, "github_redirect_uri", "")
    assert oauth_provider_configured("github") is False

    # Full config + enabled -> configured.
    monkeypatch.setattr(oauth_settings, "github_client_id", "id")
    monkeypatch.setattr(oauth_settings, "github_client_secret", "secret")
    monkeypatch.setattr(oauth_settings, "github_redirect_uri", "https://x.test/cb")
    assert oauth_provider_configured("github") is True

    # Dropping any single value flips it back to not configured.
    for field in ("client_id", "client_secret", "redirect_uri"):
        monkeypatch.setattr(oauth_settings, f"github_{field}", "")
        assert oauth_provider_configured("github") is False
        monkeypatch.setattr(oauth_settings, f"github_{field}", "restored")

    # Credentialed but not enabled -> not configured.
    monkeypatch.setattr(oauth_settings, "github_enabled", False)
    assert oauth_provider_configured("github") is False


def test_oauth_provider_configured_unknown_provider() -> None:
    assert oauth_provider_configured("gitlab") is False
