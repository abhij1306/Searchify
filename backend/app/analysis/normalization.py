"""Deterministic text and domain normalization for AI-visibility scoring.

All brand/competitor matching is alias-based (no fuzzy matching) to avoid false
positives. These helpers make the matching robust to casing, punctuation,
``&``/``and`` equivalence, and ``www.``/fragment noise on domains.

Ported unchanged from the reference ``ai_visibility/normalization.py`` (B6).
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)


def normalize_text(value: object) -> str:
    """NFKC + casefold + ``&``-> ``and`` + whitespace collapse.

    Used to build the searchable form of an answer. Punctuation is preserved
    (only collapsed whitespace) so that offsets remain meaningful for callers
    that only need case/width-insensitive matching.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("&", " and ")
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_alias(value: object) -> str:
    """Aggressive normalization for alias matching: strip punctuation too.

    ``Best&Less`` / ``Best & Less`` / ``Best and Less`` all collapse to the same
    token ``best and less``.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("&", " and ")
    text = _PUNCTUATION_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def alias_present(alias: str, normalized_haystack: str) -> bool:
    """Whole-token containment of a normalized alias in a normalized haystack.

    Both sides must already be normalized with :func:`normalize_alias`. Uses word
    boundaries so ``target`` does not match inside ``targeted``.
    """
    alias = alias.strip()
    if not alias:
        return False
    pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"
    return re.search(pattern, normalized_haystack) is not None


def first_alias_offset(alias: str, normalized_haystack: str) -> int | None:
    alias = alias.strip()
    if not alias:
        return None
    match = re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", normalized_haystack)
    return match.start() if match else None


def normalize_domain(value: object) -> str:
    """Lowercase host, strip ``www.`` and any leading scheme/path.

    Accepts a bare domain (``Kmart.com.au``), a full URL, or a citation title
    that is itself a domain (as Gemini returns).
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    host = urlsplit(text).hostname or ""
    return host.removeprefix("www.")


def normalize_url(value: object) -> str:
    """Drop the URL fragment; keep scheme/host/path/query. Lowercase host only."""
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    host = (parts.hostname or "").lower().removeprefix("www.")
    rebuilt = f"{parts.scheme}://{host}"
    if parts.port:
        rebuilt += f":{parts.port}"
    rebuilt += parts.path
    if parts.query:
        rebuilt += f"?{parts.query}"
    return rebuilt


def domain_matches(candidate: str, target: str) -> bool:
    """True if ``candidate`` domain equals or is a subdomain of ``target``.

    ``shop.bestandless.com.au`` matches owned domain ``bestandless.com.au``.
    """
    candidate = normalize_domain(candidate)
    target = normalize_domain(target)
    if not candidate or not target:
        return False
    return candidate == target or candidate.endswith("." + target)
