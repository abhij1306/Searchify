# Deterministic HTML + delivery fact extraction (Task 5).
#
# ``extract_page_facts`` turns one fetched page (decoded HTML bytes + the
# artifact's redacted delivery facts) into a bounded, JSON-safe dict of "page
# facts": metadata, headings, images, body text/word count, structured data,
# links/assets, and delivery/security signals. It is a PURE function (no I/O, no
# ORM) so the same input always yields the same facts (invariant 9), and every
# extraction step is guarded so a malformed/hostile page yields PARTIAL facts,
# never a crash (subplan Persistence contract).
#
# The lxml parser runs with ``no_network=True`` (never resolves an external
# DTD/entity) and JSON-LD is parsed with the stdlib loader, so there is no XML
# external-entity attack surface; defusedxml is used for any raw XML parse.
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from lxml import etree
from lxml import html as lxml_html

from app.analysis.site_health.structured_data import (
    parse_jsonld_blocks,
    validate_microdata_types,
)
from app.core.config.site_health import (
    EXTRACTOR_VERSION,
    LINK_KIND_ANCHOR,
    LINK_KIND_IMAGE,
    LINK_KIND_SCRIPT,
    LINK_KIND_STYLESHEET,
    site_health_settings,
)

# Bounded per-field caps so a single hostile attribute can never bloat the
# persisted facts dict.
_MAX_TITLE_CHARS = 2048
_MAX_META_CHARS = 4096
_MAX_HEADING_CHARS = 512
_MAX_HEADINGS_KEPT = 50
_MAX_URL_CHARS = 2048
_MAX_ANCHOR_TEXT_CHARS = 512

# The security response headers whose mere presence the delivery facts record.
_SECURITY_HEADERS = (
    "strict-transport-security",
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
)


def _text(node: Any) -> str:
    try:
        return (node.text_content() or "").strip()
    except Exception:
        return ""


def _parse_robots_directives(root: Any) -> dict[str, bool]:
    """Extract robots meta directives (noindex / nofollow) from the head.

    Reads every ``<meta name="robots">`` (and the ``googlebot`` variant),
    splitting on commas. Deterministic + case-insensitive.
    """
    noindex = False
    nofollow = False
    try:
        nodes = root.xpath(
            "//meta[translate(@name,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz')='robots' or "
            "translate(@name,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz')='googlebot']"
        )
    except Exception:
        nodes = []
    for node in nodes:
        content = (node.get("content") or "").lower()
        tokens = {tok.strip() for tok in content.split(",")}
        if "noindex" in tokens or "none" in tokens:
            noindex = True
        if "nofollow" in tokens or "none" in tokens:
            nofollow = True
    return {"noindex": noindex, "nofollow": nofollow}


def _meta_content(root: Any, *, name: str) -> str:
    try:
        nodes = root.xpath(
            "//meta[translate(@name,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz')=$n]",
            n=name.lower(),
        )
    except Exception:
        return ""
    for node in nodes:
        content = (node.get("content") or "").strip()
        if content:
            return content[:_MAX_META_CHARS]
    return ""


def _meta_property_map(root: Any, *, prefix: str) -> dict[str, str]:
    """Collect ``<meta property="prefix:...">`` (OG) or name= (Twitter) tags."""
    out: dict[str, str] = {}
    try:
        nodes = root.xpath("//meta[@property or @name]")
    except Exception:
        return out
    for node in nodes:
        key = (node.get("property") or node.get("name") or "").strip().lower()
        if not key or not key.startswith(prefix):
            continue
        content = (node.get("content") or "").strip()
        if content and key not in out:
            out[key] = content[:_MAX_META_CHARS]
    return out


def _canonical_href(root: Any) -> str:
    try:
        nodes = root.xpath(
            "//link[translate(@rel,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz')='canonical']"
        )
    except Exception:
        return ""
    for node in nodes:
        href = (node.get("href") or "").strip()
        if href:
            return href[:_MAX_URL_CHARS]
    return ""


