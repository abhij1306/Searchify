# URL policy: canonicalization, scope, glob narrowing, and SSRF address rules.
#
# The single owner of "is this URL allowed, and what exactly may we connect
# to?" for the Site Health crawler. Split into pure, deterministic pieces so
# every one is unit-testable without a network:
#
#   - canonicalize(): scheme lowercase, IDNA host, strip fragment, strip
#     default ports, config-owned tracking-query removal + query sort. Rejects
#     non-HTTP(S), userinfo (credentials-in-URL), and disallowed ports.
#   - registrable_domain()/is_in_scope(): offline tldextract PSL — a URL is
#     in-scope only when its host equals the root registrable domain or is a
#     subdomain of it (host == domain or endswith "." + domain).
#   - narrow(): include/exclude glob narrowing applied AFTER canonicalization;
#     empty include = all in-scope URLs; any exclude match rejects (exclusions
#     win); globs never authorize another registrable domain.
#   - validate_address()/pick_connect_ip(): reject every unsafe IP class
#     (loopback, private, link-local, multicast, reserved, unspecified,
#     IPv4-mapped IPv6, and cloud-metadata 169.254.169.254 / fd00:ec2::254),
#     then pin a single validated connection IP (DNS-rebinding protection).
#   - resolve_target(): canonicalize -> scope -> DNS via the injected resolver
#     -> address validation -> pinned ResolvedTarget, re-runnable per redirect
#     hop so a redirect to a private/out-of-scope/excluded URL is rejected.
#
# Config (schemes/ports/tracking params) lives in ``config/site_health`` — this
# module never hard-codes those literals (invariant 1).
from __future__ import annotations

import fnmatch
import ipaddress
from urllib.parse import (
    parse_qsl,
    quote,
    unquote,
    urlencode,
    urlsplit,
    urlunsplit,
)

import tldextract

from app.connectors.web_evidence.contracts import (
    DnsResolver,
    FetchError,
    ResolvedTarget,
)
from app.core.config.site_health import (
    ALLOWED_URL_PORTS,
    ALLOWED_URL_SCHEMES,
    ERROR_DNS_RESOLUTION_FAILED,
    ERROR_SSRF_BLOCKED,
    TRACKING_QUERY_PARAMS,
)

# Offline Public Suffix List extractor: ``suffix_list_urls=()`` forces
# tldextract to use its bundled snapshot and NEVER fetch the PSL at runtime
# (subplan: offline PSL, no runtime network access). ``cache_dir=None`` keeps
# it from writing/reading a live cache.
_PSL = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

# Default (scheme -> port) map for stripping redundant default ports.
_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}

# Cloud metadata endpoints that must never be reachable (link-local already
# covers 169.254.0.0/16, but pin the canonical addresses explicitly too).
_METADATA_IPS: frozenset[str] = frozenset(
    {
        "169.254.169.254",
        "fd00:ec2::254",
    }
)


class UrlPolicyError(ValueError):
    """A URL was rejected by canonicalization/scope/narrowing (not SSRF)."""


def _idna_host(host: str) -> str:
    """Normalize a host to lowercase IDNA (punycode) ASCII form.

    Falls back to a lowercase string when the host is already ASCII or IDNA
    encoding is not applicable, so we never raise on an odd-but-safe host.
    """
    host = host.strip().rstrip(".").lower()
    if not host:
        return ""
    try:
        return host.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return host


def _normalize_query(query: str) -> str:
    """Drop config-owned tracking params and sort remaining pairs.

    Deterministic (sorted) so the same logical URL always canonicalizes to one
    identity regardless of query-parameter ordering (invariant 9).
    """
    if not query:
        return ""
    pairs = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_PARAMS
    ]
    if not pairs:
        return ""
    pairs.sort()
    return urlencode(pairs, doseq=True)


def _normalize_path(path: str) -> str:
    """Percent-encode consistently and collapse an empty path to '/'."""
    if not path:
        return "/"
    # Re-encode to a canonical form: decode then re-quote reserved-safe.
    return quote(unquote(path), safe="/%:@!$&'()*+,;=~-._")


