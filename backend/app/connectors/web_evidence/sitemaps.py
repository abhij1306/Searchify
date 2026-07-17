# Bounded, safe sitemap parsing for the Site Health crawler (Task 3).
#
# Sitemaps are attacker-influenced XML, so parsing is defensive on every axis:
#   - ``defusedxml`` blocks XML entity-expansion / external-entity attacks.
#   - A per-document decoded-byte cap (``max_sitemap_decoded_bytes``) plus a
#     gzip expansion cap guard decompression bombs BEFORE parsing.
#   - A URL cap (``max_sitemap_urls``) bounds total extracted URLs.
#   - Sitemap-index recursion is bounded by ``max_sitemap_index_depth`` and a
#     visited-set so a self-referential / mutually-recursive index cannot loop.
#
# This module is pure parsing: the caller (worker) fetches each sitemap through
# the SSRF-safe fetcher and passes the decoded bytes here, then re-feeds child
# sitemap URLs from an index up to the depth cap. It returns plain URL strings;
# the URL policy decides admissibility.
from __future__ import annotations

import gzip
import io

from defusedxml.ElementTree import fromstring as safe_fromstring

from app.core.config.site_health import site_health_settings

# Sitemap XML uses this namespace; we match tags namespace-agnostically by
# stripping the ``{ns}`` prefix so a missing/alternate namespace still parses.


class SitemapParseError(ValueError):
    """A sitemap body was too large, malformed, or exceeded a bound."""


def _localname(tag: str) -> str:
    """Return the local tag name, dropping any ``{namespace}`` prefix."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1].lower()
    return tag.lower()


def maybe_gunzip(body: bytes, *, content_type: str = "") -> bytes:
    """Decompress a gzipped sitemap body under the decoded-byte cap.

    Detects gzip by magic bytes or content type. Streams the decompression and
    aborts with ``SitemapParseError`` once the decoded size exceeds
    ``max_sitemap_decoded_bytes`` (compression-bomb guard). Non-gzip bodies are
    returned unchanged (still bounded by the cap).
    """
    cap = site_health_settings.max_sitemap_decoded_bytes
    is_gzip = body[:2] == b"\x1f\x8b" or "gzip" in (content_type or "").lower()
    if not is_gzip:
        if len(body) > cap:
            raise SitemapParseError("sitemap exceeded decoded byte cap")
        return body
    out = io.BytesIO()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
            while True:
                chunk = gz.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                if out.tell() > cap:
                    raise SitemapParseError(
                        "gzip sitemap exceeded decoded byte cap (compression bomb)"
                    )
    except OSError as exc:
        raise SitemapParseError("malformed gzip sitemap") from exc
    return out.getvalue()


class SitemapDocument:
    """One parsed sitemap: either URL entries or child sitemap references."""

    __slots__ = ("urls", "sitemap_refs", "is_index")

    def __init__(
        self,
        *,
        urls: list[str],
        sitemap_refs: list[str],
        is_index: bool,
    ) -> None:
        self.urls = urls
        self.sitemap_refs = sitemap_refs
        self.is_index = is_index


def parse_sitemap_document(body: bytes, *, content_type: str = "") -> SitemapDocument:
    """Parse ONE sitemap document (gunzip if needed) into a bounded result.

    Recognizes a ``<sitemapindex>`` (returns child ``<loc>`` sitemap refs) and
    a ``<urlset>`` (returns ``<loc>`` page URLs). Caps extracted URLs at
    ``max_sitemap_urls``. Raises ``SitemapParseError`` on malformed XML or a
    bound violation.
    """
    decoded = maybe_gunzip(body, content_type=content_type)
    try:
        root = safe_fromstring(decoded)
    except Exception as exc:  # defusedxml raises on entity attacks + malformed
        raise SitemapParseError("malformed or unsafe sitemap XML") from exc

    root_name = _localname(root.tag)
    is_index = root_name == "sitemapindex"
    max_urls = site_health_settings.max_sitemap_urls
    urls: list[str] = []
    refs: list[str] = []

    for child in root:
        if _localname(child.tag) not in ("url", "sitemap"):
            continue
        loc_text = ""
        for grand in child:
            if _localname(grand.tag) == "loc":
                loc_text = (grand.text or "").strip()
                break
        if not loc_text:
            continue
        if is_index or _localname(child.tag) == "sitemap":
            refs.append(loc_text)
        else:
            urls.append(loc_text)
        if len(urls) >= max_urls or len(refs) >= max_urls:
            break

    return SitemapDocument(urls=urls, sitemap_refs=refs, is_index=is_index)


class SitemapCollector:
    """Bounded, loop-safe walk over a sitemap tree (index recursion capped).

    The worker feeds each fetched sitemap body here with its source URL; the
    collector records URLs (capped) and returns any child sitemap refs that are
    still within the recursion-depth budget and have not been visited, so the
    worker can fetch them next. It never fetches anything itself.
    """

    def __init__(self) -> None:
        self._settings = site_health_settings
        self.urls: list[str] = []
        self._visited: set[str] = set()

    @property
    def url_count(self) -> int:
        return len(self.urls)

    def _room(self) -> int:
        return max(0, self._settings.max_sitemap_urls - len(self.urls))

    def add_document(
        self, source_url: str, body: bytes, *, content_type: str = "", depth: int
    ) -> list[str]:
        """Ingest one fetched sitemap; return child refs to fetch next.

        ``depth`` is the current sitemap-index depth (0 = the root sitemap).
        Child refs are returned only while ``depth < max_sitemap_index_depth``
        and are de-duplicated against everything already visited so a recursive
        or self-referential index can never loop.
        """
        self._visited.add(source_url)
        doc = parse_sitemap_document(body, content_type=content_type)

        room = self._room()
        if room and doc.urls:
            self.urls.extend(doc.urls[:room])

        if depth >= self._settings.max_sitemap_index_depth:
            return []
        next_refs: list[str] = []
        for ref in doc.sitemap_refs:
            if ref in self._visited or ref in next_refs:
                continue
            next_refs.append(ref)
        return next_refs