def _headings(root: Any) -> dict[str, Any]:
    """Count h1..h6 and capture bounded h1/h2 text (deterministic order)."""
    counts: dict[str, int] = {}
    h1_texts: list[str] = []
    h2_texts: list[str] = []
    for level in range(1, 7):
        tag = f"h{level}"
        try:
            nodes = root.xpath(f"//{tag}")
        except Exception:
            nodes = []
        counts[tag] = len(nodes)
        if level == 1:
            for node in nodes[:_MAX_HEADINGS_KEPT]:
                h1_texts.append(_text(node)[:_MAX_HEADING_CHARS])
        elif level == 2:
            for node in nodes[:_MAX_HEADINGS_KEPT]:
                h2_texts.append(_text(node)[:_MAX_HEADING_CHARS])
    return {
        "counts": counts,
        "h1_count": counts.get("h1", 0),
        "h1_texts": h1_texts,
        "h2_texts": h2_texts,
    }


def _images(root: Any) -> dict[str, int]:
    """Count images and how many are missing a non-empty alt attribute."""
    try:
        nodes = root.xpath("//img")
    except Exception:
        nodes = []
    total = len(nodes)
    missing_alt = 0
    for node in nodes:
        alt = node.get("alt")
        if alt is None or not str(alt).strip():
            missing_alt += 1
    return {"count": total, "missing_alt": missing_alt}


def _body_text(root: Any, *, max_chars: int) -> dict[str, Any]:
    """Extract bounded visible body text + a whitespace-split word count.

    Script/style/noscript/template subtrees are dropped so their content never
    inflates the word count. The text is capped at ``max_chars``.
    """
    body_nodes = root.xpath("//body")
    node = body_nodes[0] if body_nodes else root
    # Drop non-content subtrees before reading text.
    try:
        for junk in node.xpath(
            ".//script | .//style | .//noscript | .//template"
        ):
            junk.getparent().remove(junk)
    except Exception:
        pass
    raw = _text(node)
    text = " ".join(raw.split())[:max_chars]
    word_count = len(text.split()) if text else 0
    return {"text": text, "word_count": word_count}


def _links_and_assets(
    root: Any, *, base_host: str, max_links: int
) -> dict[str, list[dict]]:
    """Collect bounded anchors + img/script/stylesheet assets in document order.

    Each entry carries the raw ``url`` (bounded), the ``kind``, an
    ``is_internal`` heuristic (same host as the final URL when both are
    absolute), and, for anchors, the ``rel`` + bounded anchor text. Bounded by
    ``max_links`` PER kind so the persisted facts stay small.
    """
    anchors: list[dict] = []
    images: list[dict] = []
    scripts: list[dict] = []
    stylesheets: list[dict] = []

    def _internal(url: str) -> bool:
        try:
            host = urlsplit(url).hostname
        except Exception:
            return False
        if host is None:
            # A relative URL is same-origin by definition.
            return True
        return bool(base_host) and host.lower() == base_host.lower()

    try:
        for anchor in root.iter("a"):
            if len(anchors) >= max_links:
                break
            href = (anchor.get("href") or "").strip()
            if not href or href.startswith(
                ("#", "javascript:", "mailto:", "tel:")
            ):
                continue
            anchors.append(
                {
                    "kind": LINK_KIND_ANCHOR,
                    "url": href[:_MAX_URL_CHARS],
                    "is_internal": _internal(href),
                    "rel": (anchor.get("rel") or "")[:128],
                    "anchor_text": _text(anchor)[:_MAX_ANCHOR_TEXT_CHARS],
                }
            )
    except Exception:
        pass

    try:
        for img in root.iter("img"):
            if len(images) >= max_links:
                break
            src = (img.get("src") or "").strip()
            if not src:
                continue
            images.append(
                {
                    "kind": LINK_KIND_IMAGE,
                    "url": src[:_MAX_URL_CHARS],
                    "is_internal": _internal(src),
                }
            )
    except Exception:
        pass

    try:
        for script in root.iter("script"):
            if len(scripts) >= max_links:
                break
            src = (script.get("src") or "").strip()
            if not src:
                continue
            scripts.append(
                {
                    "kind": LINK_KIND_SCRIPT,
                    "url": src[:_MAX_URL_CHARS],
                    "is_internal": _internal(src),
                }
            )
    except Exception:
        pass

    try:
        for link in root.iter("link"):
            if len(stylesheets) >= max_links:
                break
            rel = (link.get("rel") or "").strip().lower()
            if "stylesheet" not in rel.split():
                continue
            href = (link.get("href") or "").strip()
            if not href:
                continue
            stylesheets.append(
                {
                    "kind": LINK_KIND_STYLESHEET,
                    "url": href[:_MAX_URL_CHARS],
                    "is_internal": _internal(href),
                }
            )
    except Exception:
        pass

    return {
        "anchors": anchors,
        "images": images,
        "scripts": scripts,
        "stylesheets": stylesheets,
    }