def canonicalize(url: str, *, base_url: str | None = None) -> str:
    """Return the canonical identity of ``url`` or raise ``UrlPolicyError``.

    Applies, in order: relative resolution against ``base_url`` (if given),
    scheme lowercase + HTTP(S)-only check, userinfo rejection, IDNA host, port
    validation + default-port stripping, fragment removal, path canonicalization,
    and config-owned query normalization. The result never carries a fragment,
    userinfo, a default port, or a tracking parameter.
    """
    raw = str(url or "").strip()
    if not raw:
        raise UrlPolicyError("empty URL")

    if base_url:
        raw = _resolve_relative(base_url, raw)

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise UrlPolicyError(f"scheme not allowed: {scheme or '(none)'}")

    # Reject credentials-in-URL (userinfo) outright.
    if parts.username is not None or parts.password is not None or (
        "@" in parts.netloc
    ):
        raise UrlPolicyError("userinfo (credentials) not allowed in URL")

    host = _idna_host(parts.hostname or "")
    if not host:
        raise UrlPolicyError("missing host")

    port = parts.port
    if port is None:
        port = _DEFAULT_PORTS.get(scheme)
    if port is None or int(port) not in ALLOWED_URL_PORTS:
        raise UrlPolicyError(f"port not allowed: {port}")

    # Strip the port from netloc when it is the scheme default.
    netloc = host
    if _DEFAULT_PORTS.get(scheme) != int(port):
        netloc = f"{host}:{int(port)}"

    path = _normalize_path(parts.path)
    query = _normalize_query(parts.query)
    # Fragment always dropped.
    return urlunsplit((scheme, netloc, path, query, ""))


def _resolve_relative(base_url: str, ref: str) -> str:
    from urllib.parse import urljoin

    return urljoin(base_url, ref)


def split_host_port(url: str) -> tuple[str, int]:
    """Return the (host, port) of a canonical URL (port defaulted by scheme)."""
    parts = urlsplit(url)
    host = _idna_host(parts.hostname or "")
    port = parts.port or _DEFAULT_PORTS.get(parts.scheme.lower(), 0)
    return host, int(port)


def registrable_domain(host_or_url: str) -> str:
    """Return the offline-PSL registrable domain for a host or URL.

    ``a.b.example.co.uk`` -> ``example.co.uk``; ``example.com`` -> ``example.com``.
    Returns "" when there is no registrable domain (e.g. a bare TLD or an IP).
    """
    value = str(host_or_url or "").strip()
    if "://" in value:
        value = urlsplit(value).hostname or ""
    value = value.strip().rstrip(".").lower()
    if not value:
        return ""
    extracted = _PSL(value)
    if not extracted.domain or not extracted.suffix:
        return ""
    return f"{extracted.domain}.{extracted.suffix}"


def is_in_scope(url: str, root_registrable_domain: str) -> bool:
    """True when ``url``'s host is the root registrable domain or a subdomain.

    Scope = the primary registrable domain PLUS all subdomains. A sibling or
    attacker domain (different registrable domain) is out of scope even if it
    shares a suffix. A public-suffix boundary (``example.co.uk``) is respected
    because the comparison is against the registrable domain, not a naive
    suffix match.
    """
    root = str(root_registrable_domain or "").strip().rstrip(".").lower()
    if not root:
        return False
    try:
        host, _port = split_host_port(url)
    except ValueError:
        return False
    if not host:
        return False
    return host == root or host.endswith("." + root)


def _normalize_globs(globs: list[str] | None) -> list[str]:
    return [str(g).strip() for g in (globs or []) if str(g).strip()]


def narrow(
    url: str,
    *,
    include_globs: list[str] | None,
    exclude_globs: list[str] | None,
) -> bool:
    """Apply include/exclude glob narrowing to a CANONICAL absolute URL.

    Semantics (subplan): an empty include list means "all in-scope URLs";
    otherwise at least one include glob must match. Any exclude glob match
    rejects the URL (exclusions win over includes). Globs only NARROW — scope
    is enforced separately, so a glob can never authorize another registrable
    domain.
    """
    includes = _normalize_globs(include_globs)
    excludes = _normalize_globs(exclude_globs)
    for pattern in excludes:
        if fnmatch.fnmatch(url, pattern):
            return False
    if not includes:
        return True
    return any(fnmatch.fnmatch(url, pattern) for pattern in includes)


