"""Unit tests for the Site Health page-fact parser + structured-data helpers.

Pure, offline: local HTML byte fixtures only (no live internet). Covers full
fact extraction, malformed/partial pages, the bounded limits, delivery/security
facts, and JSON-LD / microdata validation against the config schema map.
"""
from __future__ import annotations

from app.analysis.site_health.parser import extract_page_facts
from app.analysis.site_health.structured_data import (
    parse_jsonld_blocks,
    validate_microdata_types,
)
from app.core.config.site_health import EXTRACTOR_VERSION, site_health_settings

_FULL_PAGE = b"""
<html>
  <head>
    <title>Acme Widgets</title>
    <meta name="description" content="Best widgets on the web.">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="https://acme.example.com/widgets">
    <meta property="og:title" content="Acme Widgets">
    <meta property="og:description" content="Buy widgets">
    <meta name="twitter:card" content="summary">
    <link rel="stylesheet" href="/styles.css">
    <script type="application/ld+json">
      {"@context":"https://schema.org","@type":"Organization",
       "name":"Acme","url":"https://acme.example.com"}
    </script>
  </head>
  <body>
    <h1>Acme Widgets</h1>
    <h2>Section one</h2>
    <h2>Section two</h2>
    <p>Widgets are great. We sell many widgets to many happy customers.</p>
    <img src="/a.png" alt="a picture">
    <img src="/b.png">
    <a href="https://acme.example.com/about" rel="nofollow">About</a>
    <a href="https://external.org/x">External</a>
    <a href="#frag">skip</a>
    <script src="/app.js"></script>
    <script src="/async.js" async></script>
  </body>
</html>
"""


def _facts(body: bytes, **kwargs):
    defaults = dict(
        final_url="https://acme.example.com/widgets",
        content_type="text/html",
        status_code=200,
        redacted_headers={
            "content-encoding": "gzip",
            "cache-control": "max-age=60",
            "strict-transport-security": "max-age=31536000",
            "content-security-policy": "default-src 'self'",
        },
        http_version="HTTP/2",
        ttfb_ms=42,
        latency_ms=90,
        wire_bytes=1234,
        decoded_bytes=4567,
    )
    defaults.update(kwargs)
    return extract_page_facts(body, **defaults)


def test_full_page_extraction():
    facts = _facts(_FULL_PAGE)
    assert facts["has_html"] is True
    assert facts["title"] == "Acme Widgets"
    assert facts["meta_description"] == "Best widgets on the web."
    assert facts["robots"] == {"noindex": False, "nofollow": False}
    assert facts["canonical_url"] == "https://acme.example.com/widgets"
    assert facts["open_graph"]["og:title"] == "Acme Widgets"
    assert facts["open_graph"]["og:description"] == "Buy widgets"
    assert facts["twitter"]["twitter:card"] == "summary"
    assert facts["headings"]["h1_count"] == 1
    assert facts["headings"]["counts"]["h2"] == 2
    assert facts["images"]["count"] == 2
    assert facts["images"]["missing_alt"] == 1
    assert facts["body"]["word_count"] > 0
    assert "widgets" in facts["body"]["text"].lower()
    assert facts["extractor_version"] == EXTRACTOR_VERSION


def test_structured_data_extraction_and_validation():
    facts = _facts(_FULL_PAGE)
    sd = facts["structured_data"]
    assert sd["count"] == 1
    assert sd["has_json_ld"] is True
    assert "Organization" in sd["types"]
    block = sd["blocks"][0]
    assert block["type"] == "Organization"
    assert block["valid"] is True
    assert set(block["present"]) == {"name", "url"}


def test_links_and_assets_classification():
    facts = _facts(_FULL_PAGE)
    links = facts["links"]
    # Fragment + external anchors: fragment dropped, external kept but external.
    anchor_urls = [a["url"] for a in links["anchors"]]
    assert "https://acme.example.com/about" in anchor_urls
    assert "https://external.org/x" in anchor_urls
    assert not any("#frag" in u for u in anchor_urls)
    internal = {a["url"]: a["is_internal"] for a in links["anchors"]}
    assert internal["https://acme.example.com/about"] is True
    assert internal["https://external.org/x"] is False
    assert [a["rel"] for a in links["anchors"] if "about" in a["url"]] == [
        "nofollow"
    ]
    assert len(links["scripts"]) == 2
    assert len(links["stylesheets"]) == 1
    # One sync script blocks; async does not; one stylesheet blocks.
    assert facts["blocking_resources"]["scripts"] == 1
    assert facts["blocking_resources"]["stylesheets"] == 1
    assert facts["blocking_resources"]["total"] == 2


def test_delivery_and_security_facts():
    facts = _facts(_FULL_PAGE)
    delivery = facts["delivery"]
    assert delivery["is_https"] is True
    assert delivery["scheme"] == "https"
    assert delivery["http_version"] == "HTTP/2"
    assert delivery["ttfb_ms"] == 42
    assert delivery["wire_bytes"] == 1234
    assert delivery["decoded_bytes"] == 4567
    assert delivery["content_encoding"] == "gzip"
    assert delivery["is_compressed"] is True
    assert delivery["cache_control"] == "max-age=60"
    sh = delivery["security_headers"]
    assert sh["strict-transport-security"] is True
    assert sh["content-security-policy"] is True
    assert sh["x-frame-options"] is False


def test_noindex_robots_directive():
    body = (
        b"<html><head><title>x</title>"
        b'<meta name="robots" content="noindex, nofollow"></head>'
        b"<body><h1>x</h1></body></html>"
    )
    facts = _facts(body)
    assert facts["robots"]["noindex"] is True
    assert facts["robots"]["nofollow"] is True


