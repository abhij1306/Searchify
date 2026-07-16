# SSRF-safe async HTTP fetcher for the Site Health crawler (Task 3).
#
# Every safety property the plan requires lives here:
#   - trust_env=False (never read proxy/CA env of the host).
#   - MANUAL redirects only (follow_redirects=False): each hop is re-validated
#     through ``url_policy.resolve_target`` (scheme/port/userinfo/scope/DNS/
#     SSRF), so a redirect to a private/loopback/out-of-scope URL is rejected.
#   - A validated connection IP is PINNED for the dial while the original Host
#     header + TLS SNI are preserved (DNS-rebinding protection: we never let
#     the socket re-resolve the hostname).
#   - Independent wire-byte and DECODED-byte caps enforced while streaming, so
#     an oversized response OR a compression bomb aborts before it is buffered
#     or parsed (we decompress incrementally and measure output).
#   - Response headers redacted to the config allowlist (no cookies/auth).
#   - Per-request timeout and a redirect-count cap.
#
# The DNS resolver + (optionally) the httpx transport are injected so tests run
# entirely offline with a fake resolver and ``httpx.MockTransport`` (no live
# internet — subplan test contract). There is NO raw-body persistence: the
# decoded bytes are handed back in-process for bounded parsing only.
from __future__ import annotations

import time
import zlib
from urllib.parse import urlsplit

import httpx

from app.connectors.web_evidence.contracts import (
    DnsResolver,
    FetchError,
    FetchRequest,
    FetchResult,
    RedirectHop,
    ResolvedTarget,
)
from app.connectors.web_evidence.url_policy import (
    UrlPolicyError,
    resolve_target,
)
from app.core.config.site_health import (
    ERROR_REDIRECT_LIMIT,
    ERROR_RESPONSE_TOO_LARGE,
    ERROR_SSRF_BLOCKED,
    ERROR_TIMEOUT,
    ERROR_UNSUPPORTED_CONTENT_TYPE,
    PERSISTED_RESPONSE_HEADERS,
    site_health_settings,
)

_REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})


def redact_headers(headers: httpx.Headers | dict) -> dict[str, str]:
    """Keep only the config-allowlisted response headers (lowercased keys).

    Everything else (Set-Cookie, Authorization echoes, etc.) is dropped so no
    sensitive header is ever persisted or logged.
    """
    out: dict[str, str] = {}
    items = headers.items() if hasattr(headers, "items") else []
    for key, value in items:
        lk = str(key).lower()
        if lk in PERSISTED_RESPONSE_HEADERS:
            out[lk] = str(value)
    return out


def _content_type(headers: httpx.Headers) -> str:
    raw = headers.get("content-type", "")
    return str(raw).split(";", 1)[0].strip().lower()


def _incremental_decoder(content_encoding: str):
    """Return a callable(chunk)->bytes decompressor for the wire encoding.

    Supports gzip and deflate (the encodings a compression bomb would use);
    ``identity``/unknown pass bytes through unchanged. brotli is not a
    dependency, so a ``br`` body is treated as opaque wire bytes (the wire cap
    still bounds it).
    """
    enc = str(content_encoding or "").strip().lower()
    if enc == "gzip":
        obj = zlib.decompressobj(16 + zlib.MAX_WBITS)
        return lambda chunk: obj.decompress(chunk)
    if enc == "deflate":
        obj = zlib.decompressobj()
        return lambda chunk: obj.decompress(chunk)
    return lambda chunk: chunk