def _structured_data(root: Any, *, max_blocks: int) -> dict[str, Any]:
    """Extract + validate JSON-LD + microdata structured-data facts."""
    raw_jsonld: list[str] = []
    try:
        for script in root.xpath(
            "//script[translate(@type,"
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz')='application/ld+json']"
        ):
            raw_jsonld.append(script.text_content() or "")
    except Exception:
        raw_jsonld = []
    jsonld_facts = parse_jsonld_blocks(raw_jsonld, max_blocks=max_blocks)

    itemtypes: list[str] = []
    try:
        for node in root.xpath("//*[@itemscope][@itemtype]"):
            itemtype = (node.get("itemtype") or "").strip()
            if itemtype:
                itemtypes.append(itemtype)
    except Exception:
        itemtypes = []
    microdata_facts = validate_microdata_types(
        itemtypes, max_blocks=max_blocks
    )

    blocks = (jsonld_facts + microdata_facts)[:max_blocks]
    return {
        "blocks": blocks,
        "count": len(blocks),
        "has_json_ld": bool(jsonld_facts),
        "has_microdata": bool(microdata_facts),
        "types": sorted({b["type"] for b in blocks}),
    }


def _delivery_facts(
    *,
    final_url: str,
    status_code: int | None,
    redacted_headers: dict[str, str] | None,
    http_version: str,
    ttfb_ms: int | None,
    latency_ms: int | None,
    wire_bytes: int | None,
    decoded_bytes: int | None,
) -> dict[str, Any]:
    """Derive delivery/security facts from the artifact's delivery fields.

    Pure: reads only the (already redacted) header allowlist + timing/byte
    fields the fetch produced. Records HTTPS from the final URL scheme, TTFB /
    wire/decoded bytes / HTTP version, compression + cache directives, and the
    PRESENCE of each security header (never the value).
    """
    headers = {
        str(k).lower(): str(v) for k, v in (redacted_headers or {}).items()
    }
    scheme = ""
    try:
        scheme = (urlsplit(final_url).scheme or "").lower()
    except Exception:
        scheme = ""
    content_encoding = headers.get("content-encoding", "").strip().lower()
    security_headers = {
        name: name in headers for name in _SECURITY_HEADERS
    }
    return {
        "final_url": (final_url or "")[:_MAX_URL_CHARS],
        "scheme": scheme,
        "is_https": scheme == "https",
        "status_code": status_code,
        "http_version": http_version or "",
        "ttfb_ms": ttfb_ms,
        "latency_ms": latency_ms,
        "wire_bytes": wire_bytes,
        "decoded_bytes": decoded_bytes,
        "content_encoding": content_encoding,
        "is_compressed": bool(content_encoding)
        and content_encoding != "identity",
        "cache_control": headers.get("cache-control", ""),
        "security_headers": security_headers,
        # Static blocking-resource heuristic: render-blocking assets are the
        # synchronous scripts + stylesheets referenced in the document. Counted
        # from the parsed facts by the caller; recorded here as a flag holder.
    }