def is_admissible(
    url: str,
    *,
    root_registrable_domain: str,
    include_globs: list[str] | None,
    exclude_globs: list[str] | None,
) -> bool:
    """Scope + narrowing gate for a canonical URL (does NOT do DNS/SSRF)."""
    if not is_in_scope(url, root_registrable_domain):
        return False
    return narrow(
        url, include_globs=include_globs, exclude_globs=exclude_globs
    )


def _is_unsafe_ip(ip: ipaddress._BaseAddress) -> bool:
    """True when an address is in any class the crawler must never connect to.

    Covers loopback, private, link-local, multicast, reserved, unspecified,
    and (for IPv6) IPv4-mapped addresses whose embedded IPv4 is itself unsafe,
    plus the pinned cloud-metadata addresses.
    """
    if str(ip) in _METADATA_IPS:
        return True
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    # A globally-routable check as a final gate (catches other non-global).
    if not ip.is_global:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        # IPv4-mapped (::ffff:a.b.c.d) and 6to4/teredo embed an IPv4 address
        # that must be re-checked so a mapped private/metadata IP is caught.
        mapped = ip.ipv4_mapped
        if mapped is not None and _is_unsafe_ip(mapped):
            return True
    return False


def validate_address(ip_text: str) -> ipaddress._BaseAddress:
    """Parse + SSRF-validate a single IP literal. Raises ``FetchError``.

    Raises ``FetchError(ssrf_blocked)`` for an unparseable literal or any
    unsafe address class. Returns the parsed address on success.
    """
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError as exc:
        raise FetchError(
            "unparseable resolved address",
            error_code=ERROR_SSRF_BLOCKED,
        ) from exc
    if _is_unsafe_ip(ip):
        raise FetchError(
            f"blocked unsafe address class for {ip.version}",
            error_code=ERROR_SSRF_BLOCKED,
        )
    return ip


def pick_connect_ip(resolved_ips: list[str]) -> tuple[str, tuple[str, ...]]:
    """Validate EVERY resolved address; return one pinned IP + the safe set.

    Rejects the whole target if ANY returned address is unsafe (mixed
    public/private answers are treated as hostile — a rebinding attacker can
    return one safe + one unsafe answer). The pinned IP is deterministic (the
    first resolved address) so the connection dials exactly a validated
    address.
    """
    if not resolved_ips:
        raise FetchError(
            "no addresses resolved", error_code=ERROR_DNS_RESOLUTION_FAILED
        )
    safe: list[str] = []
    for ip_text in resolved_ips:
        validate_address(ip_text)  # raises on any unsafe address
        safe.append(str(ipaddress.ip_address(ip_text)))
    return safe[0], tuple(safe)


async def resolve_target(
    url: str,
    *,
    resolver: DnsResolver,
    root_registrable_domain: str | None = None,
    include_globs: list[str] | None = None,
    exclude_globs: list[str] | None = None,
    enforce_scope: bool = True,
) -> ResolvedTarget:
    """Canonicalize, scope-check, resolve DNS, SSRF-validate, and pin an IP.

    This is the per-hop gate the fetcher runs on the initial URL AND on every
    redirect target. When ``enforce_scope`` is True, an out-of-scope or
    glob-excluded URL raises ``UrlPolicyError`` (a redirect that escapes scope
    is rejected). DNS goes through the injected resolver only.
    """
    canonical = canonicalize(url)
    if enforce_scope and root_registrable_domain:
        if not is_admissible(
            canonical,
            root_registrable_domain=root_registrable_domain,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        ):
            raise UrlPolicyError(f"URL out of scope/narrowing: {canonical}")

    host, port = split_host_port(canonical)
    try:
        resolved = await resolver.resolve(host, port)
    except Exception as exc:  # resolver failure -> classified DNS error
        raise FetchError(
            "DNS resolution failed",
            error_code=ERROR_DNS_RESOLUTION_FAILED,
        ) from exc
    connect_ip, safe_ips = pick_connect_ip(list(resolved or []))
    parts = urlsplit(canonical)
    return ResolvedTarget(
        url=canonical,
        scheme=parts.scheme.lower(),
        host=host,
        port=port,
        connect_ip=connect_ip,
        resolved_ips=safe_ips,
    )
