"""Offline unit tests for the Site Health URL policy (Task 3).

Covers canonicalization, offline-PSL registrable-domain + scope boundaries,
include/exclude glob narrowing (exclusions win), SSRF address validation for
every unsafe IPv4/IPv6 class, mixed / rebinding DNS answers, and per-hop
``resolve_target`` (scope + DNS + pinning). No network: the PSL extractor is
pinned offline and DNS goes through an injected fake resolver.
"""

from __future__ import annotations

import pytest

from app.connectors.web_evidence.contracts import FetchError
from app.connectors.web_evidence.url_policy import (
    UrlPolicyError,
    canonicalize,
    is_admissible,
    is_in_scope,
    narrow,
    pick_connect_ip,
    registrable_domain,
    resolve_target,
    split_host_port,
    validate_address,
)


class _FakeResolver:
    """A fake DNS resolver returning a fixed answer per host (offline)."""

    def __init__(self, mapping: dict[str, list[str]], *, default=None) -> None:
        self._mapping = mapping
        self._default = default

    async def resolve(self, host: str, port: int) -> list[str]:
        if host in self._mapping:
            return list(self._mapping[host])
        if self._default is not None:
            return list(self._default)
        raise OSError(f"no answer for {host}")


# --- canonicalize ---------------------------------------------------------


def test_canonicalize_lowercases_scheme_host_strips_default_port_and_frag():
    assert (
        canonicalize("HTTP://Example.com:80/a/b?z=1#frag")
        == "http://example.com/a/b?z=1"
    )


def test_canonicalize_strips_tracking_params_and_sorts_query():
    out = canonicalize("https://example.com/p?b=2&utm_source=x&a=1")
    assert out == "https://example.com/p?a=1&b=2"


def test_canonicalize_drops_query_when_only_tracking_params():
    assert canonicalize("https://example.com/p?utm_source=x") == (
        "https://example.com/p"
    )


def test_canonicalize_empty_path_becomes_slash():
    assert canonicalize("https://example.com") == "https://example.com/"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "",
    ],
)
def test_canonicalize_rejects_non_http_schemes(url):
    with pytest.raises(UrlPolicyError):
        canonicalize(url)


def test_canonicalize_rejects_userinfo_credentials():
    with pytest.raises(UrlPolicyError):
        canonicalize("http://user:pass@example.com/")


@pytest.mark.parametrize("port", [8080, 22, 3306])
def test_canonicalize_rejects_disallowed_ports(port):
    with pytest.raises(UrlPolicyError):
        canonicalize(f"http://example.com:{port}/")


def test_canonicalize_allows_explicit_allowed_ports():
    assert canonicalize("https://example.com:443/") == "https://example.com/"


def test_canonicalize_resolves_relative_against_base():
    out = canonicalize("../c", base_url="https://example.com/a/b")
    assert out == "https://example.com/c"


# --- registrable domain + scope (offline PSL) -----------------------------


@pytest.mark.parametrize(
    "host,expected",
    [
        ("www.example.com", "example.com"),
        ("a.b.example.co.uk", "example.co.uk"),
        ("example.com", "example.com"),
        ("http://deep.sub.example.com/x", "example.com"),
    ],
)
def test_registrable_domain(host, expected):
    assert registrable_domain(host) == expected


def test_in_scope_root_and_subdomain_true():
    assert is_in_scope("https://example.com/x", "example.com")
    assert is_in_scope("https://blog.example.com/x", "example.com")


def test_in_scope_sibling_and_attacker_domain_false():
    # A sibling registrable domain and an attacker domain that merely embeds
    # the root as a substring are both out of scope.
    assert not is_in_scope("https://example.org/x", "example.com")
    assert not is_in_scope("https://example.com.evil.com/x", "example.com")
    assert not is_in_scope("https://notexample.com/x", "example.com")


def test_in_scope_respects_public_suffix_boundary():
    # example.co.uk is the registrable domain; a sibling under co.uk is out.
    assert is_in_scope("https://a.example.co.uk/x", "example.co.uk")
    assert not is_in_scope("https://other.co.uk/x", "example.co.uk")


# --- narrowing (globs) ----------------------------------------------------


def test_narrow_empty_include_allows_all():
    assert narrow("https://example.com/x", include_globs=[], exclude_globs=[])


def test_narrow_include_requires_match():
    assert narrow(
        "https://example.com/blog/1",
        include_globs=["*/blog/*"],
        exclude_globs=[],
    )
    assert not narrow(
        "https://example.com/shop/1",
        include_globs=["*/blog/*"],
        exclude_globs=[],
    )


