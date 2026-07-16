# Stable URL identity + keyset cursor encoding for Site Health (Task 3).
#
# One owner for two deterministic transforms every Site Health path relies on:
#   - ``url_hash`` — the stable per-URL identity used for the unique
#     ``(project_id, url_hash)`` catalog constraint and the task slot key. It is
#     computed from the CANONICAL URL (see ``url_policy.canonicalize``) so the
#     same logical page always hashes to one identity regardless of fragment,
#     default port, tracking params, or query ordering (invariant 9).
#   - opaque ``(normalized_url, id)`` keyset cursor encode/decode used by the
#     progressive inventory API in later tasks; defined here so cursor identity
#     has a single owner.
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid

from app.connectors.web_evidence.url_policy import canonicalize


def url_hash(normalized_url: str) -> str:
    """Return the stable 64-char identity hash for a canonical URL."""
    return hashlib.sha256(
        str(normalized_url).encode("utf-8")
    ).hexdigest()[:64]


def canonical_identity(url: str, *, base_url: str | None = None) -> tuple[str, str]:
    """Return ``(canonical_url, url_hash)`` for a raw/relative URL.

    Raises ``UrlPolicyError`` (from ``canonicalize``) for a URL the policy
    rejects (bad scheme/port/userinfo), so callers never admit an unsafe URL.
    """
    canonical = canonicalize(url, base_url=base_url)
    return canonical, url_hash(canonical)


def encode_cursor(*, normalized_url: str, row_id: uuid.UUID | str) -> str:
    """Encode an opaque base64url keyset cursor from the sort tuple."""
    payload = {"u": str(normalized_url), "i": str(row_id)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """Decode a cursor to ``(normalized_url, row_id)``; raise on tampering."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        return str(payload["u"]), str(payload["i"])
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise ValueError("invalid cursor") from exc
