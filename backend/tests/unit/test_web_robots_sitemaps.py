"""Offline unit tests for robots.txt + sitemap parsing (Task 3).

Robots: allow/disallow, wildcards, crawl-delay clamping, sitemap directives,
and fail-open behavior on empty/malformed bodies. Sitemaps: urlset + index
parsing, namespace-agnostic tags, gzip handling, decoded-byte / gzip bomb
caps, malformed / entity-attack XML rejection, and the bounded, loop-safe
``SitemapCollector`` (depth cap + visited-set so a recursive index can't loop).
All pure parsing — no network.
"""

from __future__ import annotations

import gzip

import pytest

from app.connectors.web_evidence.robots import RobotsPolicy
from app.connectors.web_evidence.sitemaps import (
    SitemapCollector,
    SitemapParseError,
    maybe_gunzip,
    parse_sitemap_document,
)
from app.core.config.site_health import site_health_settings

_UA = "SearchifySiteHealthBot/1.0"


# --- robots ---------------------------------------------------------------


def test_robots_disallow_blocks_matching_path():
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /private/\n", user_agent=_UA)
    assert not policy.can_fetch("https://example.com/private/x")
    assert policy.can_fetch("https://example.com/public/x")


def test_robots_wildcard_disallow():
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /*.pdf$\n", user_agent=_UA)
    assert not policy.can_fetch("https://example.com/doc.pdf")
    assert policy.can_fetch("https://example.com/page.html")


def test_robots_empty_body_is_allow_all():
    policy = RobotsPolicy.parse("   ", user_agent=_UA)
    assert policy.can_fetch("https://example.com/anything")


def test_robots_allow_all_classmethod():
    policy = RobotsPolicy.allow_all(user_agent=_UA)
    assert policy.can_fetch("https://example.com/x")
    assert policy.sitemaps() == []


def test_robots_crawl_delay_is_clamped_to_max():
    huge = site_health_settings.max_crawl_delay_seconds + 1000
    policy = RobotsPolicy.parse(
        f"User-agent: *\nCrawl-delay: {int(huge)}\n", user_agent=_UA
    )
    assert policy.crawl_delay() == site_health_settings.max_crawl_delay_seconds


def test_robots_crawl_delay_default_when_absent():
    policy = RobotsPolicy.parse("User-agent: *\nDisallow:\n", user_agent=_UA)
    assert policy.crawl_delay() == site_health_settings.default_crawl_delay_seconds


def test_robots_malformed_parser_fails_open(monkeypatch):
    """A robots body that makes the underlying parser raise fails open.

    ``Protego.parse`` throwing on some pathological input must not crash
    discovery: the resulting policy allows every URL, exactly like an
    empty/unfetchable robots.txt.
    """

    def _raise(_text: str):
        raise ValueError("boom")

    monkeypatch.setattr(
        "app.connectors.web_evidence.robots.Protego.parse", staticmethod(_raise)
    )
    policy = RobotsPolicy.parse("User-agent: *\nDisallow: /private/\n", user_agent=_UA)
    assert policy.can_fetch("https://example.com/private/x")
    assert policy.can_fetch("https://example.com/anything")


def test_robots_declares_sitemaps():
    body = "User-agent: *\nDisallow:\nSitemap: https://example.com/sitemap.xml\n"
    policy = RobotsPolicy.parse(body, user_agent=_UA)
    assert "https://example.com/sitemap.xml" in policy.sitemaps()


# --- sitemap parsing ------------------------------------------------------


def test_parse_urlset_returns_page_urls():
    xml = (
        b'<?xml version="1.0"?>'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<url><loc>https://example.com/a</loc></url>"
        b"<url><loc>https://example.com/b</loc></url>"
        b"</urlset>"
    )
    doc = parse_sitemap_document(xml)
    assert not doc.is_index
    assert doc.urls == ["https://example.com/a", "https://example.com/b"]
    assert doc.sitemap_refs == []


def test_parse_sitemapindex_returns_child_refs():
    xml = (
        b'<?xml version="1.0"?>'
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://example.com/s1.xml</loc></sitemap>"
        b"<sitemap><loc>https://example.com/s2.xml</loc></sitemap>"
        b"</sitemapindex>"
    )
    doc = parse_sitemap_document(xml)
    assert doc.is_index
    assert doc.sitemap_refs == [
        "https://example.com/s1.xml",
        "https://example.com/s2.xml",
    ]


def test_parse_sitemap_without_namespace():
    xml = b"<urlset><url><loc>https://example.com/a</loc></url></urlset>"
    doc = parse_sitemap_document(xml)
    assert doc.urls == ["https://example.com/a"]


def test_parse_malformed_xml_raises():
    with pytest.raises(SitemapParseError):
        parse_sitemap_document(b"<urlset><url><loc>oops")


def test_parse_entity_expansion_attack_rejected():
    # A billion-laughs style entity payload must be rejected by defusedxml.
    xml = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;">]>'
        b"<urlset><url><loc>&lol2;</loc></url></urlset>"
    )
    with pytest.raises(SitemapParseError):
        parse_sitemap_document(xml)


# --- gzip / caps ----------------------------------------------------------


def test_maybe_gunzip_decompresses_gzip_body():
    raw = b"<urlset></urlset>"
    body = gzip.compress(raw)
    assert maybe_gunzip(body, content_type="application/gzip") == raw


def test_maybe_gunzip_passes_through_plain_body():
    raw = b"<urlset></urlset>"
    assert maybe_gunzip(raw) == raw


def test_maybe_gunzip_plain_body_over_cap_raises(monkeypatch):
    monkeypatch.setattr(site_health_settings, "max_sitemap_decoded_bytes", 10)
    with pytest.raises(SitemapParseError):
        maybe_gunzip(b"x" * 100)


def test_maybe_gunzip_bomb_over_cap_raises(monkeypatch):
    monkeypatch.setattr(site_health_settings, "max_sitemap_decoded_bytes", 1000)
    body = gzip.compress(b"A" * 100_000)
    with pytest.raises(SitemapParseError):
        maybe_gunzip(body, content_type="gzip")


def test_parse_caps_urls(monkeypatch):
    monkeypatch.setattr(site_health_settings, "max_sitemap_urls", 2)
    xml = (
        b"<urlset>"
        b"<url><loc>https://example.com/a</loc></url>"
        b"<url><loc>https://example.com/b</loc></url>"
        b"<url><loc>https://example.com/c</loc></url>"
        b"</urlset>"
    )
    doc = parse_sitemap_document(xml)
    assert len(doc.urls) == 2


# --- SitemapCollector (loop-safe recursion) -------------------------------


def _index(refs: list[str]) -> bytes:
    inner = "".join(f"<sitemap><loc>{r}</loc></sitemap>" for r in refs)
    return f"<sitemapindex>{inner}</sitemapindex>".encode()


def _urlset(urls: list[str]) -> bytes:
    inner = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f"<urlset>{inner}</urlset>".encode()


def test_collector_returns_child_refs_within_depth():
    collector = SitemapCollector()
    refs = collector.add_document(
        "https://example.com/root.xml",
        _index(["https://example.com/child.xml"]),
        depth=0,
    )
    assert refs == ["https://example.com/child.xml"]


def test_collector_self_reference_does_not_loop():
    collector = SitemapCollector()
    # An index that references itself -> visited-set drops the self ref.
    refs = collector.add_document(
        "https://example.com/root.xml",
        _index(["https://example.com/root.xml"]),
        depth=0,
    )
    assert refs == []


def test_collector_stops_returning_refs_at_depth_cap():
    collector = SitemapCollector()
    max_depth = site_health_settings.max_sitemap_index_depth
    refs = collector.add_document(
        "https://example.com/deep.xml",
        _index(["https://example.com/deeper.xml"]),
        depth=max_depth,
    )
    assert refs == []


def test_collector_accumulates_urls():
    collector = SitemapCollector()
    collector.add_document(
        "https://example.com/s1.xml",
        _urlset(["https://example.com/a", "https://example.com/b"]),
        depth=1,
    )
    assert collector.url_count == 2
    assert "https://example.com/a" in collector.urls