def test_narrow_exclusions_win_over_includes():
    # A URL matching BOTH an include and an exclude is rejected (exclusions win).
    assert not narrow(
        "https://example.com/blog/private",
        include_globs=["*/blog/*"],
        exclude_globs=["*/private*"],
    )


def test_is_admissible_combines_scope_and_narrowing():
    assert is_admissible(
        "https://example.com/blog/1",
        root_registrable_domain="example.com",
        include_globs=["*/blog/*"],
        exclude_globs=[],
    )
    # In scope but excluded.
    assert not is_admissible(
        "https://example.com/blog/1",
        root_registrable_domain="example.com",
        include_globs=[],
        exclude_globs=["*/blog/*"],
    )
    # Out of scope (glob can never authorize another registrable domain).
    assert not is_admissible(
        "https://evil.com/blog/1",
        root_registrable_domain="example.com",
        include_globs=["*"],
        exclude_globs=[],
    )


# --- SSRF address validation ----------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # private
        "192.168.1.1",  # private
        "172.16.0.1",  # private
        "169.254.169.254",  # cloud metadata / link-local
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fd00::1",  # IPv6 unique-local (private)
        "fd00:ec2::254",  # IPv6 cloud metadata
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "not-an-ip",  # unparseable
    ],
)
def test_validate_address_rejects_unsafe(ip):
    with pytest.raises(FetchError):
        validate_address(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
def test_validate_address_allows_public(ip):
    # Does not raise for a globally-routable public address.
    validate_address(ip)


def test_pick_connect_ip_rejects_when_any_answer_unsafe():
    # Mixed public + private answers -> reject the whole target (rebinding).
    with pytest.raises(FetchError):
        pick_connect_ip(["93.184.216.34", "10.0.0.1"])


def test_pick_connect_ip_pins_first_safe_ip():
    connect_ip, safe = pick_connect_ip(["93.184.216.34", "93.184.216.35"])
    assert connect_ip == "93.184.216.34"
    assert safe == ("93.184.216.34", "93.184.216.35")


def test_pick_connect_ip_empty_raises_dns_error():
    with pytest.raises(FetchError) as exc:
        pick_connect_ip([])
    assert exc.value.error_code == "dns_resolution_failed"


# --- resolve_target (per-hop gate) ----------------------------------------


async def test_resolve_target_public_host_pins_ip():
    resolver = _FakeResolver({"example.com": ["93.184.216.34"]})
    target = await resolve_target(
        "https://example.com/x",
        resolver=resolver,
        root_registrable_domain="example.com",
        enforce_scope=True,
    )
    assert target.host == "example.com"
    assert target.connect_ip == "93.184.216.34"
    assert target.url == "https://example.com/x"


async def test_resolve_target_out_of_scope_raises_policy_error():
    resolver = _FakeResolver({"evil.com": ["93.184.216.34"]})
    with pytest.raises(UrlPolicyError):
        await resolve_target(
            "https://evil.com/x",
            resolver=resolver,
            root_registrable_domain="example.com",
            enforce_scope=True,
        )


async def test_resolve_target_dns_failure_is_classified():
    resolver = _FakeResolver({})  # raises for every host
    with pytest.raises(FetchError) as exc:
        await resolve_target(
            "https://example.com/x",
            resolver=resolver,
            enforce_scope=False,
        )
    assert exc.value.error_code == "dns_resolution_failed"


async def test_resolve_target_rebinding_mixed_answer_blocked():
    # DNS returns a safe + an unsafe (private) IP: whole target rejected.
    resolver = _FakeResolver({"example.com": ["93.184.216.34", "127.0.0.1"]})
    with pytest.raises(FetchError) as exc:
        await resolve_target(
            "https://example.com/x",
            resolver=resolver,
            enforce_scope=False,
        )
    assert exc.value.error_code == "ssrf_blocked"


async def test_resolve_target_private_only_answer_blocked():
    resolver = _FakeResolver({"internal.example.com": ["10.1.2.3"]})
    with pytest.raises(FetchError) as exc:
        await resolve_target(
            "https://internal.example.com/x",
            resolver=resolver,
            enforce_scope=False,
        )
    assert exc.value.error_code == "ssrf_blocked"


def test_split_host_port_defaults_by_scheme():
    assert split_host_port("https://example.com/x") == ("example.com", 443)
    assert split_host_port("http://example.com/x") == ("example.com", 80)