class SecureFetcher:
    """Shared SSRF-safe fetcher over one ``httpx.AsyncClient``.

    Construct with the injected DNS ``resolver`` and, optionally, an httpx
    ``transport`` (tests pass ``httpx.MockTransport``). When a transport is
    injected the fetcher sends to the canonical URL as-is (so the mock can match
    it); in production (no transport) it pins the validated connection IP while
    preserving Host + SNI.
    """

    def __init__(
        self,
        *,
        resolver: DnsResolver,
        transport: httpx.AsyncBaseTransport | None = None,
        settings=site_health_settings,
        user_agent: str = "SearchifySiteHealthBot/1.0 (+https://searchify)",
    ) -> None:
        self._resolver = resolver
        self._settings = settings
        self._user_agent = user_agent
        self._injected_transport = transport
        # In production we pin the IP ourselves, so the transport must never
        # re-resolve or read the host environment (invariant: trust_env=False).
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            headers={"user-agent": user_agent},
        )
        self._pin_ip = transport is None

    async def __aenter__(self) -> SecureFetcher:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _limits(self, request: FetchRequest) -> tuple[int, int, float, int]:
        s = self._settings
        return (
            request.max_wire_bytes or s.max_response_wire_bytes,
            request.max_decoded_bytes or s.max_response_decoded_bytes,
            request.timeout_seconds or s.request_timeout_seconds,
            request.max_redirects if request.max_redirects is not None
            else s.max_redirects,
        )

    def _build_httpx_request(
        self,
        *,
        method: str,
        target: ResolvedTarget,
        extra_headers: dict[str, str],
        timeout: float,
    ) -> httpx.Request:
        headers = dict(extra_headers)
        if self._pin_ip:
            # Dial the pinned, validated IP but keep Host + SNI = original host
            # (DNS-rebinding protection). httpcore uses the sni_hostname
            # extension for the TLS handshake.
            parts = urlsplit(target.url)
            host_header = target.host
            if target.port not in (80, 443):
                host_header = f"{target.host}:{target.port}"
            ip_literal = (
                f"[{target.connect_ip}]"
                if ":" in target.connect_ip
                else target.connect_ip
            )
            dial_url = parts._replace(
                netloc=f"{ip_literal}:{target.port}"
            ).geturl()
            headers["host"] = host_header
            return self._client.build_request(
                method,
                dial_url,
                headers=headers,
                timeout=timeout,
                extensions={"sni_hostname": target.host},
            )
        return self._client.build_request(
            method, target.url, headers=headers, timeout=timeout
        )

    async def fetch(
        self,
        request: FetchRequest,
        *,
        root_registrable_domain: str | None = None,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        enforce_scope: bool = False,
    ) -> FetchResult:
        """Fetch ``request.url`` with full SSRF + size + redirect enforcement.

        Re-validates the initial URL and every redirect hop. Returns a bounded,
        redacted ``FetchResult`` (including 4xx/5xx — the caller classifies the
        status); raises ``FetchError`` with a safe token for SSRF, redirect
        limit, oversize, unsupported content type, or timeout.
        """
        (
            max_wire,
            max_decoded,
            timeout,
            max_redirects,
        ) = self._limits(request)

        current_url = request.url
        redirect_chain: list[RedirectHop] = []
        started = time.monotonic()

        for hop in range(max_redirects + 1):
            target = await self._resolve(
                current_url,
                root_registrable_domain=root_registrable_domain,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                enforce_scope=enforce_scope,
            )
            httpx_request = self._build_httpx_request(
                method=request.method,
                target=target,
                extra_headers=request.headers,
                timeout=timeout,
            )
            try:
                response = await self._client.send(
                    httpx_request, stream=True
                )
            except httpx.TimeoutException as exc:
                raise FetchError(
                    "request timed out", error_code=ERROR_TIMEOUT,
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                # Network-level failure: classify as SSRF-adjacent connection
                # error but keep the message safe.
                raise FetchError(
                    f"connection error: {type(exc).__name__}",
                    error_code=ERROR_SSRF_BLOCKED,
                    retryable=True,
                ) from exc

            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    # A redirect status without a target: treat as final.
                    return self._finalize_no_body(
                        request, target, response, redirect_chain, started
                    )
                if hop >= max_redirects:
                    raise FetchError(
                        "too many redirects",
                        error_code=ERROR_REDIRECT_LIMIT,
                    )
                next_url = self._resolve_location(target.url, location)
                redirect_chain.append(
                    RedirectHop(
                        from_url=target.url,
                        to_url=next_url,
                        status_code=response.status_code,
                    )
                )
                current_url = next_url
                continue

            # Terminal response: stream body under both caps.
            return await self._read_body(
                request=request,
                target=target,
                response=response,
                redirect_chain=redirect_chain,
                started=started,
                max_wire=max_wire,
                max_decoded=max_decoded,
            )

        raise FetchError(
            "too many redirects", error_code=ERROR_REDIRECT_LIMIT
        )

    async def _resolve(
        self,
        url: str,
        *,
        root_registrable_domain: str | None,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        enforce_scope: bool,
    ) -> ResolvedTarget:
        try:
            return await resolve_target(
                url,
                resolver=self._resolver,
                root_registrable_domain=root_registrable_domain,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                enforce_scope=enforce_scope,
            )
        except UrlPolicyError as exc:
            # Out-of-scope / disallowed scheme-port-userinfo on a redirect hop.
            raise FetchError(
                str(exc), error_code=ERROR_SSRF_BLOCKED
            ) from exc

    def _resolve_location(self, base_url: str, location: str) -> str:
        from urllib.parse import urljoin

        return urljoin(base_url, location)

    def _finalize_no_body(
        self,
        request: FetchRequest,
        target: ResolvedTarget,
        response: httpx.Response,
        redirect_chain: list[RedirectHop],
        started: float,
    ) -> FetchResult:
        latency = int((time.monotonic() - started) * 1000)
        return FetchResult(
            requested_url=request.url,
            final_url=target.url,
            status_code=response.status_code,
            redacted_headers=redact_headers(response.headers),
            content_type=_content_type(response.headers),
            http_version=response.http_version or "",
            body=b"",
            wire_bytes=0,
            decoded_bytes=0,
            ttfb_ms=latency,
            latency_ms=latency,
            redirect_chain=tuple(redirect_chain),
        )

    async def _read_body(
        self,
        *,
        request: FetchRequest,
        target: ResolvedTarget,
        response: httpx.Response,
        redirect_chain: list[RedirectHop],
        started: float,
        max_wire: int,
        max_decoded: int,
    ) -> FetchResult:
        ttfb = int((time.monotonic() - started) * 1000)
        content_type = _content_type(response.headers)
        allowed = request.allowed_content_types
        # An empty status body (204/304) or HEAD carries no content-type; only
        # enforce the allowlist when one is set on the request.
        if allowed and content_type and content_type not in allowed:
            await response.aclose()
            raise FetchError(
                f"unsupported content type: {content_type}",
                error_code=ERROR_UNSUPPORTED_CONTENT_TYPE,
            )

        decode = _incremental_decoder(
            response.headers.get("content-encoding", "")
        )
        wire_total = 0
        decoded_total = 0
        decoded_chunks: list[bytes] = []
        try:
            async for raw in response.aiter_raw():
                wire_total += len(raw)
                if wire_total > max_wire:
                    raise FetchError(
                        "response exceeded wire byte cap",
                        error_code=ERROR_RESPONSE_TOO_LARGE,
                    )
                try:
                    out = decode(raw)
                except zlib.error:
                    # Malformed compression -> treat bytes as opaque wire data.
                    out = raw
                if out:
                    decoded_total += len(out)
                    if decoded_total > max_decoded:
                        raise FetchError(
                            "response exceeded decoded byte cap "
                            "(compression bomb)",
                            error_code=ERROR_RESPONSE_TOO_LARGE,
                        )
                    decoded_chunks.append(out)
        finally:
            await response.aclose()

        latency = int((time.monotonic() - started) * 1000)
        return FetchResult(
            requested_url=request.url,
            final_url=target.url,
            status_code=response.status_code,
            redacted_headers=redact_headers(response.headers),
            content_type=content_type,
            http_version=response.http_version or "",
            body=b"".join(decoded_chunks),
            wire_bytes=wire_total,
            decoded_bytes=decoded_total,
            ttfb_ms=ttfb,
            latency_ms=latency,
            redirect_chain=tuple(redirect_chain),
        )