def test_http_final_url_not_https():
    facts = _facts(_FULL_PAGE, final_url="http://acme.example.com/widgets")
    assert facts["delivery"]["is_https"] is False
    assert facts["delivery"]["scheme"] == "http"


def test_empty_body_yields_partial_facts():
    facts = _facts(b"")
    assert facts["has_html"] is False
    assert facts["title"] == ""
    assert facts["structured_data"]["count"] == 0
    # Delivery facts still computed from the artifact fields.
    assert facts["delivery"]["is_https"] is True


def test_malformed_html_never_crashes():
    body = b"<html><head><title>Broken<body><h1>hi</h1><a href='/x'>"
    facts = _facts(body)
    # lxml's recover parser tolerates it and still yields facts.
    assert facts["has_html"] is True
    assert facts["title"] == "Broken"
    assert facts["headings"]["h1_count"] == 1


def test_malformed_jsonld_block_skipped_but_others_kept():
    body = (
        b"<html><head><title>x</title>"
        b'<script type="application/ld+json">{ not json }</script>'
        b'<script type="application/ld+json">'
        b'{"@type":"WebSite","name":"S","url":"https://s.example"}'
        b"</script></head><body><h1>x</h1></body></html>"
    )
    facts = _facts(body)
    sd = facts["structured_data"]
    assert sd["count"] == 1
    assert sd["blocks"][0]["type"] == "WebSite"
    assert sd["blocks"][0]["valid"] is True


def test_multiple_h1_counted():
    body = (
        b"<html><head><title>x</title></head>"
        b"<body><h1>one</h1><h1>two</h1></body></html>"
    )
    facts = _facts(body)
    assert facts["headings"]["h1_count"] == 2


def test_link_bound_enforced(monkeypatch):
    # Build a page with more anchors than the configured bound.
    limit = site_health_settings.max_links_per_page
    anchors = "".join(
        f'<a href="https://acme.example.com/p{i}">l</a>'
        for i in range(limit + 25)
    )
    body = (
        f"<html><head><title>x</title></head><body>{anchors}</body></html>"
    ).encode()
    facts = _facts(body)
    assert len(facts["links"]["anchors"]) == limit


def test_structured_data_block_bound_enforced():
    blocks = [
        '{"@type":"Organization","name":"n","url":"https://u.example"}'
        for _ in range(site_health_settings.max_structured_data_blocks + 5)
    ]
    facts = parse_jsonld_blocks(
        blocks, max_blocks=site_health_settings.max_structured_data_blocks
    )
    assert len(facts) == site_health_settings.max_structured_data_blocks


def test_text_bound_enforced():
    long_text = "word " * 5000
    body = (
        f"<html><head><title>x</title></head>"
        f"<body><p>{long_text}</p></body></html>"
    ).encode()
    facts = extract_page_facts(
        body,
        final_url="https://x.example/",
        content_type="text/html",
        settings=site_health_settings,
    )
    assert len(facts["body"]["text"]) <= site_health_settings.max_text_chars


def test_jsonld_missing_required_property_invalid():
    facts = parse_jsonld_blocks(
        ['{"@type":"Article","headline":"H"}'], max_blocks=10
    )
    assert len(facts) == 1
    assert facts[0]["type"] == "Article"
    assert facts[0]["valid"] is False
    assert "author" in facts[0]["missing"]
    assert "datePublished" in facts[0]["missing"]


def test_jsonld_graph_and_type_url_normalization():
    payload = (
        '{"@context":"https://schema.org","@graph":['
        '{"@type":"http://schema.org/WebPage","name":"P"},'
        '{"@type":["Organization"],"name":"O","url":"https://o.example"}]}'
    )
    facts = parse_jsonld_blocks([payload], max_blocks=10)
    types = {f["type"] for f in facts}
    assert types == {"WebPage", "Organization"}


def test_unrecognized_jsonld_type_ignored():
    facts = parse_jsonld_blocks(
        ['{"@type":"UnknownThing","name":"x"}'], max_blocks=10
    )
    assert facts == []


def test_microdata_validation():
    facts = validate_microdata_types(
        ["https://schema.org/Product", "https://schema.org/Nope"],
        max_blocks=10,
    )
    assert len(facts) == 1
    assert facts[0]["type"] == "Product"
    assert facts[0]["syntax"] == "microdata"
    assert facts[0]["valid"] is False


# --- charset handling (handoff finding 5) ---------------------------------


def test_bogus_charset_falls_back_and_never_crashes():
    # An arbitrary/unknown declared charset must NOT crash extraction: it is
    # validated away (codecs.lookup fails) and lxml auto-detects instead. The
    # parser still returns the page facts.
    facts = _facts(_FULL_PAGE, charset="totally-not-a-real-charset")
    assert facts["title"] == "Acme Widgets"
    assert facts["extractor_version"] == EXTRACTOR_VERSION


def test_empty_charset_auto_detects():
    facts = _facts(_FULL_PAGE, charset="")
    assert facts["title"] == "Acme Widgets"


def test_valid_declared_charset_is_honored():
    # A Latin-1 page whose non-ASCII byte must be decoded with the declared
    # charset (not UTF-8) to yield the correct title character.
    body = (
        "<html><head><title>Caf\u00e9</title></head><body>x</body></html>"
    ).encode("latin-1")
    facts = _facts(body, charset="ISO-8859-1")
    assert facts["title"] == "Caf\u00e9"
