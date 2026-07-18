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
import hashlib
import json
from collections.abc import Mapping

from app.connectors.web_evidence.url_policy import canonicalize


def url_hash(normalized_url: str) -> str:
    """Return the stable 64-char identity hash for a canonical URL."""
    return hashlib.sha256(str(normalized_url).encode("utf-8")).hexdigest()[:64]


def canonical_identity(url: str, *, base_url: str | None = None) -> tuple[str, str]:
    """Return ``(canonical_url, url_hash)`` for a raw/relative URL.

    Raises ``UrlPolicyError`` (from ``canonicalize``) for a URL the policy
    rejects (bad scheme/port/userinfo), so callers never admit an unsafe URL.
    """
    canonical = canonicalize(url, base_url=base_url)
    return canonical, url_hash(canonical)


# =========================================================================
# Filter-aware, typed keyset cursors (Slice 6 API)
# =========================================================================
#
# Every paginated Site Health endpoint (inventory, pages, crawls, grouped
# issues, per-URL issue history, affected URLs) uses an opaque keyset cursor
# whose payload carries BOTH the sort tuple AND a fingerprint of the endpoint +
# active filters. On decode the fingerprint is verified against the current
# request so a cursor can never be replayed against a different endpoint or a
# different filter set (which would silently skip/duplicate rows). A mismatch
# raises ``CursorScopeError`` (the API maps it to a 400).


class CursorScopeError(ValueError):
    """A cursor's endpoint/filter fingerprint does not match the request."""


def filter_fingerprint(scope: str, filters: Mapping[str, object]) -> str:
    """Deterministic short fingerprint of an endpoint scope + its filters.

    Normalizes ``filters`` to a stable JSON encoding (sorted keys, empty/None
    values dropped so an explicit empty filter and an absent one collapse to the
    same page identity) and hashes it with the endpoint ``scope`` label. Two
    requests with the same scope + effective filters share a fingerprint; any
    difference (a new status filter, a monitored toggle, a different query) makes
    the previous cursor invalid.
    """
    cleaned = {
        key: value
        for key, value in sorted(filters.items())
        if value is not None and value != ""
    }
    raw = json.dumps(
        {"s": scope, "f": cleaned}, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def encode_keyset_cursor(
    *,
    scope: str,
    filters: Mapping[str, object],
    sort_values: list[object],
) -> str:
    """Encode a typed keyset cursor bound to an endpoint scope + filters.

    ``sort_values`` is the ordered tuple of the last row's sort-key values (e.g.
    ``[normalized_url, str(site_url_id)]`` or ``[severity_rank, rule_id,
    canonical_id]``). It is stored verbatim (JSON-safe) so the query can rebuild
    the exact ``(a, b, c) > (:a, :b, :c)`` keyset predicate. The endpoint/filter
    fingerprint is embedded so the cursor cannot be replayed cross-scope.
    """
    payload = {
        "fp": filter_fingerprint(scope, filters),
        "k": [str(v) for v in sort_values],
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_keyset_cursor(
    cursor: str, *, scope: str, filters: Mapping[str, object]
) -> list[str]:
    """Decode + verify a typed keyset cursor; return the sort-value tuple.

    Raises ``CursorScopeError`` when the embedded fingerprint does not match the
    current endpoint scope + filters (replay across a different query), and
    ``ValueError`` for a tampered/malformed cursor.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
        stored_fp = str(payload["fp"])
        values = [str(v) for v in payload["k"]]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("invalid cursor") from exc
    if stored_fp != filter_fingerprint(scope, filters):
        raise CursorScopeError("cursor does not match the current endpoint filters")
    return values
