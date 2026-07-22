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
    token, session_nonce = create_oauth_state("google")
    claims = decode_oauth_state(token, "google", session_nonce)
    assert claims["sub"] == "oauth-state"
    assert claims["provider"] == "google"
    assert claims["nonce"]
    assert claims["session_nonce"] == session_nonce
    assert claims["exp"] > 0


def test_oauth_state_nonce_is_unique_per_token() -> None:
    token1, nonce1 = create_oauth_state("github")
    token2, nonce2 = create_oauth_state("github")
    first = decode_oauth_state(token1, "github", nonce1)
    second = decode_oauth_state(token2, "github", nonce2)
    assert first["nonce"] != second["nonce"]
    assert first["session_nonce"] != second["session_nonce"]


def test_expired_oauth_state_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Negative TTL mints an already-expired state token.
    monkeypatch.setattr(oauth_settings, "state_ttl_seconds", -10)
    token, session_nonce = create_oauth_state("google")
    with pytest.raises(TokenDecodeError):
        decode_oauth_state(token, "google", session_nonce)


def test_oauth_state_wrong_provider_rejected() -> None:
    token, session_nonce = create_oauth_state("google")
    with pytest.raises(TokenDecodeError):
        decode_oauth_state(token, "github", session_nonce)


def test_oauth_state_wrong_session_nonce_rejected() -> None:
    token, _ = create_oauth_state("google")
    with pytest.raises(TokenDecodeError, match="OAuth session nonce mismatch"):
        decode_oauth_state(token, "google", "invalid-nonce")


def test_oauth_state_missing_session_nonce_rejected() -> None:
    token, _ = create_oauth_state("google")
    with pytest.raises(TokenDecodeError, match="Missing OAuth session nonce"):
        decode_oauth_state(token, "google", "")


def test_oauth_state_garbage_token_rejected() -> None:
    with pytest.raises(TokenDecodeError):
        decode_oauth_state("not-a-jwt", "google", "some-nonce")


def test_access_token_is_not_valid_oauth_state() -> None:
    # A session access token must never double as an OAuth state token.
    token = create_access_token("user-uuid")
    with pytest.raises(TokenDecodeError):
        decode_oauth_state(token, "google", "some-nonce")
