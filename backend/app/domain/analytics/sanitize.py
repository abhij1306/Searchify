"""Deterministic referral sanitization (invariant 6 privacy, invariant 9).

PURE, versioned redaction applied BEFORE the immutable ``ReferralEvent``
write (llm-analytics.md section 3): persisted referral data must never carry
PII, credentials, secrets, or raw device/network identifiers. The redaction
pass itself is versioned by ``REFERRAL_SANITIZE_VERSION`` — stamped by the
CALLER onto each event, not here.

Contract:
- ``raw`` is an allowlisted, redacted payload — only config-allowlisted keys
  survive; everything else is dropped.
- URLs keep only allowlisted non-PII marketing query params (``utm_*``,
  ``ref``); fragments and embedded credentials (``user:pass@``) are dropped.
- ``user_agent`` is reduced to a coarse family token; full fingerprintable
  UA strings (OS, versions, device ids) are never persisted.
- Raw IPs / device ids are never persisted; a session is represented only by
  the opaque, salted, truncated HMAC-SHA256 ``session_id_hash`` (the raw id
  is used transiently for hashing and discarded).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Host derivation reuses the analysis normalizer (invariant 2).
from app.analysis.normalization import normalize_domain
from app.core.config import settings
from app.core.config.analytics import (
    REFERRAL_RAW_ALLOWLIST,
    REFERRAL_SESSION_HASH_HEX_LENGTH,
    REFERRAL_URL_PARAM_ALLOWLIST,
    REFERRAL_URL_PARAM_ALLOWLIST_PREFIXES,
)


def _url_param_allowed(name: str) -> bool:
    return name in REFERRAL_URL_PARAM_ALLOWLIST or any(
        name.startswith(prefix) for prefix in REFERRAL_URL_PARAM_ALLOWLIST_PREFIXES
    )


def sanitize_referral_url(url: str | None) -> str:
    """Strip a landing/referrer URL to its persistable, PII-free form.

    Keeps scheme/host/port/path plus allowlisted marketing params only;
    drops the fragment, any embedded credentials (``user:pass@``), and every
    non-allowlisted query param. Relative URLs (e.g. a GA4 ``landingPage``
    path) are supported — they simply carry no scheme/host.
    """
    text = (url or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.netloc:
        # Rebuild the authority from hostname+port ONLY — userinfo never
        # survives. Host is lowercased; a malformed port is dropped.
        host = (parts.hostname or "").lower()
        try:
            port = parts.port
        except ValueError:
            port = None
        netloc = f"{host}:{port}" if port else host
    else:
        netloc = ""
    kept_params = [
        (name.casefold(), value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if _url_param_allowed(name.casefold())
    ]
    query = urlencode(kept_params)
    # Fragment is always dropped (the trailing "").
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, query, ""))


def user_agent_family_token(user_agent: str | None) -> str:
    """Reduce a UA string to a coarse, non-fingerprintable family token.

    The family token is the first product token with its version stripped
    (``"Mozilla/5.0 (Macintosh; ...)"`` -> ``"mozilla"``,
    ``"ChatGPT-User/1.0"`` -> ``"chatgpt-user"``). OS/build/device
    identifiers in the parenthesized comment never persist.
    """
    text = (user_agent or "").strip()
    if not text:
        return ""
    return text.split()[0].split("/", 1)[0].casefold()


def hash_session_id(raw_session_id: str | None) -> str:
    """Opaque salted session identity — the ONLY persisted session marker.

    Truncated HMAC-SHA256 hex of the raw session id keyed with
    ``settings.referral_hash_salt``; deterministic for a given (salt, id) so
    events of one session link, but irreversible without the deployment
    secret. The raw id (often derived from a client IP/device id) is used
    transiently here and discarded by the caller.
    """
    raw = (raw_session_id or "").strip()
    if not raw:
        return ""
    digest = hmac.new(
        settings.referral_hash_salt.encode("utf-8"),
        raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:REFERRAL_SESSION_HASH_HEX_LENGTH]


def sanitize_raw_payload(raw: Mapping[str, object] | None) -> dict[str, object]:
    """Allowlist-filter the source payload for the persisted ``raw`` column.

    Only keys on ``REFERRAL_RAW_ALLOWLIST`` survive — raw IPs, device ids,
    emails, and arbitrary provider fields are dropped. Non-scalar values are
    stringified so the result stays JSONB-safe.
    """
    if not raw:
        return {}
    sanitized: dict[str, object] = {}
    for key, value in raw.items():
        if key not in REFERRAL_RAW_ALLOWLIST:
            continue
        if value is None or isinstance(value, str | int | float | bool):
            sanitized[key] = value
        else:
            sanitized[key] = str(value)
    return sanitized


@dataclass(frozen=True)
class SanitizedReferral:
    """The fully sanitized, persistable view of one referral's signals."""

    landing_url: str
    referrer_url: str
    referrer_host: str
    user_agent: str  # family token only
    session_id_hash: str
    raw: dict[str, object]


def sanitize_referral(
    *,
    landing_url: str | None,
    referrer_url: str | None,
    user_agent: str | None,
    session_id: str | None,
    raw: Mapping[str, object] | None,
) -> SanitizedReferral:
    """Run the full pre-write redaction pass over one referral's signals."""
    sanitized_referrer_url = sanitize_referral_url(referrer_url)
    return SanitizedReferral(
        landing_url=sanitize_referral_url(landing_url),
        referrer_url=sanitized_referrer_url,
        referrer_host=normalize_domain(sanitized_referrer_url),
        user_agent=user_agent_family_token(user_agent),
        session_id_hash=hash_session_id(session_id),
        raw=sanitize_raw_payload(raw),
    )
