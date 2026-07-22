"""Component tests for the OAuth scaffold API (httpx ASGITransport).

Covers the Phase B acceptance:
  - the providers endpoint lists google/github/apple with ``configured``
    flags only — no secret-shaped fields (invariant 6);
  - unconfigured providers -> 503 structured detail on start + callback;
  - unknown providers -> 404;
  - a configured provider's start builds an authorize URL carrying the
    client id, signed state, and encoded redirect URI; its callback is a
    deliberate 501 stub.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from app.core.config.oauth import oauth_settings
from app.core.security import decode_oauth_state

_BASE = "/api/v1/auth/oauth"
_PROVIDERS = ("google", "github", "apple")


def _disable_provider(monkeypatch: pytest.MonkeyPatch, provider: str) -> None:
    monkeypatch.setattr(oauth_settings, f"{provider}_enabled", False)
    monkeypatch.setattr(oauth_settings, f"{provider}_client_id", "")
    monkeypatch.setattr(oauth_settings, f"{provider}_client_secret", "")
    monkeypatch.setattr(oauth_settings, f"{provider}_redirect_uri", "")


@pytest.fixture
def _all_providers_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin every provider unconfigured regardless of process env."""
    for provider in _PROVIDERS:
        _disable_provider(monkeypatch, provider)


def _configure_google(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    values = {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "redirect_uri": "https://app.example.com/auth/oauth/google/callback",
    }
    monkeypatch.setattr(oauth_settings, "google_enabled", True)
    for field, value in values.items():
        monkeypatch.setattr(oauth_settings, f"google_{field}", value)
    return values


@pytest.mark.asyncio
async def test_providers_lists_catalog_with_flags_only(
    client: httpx.AsyncClient,
    _all_providers_unconfigured: None,
) -> None:
    resp = await client.get(f"{_BASE}/providers")
    assert resp.status_code == 200
    providers = resp.json()["providers"]
    assert {p["provider"] for p in providers} == set(_PROVIDERS)
    for info in providers:
        # Label + configured flag only — no secret-shaped fields (invariant 6).
        assert set(info) == {"provider", "label", "configured"}
        assert info["label"]
        assert info["configured"] is False
    # The raw payload carries no credential material.
    assert "secret" not in resp.text
    assert "client_id" not in resp.text
    assert "redirect_uri" not in resp.text


@pytest.mark.asyncio
async def test_start_unconfigured_providers_return_503(
    client: httpx.AsyncClient,
    _all_providers_unconfigured: None,
) -> None:
    for provider in _PROVIDERS:
        resp = await client.get(f"{_BASE}/{provider}/start")
        assert resp.status_code == 503
        assert resp.json()["detail"] == {
            "code": "oauth_provider_not_configured",
            "provider": provider,
        }


@pytest.mark.asyncio
async def test_callback_unconfigured_providers_return_503(
    client: httpx.AsyncClient,
    _all_providers_unconfigured: None,
) -> None:
    for provider in _PROVIDERS:
        for method in (client.get, client.post):
            resp = await method(f"{_BASE}/{provider}/callback")
            assert resp.status_code == 503
            assert resp.json()["detail"] == {
                "code": "oauth_provider_not_configured",
                "provider": provider,
            }


@pytest.mark.asyncio
async def test_unknown_provider_returns_404(client: httpx.AsyncClient) -> None:
    for path in ("start", "callback"):
        resp = await client.get(f"{_BASE}/gitlab/{path}")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_start_configured_provider_builds_authorize_url(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _configure_google(monkeypatch)
    resp = await client.get(f"{_BASE}/google/start")
    assert resp.status_code == 200
    body = resp.json()
    url = body["authorize_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")

    # The URL carries client_id + state + the encoded redirect URI.
    assert f"client_id={values['client_id']}" in url
    assert body["state"] in url
    assert urlencode({"redirect_uri": values["redirect_uri"]}) in url

    query = parse_qs(urlsplit(url).query)
    assert query["client_id"] == [values["client_id"]]
    assert query["redirect_uri"] == [values["redirect_uri"]]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email profile"]
    assert query["state"] == [body["state"]]

    # The client secret never appears in the URL or payload (invariant 6).
    assert values["client_secret"] not in url
    assert values["client_secret"] not in resp.text

    # The returned state is a valid signed token bound to the provider and session.
    claims = decode_oauth_state(body["state"], "google", body["session_nonce"])
    assert claims["provider"] == "google"
    assert claims["sub"] == "oauth-state"
    assert claims["session_nonce"] == body["session_nonce"]


@pytest.mark.asyncio
async def test_callback_configured_provider_returns_501(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_google(monkeypatch)
    for method in (client.get, client.post):
        resp = await method(f"{_BASE}/google/callback")
        assert resp.status_code == 501
        assert resp.json()["detail"] == {
            "code": "oauth_callback_not_implemented",
            "provider": "google",
        }
