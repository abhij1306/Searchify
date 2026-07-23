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


# --- Integrations connect flow: optional binding claims + optional nonce ----


def test_oauth_state_binding_claims_roundtrip() -> None:
    token, _ = create_oauth_state(
        "gsc",
        workspace_id="ws-uuid-1",
        user_id="user-uuid-1",
        jti="jti-abc123",
    )
    claims = decode_oauth_state(token, "gsc")
    assert claims["provider"] == "gsc"
    assert claims["workspace_id"] == "ws-uuid-1"
    assert claims["user_id"] == "user-uuid-1"
    assert claims["jti"] == "jti-abc123"


def test_oauth_state_without_binding_claims_omits_them() -> None:
    # Auth sign-in callers keep byte-identical claim sets (no new keys).
    token, session_nonce = create_oauth_state("google")
    claims = decode_oauth_state(token, "google", session_nonce)
    assert "workspace_id" not in claims
    assert "user_id" not in claims
    assert "jti" not in claims


def test_oauth_state_partial_binding_claims_only_include_passed() -> None:
    token, _ = create_oauth_state("ga4", jti="jti-only")
    claims = decode_oauth_state(token, "ga4")
    assert claims["jti"] == "jti-only"
    assert "workspace_id" not in claims
    assert "user_id" not in claims


def test_decode_without_session_nonce_skips_nonce_comparison() -> None:
    # Integration states skip the cookie-nonce binding (session_nonce=None).
    token, _ = create_oauth_state("bing")
    claims = decode_oauth_state(token, "bing")
    assert claims["sub"] == "oauth-state"
    assert claims["session_nonce"]


def test_decode_without_session_nonce_still_rejects_wrong_provider() -> None:
    token, _ = create_oauth_state("gsc")
    with pytest.raises(TokenDecodeError, match="OAuth state provider mismatch"):
        decode_oauth_state(token, "ga4")


def test_decode_without_session_nonce_still_rejects_garbage() -> None:
    with pytest.raises(TokenDecodeError):
        decode_oauth_state("not-a-jwt", "gsc")


def test_decode_without_session_nonce_still_rejects_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(oauth_settings, "state_ttl_seconds", -10)
    token, _ = create_oauth_state("gsc", jti="jti-exp")
    with pytest.raises(TokenDecodeError):
        decode_oauth_state(token, "gsc")
