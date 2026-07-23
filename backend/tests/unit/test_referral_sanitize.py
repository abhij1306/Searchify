"""Deterministic referral sanitizer (A4): URL param/fragment/userinfo
stripping, raw-payload allowlisting, UA family reduction, and the salted
truncated HMAC session hash — no PII survives the pre-write redaction pass
(invariant 6). Pure — no DB, no network."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from app.core.config import settings
from app.core.config.analytics import (
    REFERRAL_RAW_ALLOWLIST,
    REFERRAL_SESSION_HASH_HEX_LENGTH,
)
from app.domain.analytics.sanitize import (
    hash_session_id,
    sanitize_raw_payload,
    sanitize_referral,
    sanitize_referral_url,
    user_agent_family_token,
)


def test_url_strips_to_marketing_param_allowlist() -> None:
    url = (
        "HTTPS://Example.COM:8443/landing/page"
        "?utm_source=ChatGPT&utm_medium=referral&ref=nav"
        "&token=secret123&fbclid=abc&email=user@example.com#section-2"
    )
    sanitized = sanitize_referral_url(url)
    # Scheme/host lowercased, port + path kept, fragment dropped.
    assert sanitized.startswith("https://example.com:8443/landing/page?")
    assert "#" not in sanitized
    # Only utm_*/ref params survive (names casefolded, order preserved).
    assert "utm_source=ChatGPT" in sanitized
    assert "utm_medium=referral" in sanitized
    assert "ref=nav" in sanitized
    # PII / tracker params are gone entirely.
    for leaked in ("token", "secret123", "fbclid", "abc", "email", "user@example.com"):
        assert leaked not in sanitized


def test_url_drops_embedded_credentials() -> None:
    sanitized = sanitize_referral_url(
        "https://user:pass@example.com/path?utm_campaign=spring"
    )
    assert "user" not in sanitized.split("://", 1)[0]
    assert "pass" not in sanitized
    assert "@" not in sanitized
    assert sanitized == "https://example.com/path?utm_campaign=spring"


def test_url_relative_landing_page_supported() -> None:
    # GA4 landingPage dimensions arrive as bare paths.
    assert (
        sanitize_referral_url("/pricing?utm_source=chatgpt&session=abc#top")
        == "/pricing?utm_source=chatgpt"
    )
    assert sanitize_referral_url("/plain") == "/plain"


def test_url_empty_inputs() -> None:
    assert sanitize_referral_url(None) == ""
    assert sanitize_referral_url("   ") == ""


def test_user_agent_reduced_to_family_token() -> None:
    assert (
        user_agent_family_token(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148"
        )
        == "mozilla"
    )
    assert user_agent_family_token("ChatGPT-User/1.0") == "chatgpt-user"
    assert user_agent_family_token("python-requests/2.31.0") == "python-requests"
    # No OS/build/device fragments survive.
    assert user_agent_family_token(None) == ""
    assert user_agent_family_token("  ") == ""


def test_session_hash_is_deterministic_salted_and_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "referral_hash_salt", "unit-test-salt")
    expected = hmac.new(
        b"unit-test-salt", b"session-42", hashlib.sha256
    ).hexdigest()[:REFERRAL_SESSION_HASH_HEX_LENGTH]
    assert hash_session_id("session-42") == expected
    # Deterministic for the same (salt, raw id)...
    assert hash_session_id("session-42") == hash_session_id("session-42")
    # ...truncated, never the full digest...
    assert len(hash_session_id("session-42")) == REFERRAL_SESSION_HASH_HEX_LENGTH
    # ...keyed by the deployment salt...
    monkeypatch.setattr(settings, "referral_hash_salt", "another-salt")
    assert hash_session_id("session-42") != expected
    # ...and never echoes the raw id.
    assert "session-42" not in hash_session_id("session-42")
    # Absent session id -> empty marker, not a hash of an empty string.
    assert hash_session_id("") == ""
    assert hash_session_id(None) == ""


def test_raw_payload_allowlist_drops_everything_else() -> None:
    raw = {
        "utm_source": "chatgpt.com",
        "utm_medium": "referral",
        "dataset": "ga4_referrer_daily",
        "ip": "203.0.113.9",
        "client_ip": "203.0.113.9",
        "device_id": "EA7583CD-A667-48BC-B806-42ECB2B48606",
        "email": "user@example.com",
        "headers": {"user-agent": "full ua string"},
        "freeform": "anything",
    }
    sanitized = sanitize_raw_payload(raw)
    assert sanitized == {
        "utm_source": "chatgpt.com",
        "utm_medium": "referral",
        "dataset": "ga4_referrer_daily",
    }
    for key in ("ip", "client_ip", "device_id", "email", "headers", "freeform"):
        assert key not in sanitized
    # Every surviving key is on the config allowlist by construction.
    assert set(sanitized) <= REFERRAL_RAW_ALLOWLIST
    assert sanitize_raw_payload(None) == {}
    assert sanitize_raw_payload({}) == {}


def test_raw_payload_stringifies_non_scalars() -> None:
    sanitized = sanitize_raw_payload({"utm_term": ["a", "b"], "ref": 7})
    assert sanitized["utm_term"] == "['a', 'b']"
    assert sanitized["ref"] == 7


def test_sanitize_referral_end_to_end_no_pii_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "referral_hash_salt", "e2e-salt")
    result = sanitize_referral(
        landing_url="https://example.com/lp?utm_source=chatgpt&sid=raw-session#x",
        referrer_url="https://user:pw@www.ChatGPT.com/share/abc?utm_medium=ai&t=1#f",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ChatGPT-User/1.0",
        session_id="203.0.113.9|EA7583CD-device",
        raw={
            "referrer_host": "chatgpt.com",
            "ip": "203.0.113.9",
            "device_id": "EA7583CD-device",
        },
    )
    assert result.landing_url == "https://example.com/lp?utm_source=chatgpt"
    assert result.referrer_url == "https://www.chatgpt.com/share/abc?utm_medium=ai"
    # referrer_host derives from the SANITIZED referrer URL (www stripped).
    assert result.referrer_host == "chatgpt.com"
    assert result.user_agent == "mozilla"
    assert len(result.session_id_hash) == REFERRAL_SESSION_HASH_HEX_LENGTH
    assert result.raw == {"referrer_host": "chatgpt.com"}
    # The raw IP/device id appears NOWHERE in the sanitized view.
    persisted = (
        result.landing_url
        + result.referrer_url
        + result.referrer_host
        + result.user_agent
        + result.session_id_hash
        + repr(sorted(result.raw.items()))
    )
    assert "203.0.113.9" not in persisted
    assert "EA7583CD" not in persisted
    assert "pw" not in persisted
