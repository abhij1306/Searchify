"""Domain normalization for citation classification.

Package-local, minimal helper the answer-engine parsers use to derive a clean
citation host from a URL or a domain-shaped title. The full text/alias
normalization suite lives with the analysis subsystem (B6); this file owns only
the domain form the adapters need so parsing has no cross-subsystem dependency.
"""

from __future__ import annotations

from urllib.parse import urlsplit


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