def _empty_facts() -> dict[str, Any]:
    return {
        "has_html": False,
        "title": "",
        "meta_description": "",
        "robots": {"noindex": False, "nofollow": False},
        "canonical_url": "",
        "open_graph": {},
        "twitter": {},
        "headings": {
            "counts": {},
            "h1_count": 0,
            "h1_texts": [],
            "h2_texts": [],
        },
        "images": {"count": 0, "missing_alt": 0},
        "body": {"text": "", "word_count": 0},
        "structured_data": {
            "blocks": [],
            "count": 0,
            "has_json_ld": False,
            "has_microdata": False,
            "types": [],
        },
        "links": {
            "anchors": [],
            "images": [],
            "scripts": [],
            "stylesheets": [],
        },
        "blocking_resources": {"scripts": 0, "stylesheets": 0, "total": 0},
    }


def extract_page_facts(
    body: bytes,
    *,
    final_url: str,
    content_type: str = "",
    status_code: int | None = None,
    redacted_headers: dict[str, str] | None = None,
    http_version: str = "",
    ttfb_ms: int | None = None,
    latency_ms: int | None = None,
    wire_bytes: int | None = None,
    decoded_bytes: int | None = None,
    settings=site_health_settings,
) -> dict[str, Any]:
    """Extract the bounded, deterministic page-facts dict for one page.

    PURE: ``body`` is the decoded HTML bytes; the remaining kwargs are the
    artifact's delivery facts. Returns a JSON-safe dict. HTML parsing is fully
    guarded — a malformed/empty page yields partial facts with ``has_html``
    reflecting whether any DOM was parsed. Never raises.
    """
    facts = _empty_facts()
    facts["extractor_version"] = EXTRACTOR_VERSION
    facts["content_type"] = (content_type or "").strip().lower()

    # Delivery facts never depend on the HTML parse succeeding.
    facts["delivery"] = _delivery_facts(
        final_url=final_url,
        status_code=status_code,
        redacted_headers=redacted_headers,
        http_version=http_version,
        ttfb_ms=ttfb_ms,
        latency_ms=latency_ms,
        wire_bytes=wire_bytes,
        decoded_bytes=decoded_bytes,
    )

    if not body:
        return facts

    # Bound the bytes handed to the parser (defence against an oversize body
    # that slipped past the fetch cap).
    bounded = body[: settings.max_html_bytes]
    parser = lxml_html.HTMLParser(
        recover=True, encoding="utf-8", no_network=True
    )
    try:
        root = lxml_html.document_fromstring(bounded, parser=parser)
    except (etree.ParserError, ValueError):
        return facts
    if root is None:
        return facts

    facts["has_html"] = True

    try:
        title_nodes = root.xpath("//title")
        if title_nodes:
            facts["title"] = (_text(title_nodes[0]))[:_MAX_TITLE_CHARS]
    except Exception:
        pass

    facts["meta_description"] = _meta_content(root, name="description")
    facts["robots"] = _parse_robots_directives(root)
    facts["canonical_url"] = _canonical_href(root)
    facts["open_graph"] = _meta_property_map(root, prefix="og:")
    facts["twitter"] = _meta_property_map(root, prefix="twitter:")
    facts["headings"] = _headings(root)
    facts["images"] = _images(root)
    facts["structured_data"] = _structured_data(
        root, max_blocks=settings.max_structured_data_blocks
    )

    base_host = ""
    try:
        base_host = urlsplit(final_url).hostname or ""
    except Exception:
        base_host = ""
    facts["links"] = _links_and_assets(
        root, base_host=base_host, max_links=settings.max_links_per_page
    )

    # Static blocking-resource heuristic: synchronous <script src> (no async/
    # defer) plus stylesheet <link>s block first render.
    blocking_scripts = 0
    try:
        for script in root.iter("script"):
            if not (script.get("src") or "").strip():
                continue
            if script.get("async") is not None or script.get("defer") is not None:
                continue
            blocking_scripts += 1
    except Exception:
        blocking_scripts = 0
    blocking_styles = len(facts["links"]["stylesheets"])
    facts["blocking_resources"] = {
        "scripts": blocking_scripts,
        "stylesheets": blocking_styles,
        "total": blocking_scripts + blocking_styles,
    }

    # Body text last (it mutates the tree by removing script/style subtrees).
    facts["body"] = _body_text(root, max_chars=settings.max_text_chars)
    return facts
