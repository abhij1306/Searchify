# Provider-neutral contracts for the secure web-evidence fetcher (Task 3).
#
# These are the transport-agnostic value types + protocols the Site Health
# crawler's URL policy, SSRF-safe fetcher, robots parser, and sitemap parser
# share. Everything here is immutable (frozen dataclasses) so a fetch result is
# safe to pass across the worker without accidental mutation, and there is NO
# raw HTML body field persisted anywhere downstream — only bounded decoded
# bytes handed to the parser in-process.
#
# The DNS resolver is a Protocol so the worker injects a real resolver in
# production and tests inject a fake one (no live internet — subplan test
# contract). The connection IP is pinned by the policy after validation so the
# fetcher connects to exactly the address that passed the SSRF checks while
# preserving the original Host header + TLS SNI.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """A URL that passed canonicalization, scope, DNS, and SSRF validation.

    ``connect_ip`` is the single validated address the fetcher must dial; the
    original ``host``/``port`` are preserved so the request still sends the
    correct ``Host`` header and TLS SNI (DNS-rebinding protection: we never
    re-resolve the host at connect time).
    """

    url: str
    scheme: str
    host: str
    port: int
    connect_ip: str
    # Every resolved address that passed validation (diagnostic; connect_ip is
    # the one dialed). Empty when the resolver returned nothing.
    resolved_ips: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FetchRequest:
    """One fetch to perform. ``purpose`` is a config FETCH_PURPOSE_* token."""

    url: str
    purpose: str
    method: str = "GET"
    # Extra request headers (merged over the fetcher's defaults). Never carries
    # credentials — the policy rejects userinfo before a request is built.
    headers: dict[str, str] = field(default_factory=dict)
    # Per-request overrides; None means "use the fetcher's configured value".
    max_wire_bytes: int | None = None
    max_decoded_bytes: int | None = None
    timeout_seconds: float | None = None
    max_redirects: int | None = None
    # Content types this request accepts; empty means the fetcher's default
    # allowlist for the purpose.
    allowed_content_types: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RedirectHop:
    """One re-validated redirect hop (URLs only — never credentials)."""

    from_url: str
    to_url: str
    status_code: int


@dataclass(frozen=True, slots=True)
class FetchResult:
    """The bounded, redacted outcome of one successful fetch.

    ``body`` holds the decoded bytes (already capped) for in-process parsing;
    it is never persisted as-is. ``redacted_headers`` contains only the config
    allowlist. ``redirect_chain`` records every re-validated hop.
    """

    requested_url: str
    final_url: str
    status_code: int
    redacted_headers: dict[str, str]
    content_type: str
    http_version: str
    body: bytes
    wire_bytes: int
    decoded_bytes: int
    ttfb_ms: int | None
    latency_ms: int | None
    redirect_chain: tuple[RedirectHop, ...] = ()


class FetchError(Exception):
    """A classified fetch failure carrying a safe error token.

    ``error_code`` is one of the config ``SITE_FETCH_ERROR_TOKENS`` (e.g.
    ``ssrf_blocked``, ``redirect_limit``, ``response_too_large``, ``timeout``).
    The message is safe for logs/diagnostics: it never contains a raw body or a
    sensitive header. ``status_code``/``retry_after_seconds`` are populated when
    known (HTTP errors).
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.retryable = retryable


@runtime_checkable
class DnsResolver(Protocol):
    """Async host -> IP resolver. Injected so tests never hit the network."""

    async def resolve(self, host: str, port: int) -> list[str]:
        """Return the resolved IP address strings for ``host``.

        May return IPv4 and/or IPv6 literals. An empty list (or a raised
        exception) means resolution failed; the policy treats that as
        ``dns_resolution_failed``.
        """
        ...
