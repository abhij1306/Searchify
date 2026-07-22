"""OAuth state tokens: signed round-trip, expiry, provider binding."""

from __future__ import annotations

import pytest

from app.core.config.oauth import oauth_settings
from app.core.security import (
    TokenDecodeError,
    create_access_token,
    create_oauth_state,
    decode_oauth_state,
)


def test_oauth_state_roundtrip_returns_claims_with_nonce() -> None:
    token = create_oauth_state("google")
    claims = decode_oauth_state(token, "google")
    assert claims["sub"] == "oauth-state"
    assert claims["provider"] == "google"
    assert claims["nonce"]
    assert claims["exp"] > 0


def test_oauth_state_nonce_is_unique_per_token() -> None:
    first = decode_oauth_state(create_oauth_state("github"), "github")
    second = decode_oauth_state(create_oauth_state("github"), "github")
    assert first["nonce"] != second["nonce"]


def test_expired_oauth_state_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Negative TTL mints an already-expired state token.
    monkeypatch.setattr(oauth_settings, "state_ttl_seconds", -10)
    token = create_oauth_state("google")
    with pytest.raises(TokenDecodeError):
        decode_oauth_state(token, "google")


def test_oauth_state_wrong_provider_rejected() -> None:
    token = create_oauth_state("google")
    with pytest.raises(TokenDecodeError):
        decode_oauth_state(token, "github")


def test_oauth_state_garbage_token_rejected() -> None:
    with pytest.raises(TokenDecodeError):
        decode_oauth_state("not-a-jwt", "google")


def test_access_token_is_not_valid_oauth_state() -> None:
    # A session access token must never double as an OAuth state token.
    token = create_access_token("user-uuid")
    with pytest.raises(TokenDecodeError):
        decode_oauth_state(token, "google")
