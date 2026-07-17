"""Domain normalization for citation classification.

Package-local, minimal helper the answer-engine parsers use to derive a clean
citation host from a URL or a domain-shaped title. The full text/alias
normalization suite lives with the analysis subsystem (B6); this file owns only
the domain form the adapters need so parsing has no cross-subsystem dependency.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


def annotation_offset(annotation: dict[str, Any], *keys: str) -> int | None:
    """First integer-coercible offset among ``keys`` on a citation annotation.

    Accepts both snake_case and camelCase offset keys (REST vs SDK casing).
    Returns ``None`` when no key is present or the value is not int-coercible.
    """
    for key in keys:
        if key in annotation and annotation[key] is not None:
            try:
                return int(annotation[key])
            except (TypeError, ValueError):
                continue
    return None


def coerce_int(value: object, default: int = 0) -> int:
    """Best-effort integer coercion that never raises.

    Returns ``default`` when ``value`` is missing or not int-coercible, so
    malformed provider usage payloads degrade gracefully instead of crashing
    the worker path.
    """
    if value is None:
        return default
    try:
        # int(float("inf"))/int("nan") raise OverflowError/ValueError; treat any
        # non-int-coercible or non-finite value as the default.
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


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
