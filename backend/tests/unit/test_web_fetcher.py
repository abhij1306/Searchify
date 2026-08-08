"""Offline unit tests for the SSRF-safe fetcher (Task 3).

Every test injects a fake ``DnsResolver`` (returning a safe public IP so the
policy's DNS + SSRF gate passes) and an ``httpx.MockTransport`` so nothing hits
the network. Covers success, header redaction, content-type allowlist, 4xx/5xx
pass-through (not raised), redirect following + re-validation, redirect-limit,
redirect-escapes-scope, redirect-to-private (SSRF), timeout, wire-byte cap, and
the decoded-byte (gzip compression bomb) cap.
"""

from __future__ import annotations

import gzip
import zlib
from dataclasses import replace

import httpx
import pytest

from app.connectors.web_evidence.contracts import (
    AcquisitionTransport,
    FetchError,
    FetchRequest,
    FetchResult,
    ResolvedTarget,
)
from app.connectors.web_evidence.fetcher import SecureFetcher, redact_headers
from app.core.config.site_health import SiteHealthSettings

_PUBLIC_IP = "93.184.216.34"


class _ByteStream(httpx.AsyncByteStream):
    """A replayable async byte stream so ``aiter_raw`` can read the body.

    ``httpx.MockTransport`` with ``content=`` marks the stream consumed, which
    trips the fetcher's streaming read; a real stream avoids that.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):
        yield self._data

    async def aclose(self) -> None:
        return None


def _html_response(
    status: int = 200,
    *,
    body: bytes = b"<html></html>",
    content_type: str = "text/html",
    content_encoding: str | None = None,
) -> httpx.Response:
    headers = {"content-type": content_type}
    if content_encoding is not None:
        headers["content-encoding"] = content_encoding
    return httpx.Response(status, headers=headers, stream=_ByteStream(body))


class _FakeResolver:
    def __init__(self, mapping: dict[str, list[str]], *, default=None) -> None:
        self._mapping = mapping
        self._default = default if default is not None else [_PUBLIC_IP]

    async def resolve(self, host: str, port: int) -> list[str]:
        return list(self._mapping.get(host, self._default))


class _FakeAcquisitionTransport(AcquisitionTransport):
    def __init__(self, result: FetchResult) -> None:
        self.result = result
        self.targets: list[ResolvedTarget] = []

    async def fetch(
        self,
        request: FetchRequest,
        target: ResolvedTarget,
        *,
        max_wire_bytes: int,
        max_decoded_bytes: int,
        timeout_seconds: float,
    ) -> FetchResult:
        self.targets.append(target)
        return self.result


class _SequenceAcquisitionTransport(AcquisitionTransport):
    def __init__(self, results: list[FetchResult]) -> None:
        self.results = iter(results)
        self.targets: list[ResolvedTarget] = []

    async def fetch(
        self,
        request: FetchRequest,
        target: ResolvedTarget,
        *,
        max_wire_bytes: int,
        max_decoded_bytes: int,
        timeout_seconds: float,
    ) -> FetchResult:
        self.targets.append(target)
        return next(self.results)


def _rendered_result(*, title: str = "rendered page") -> FetchResult:
    """A browser result big enough to clear ``browser_low_content_bytes``.

    The floor exists so a challenge interstitial or JS shell cannot overwrite
    the server evidence an earlier rung already had, which means a stub standing
    in for a REAL rendered page has to look like one. A 40-byte fixture is a
    shell by the crawler's own definition.
    """
    body = (
        f"<html><head><title>{title}</title></head><body>".encode()
        + b"<p>Rendered prose that a real page would carry.</p>" * 16
        + b"</body></html>"
    )
    return _acquisition_result(body=body)


def _acquisition_result(
    *,
    status: int = 200,
    body: bytes = b"<html><body>curl content</body></html>",
) -> FetchResult:
    return FetchResult(
        requested_url="https://example.com/",
        final_url="https://example.com/",
        status_code=status,
        redacted_headers={"content-type": "text/html"},
        content_type="text/html",
        http_version="2",
        body=body,
        wire_bytes=len(body),
        decoded_bytes=len(body),
        ttfb_ms=1,
        latency_ms=2,
    )


def _fetcher(
    handler,
    resolver,
    *,
    settings: SiteHealthSettings | None = None,
    browser_transport: AcquisitionTransport | None = None,
    curl_transport: AcquisitionTransport | None = None,
    curl_pinned_resolution_supported: bool | None = None,
) -> SecureFetcher:
    return SecureFetcher(
        resolver=resolver,
        transport=httpx.MockTransport(handler),
        browser_transport=browser_transport,
        curl_transport=curl_transport,
        curl_pinned_resolution_supported=curl_pinned_resolution_supported,
        settings=settings or SiteHealthSettings(),
    )


class _StubBrowserTransport(AcquisitionTransport):
    """Records calls and returns a canned rendered result (no real browser).

    Declares the transport interface like ``_FakeAcquisitionTransport`` does, so
    a change to that contract fails here rather than leaving a structurally
    typed stub standing in for a transport it no longer matches.
    """

    def __init__(self, result: FetchResult | Exception) -> None:
        self._result = result
        self.calls: list[str] = []
        self.closed = False

    async def fetch(
        self,
        request: FetchRequest,
        target: ResolvedTarget,
        *,
        max_wire_bytes: int,
        max_decoded_bytes: int,
        timeout_seconds: float,
    ) -> FetchResult:
        self.calls.append(target.url)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def aclose(self) -> None:
        self.closed = True


# --- redact_headers -------------------------------------------------------


def test_redact_headers_drops_non_allowlisted():
    headers = httpx.Headers(
        {
            "content-type": "text/html",
            "set-cookie": "session=secret",
            "authorization": "Bearer x",
        }
    )
    out = redact_headers(headers)
    assert "content-type" in out
    assert "set-cookie" not in out
    assert "authorization" not in out


# --- success --------------------------------------------------------------


async def test_fetch_success_returns_bounded_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(
            body=b"<html><title>Hi</title></html>",
            content_type="text/html; charset=utf-8",
        )

    resolver = _FakeResolver({"example.com": [_PUBLIC_IP]})
    async with _fetcher(handler, resolver) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(
                url="https://example.com/",
                purpose="discover",
                allowed_content_types=frozenset({"text/html"}),
            )
        )
    assert result.status_code == 200
    assert result.content_type == "text/html"
    assert b"Hi" in result.body
    assert result.decoded_bytes > 0


async def test_fetch_rejects_unsupported_content_type():
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(body=b"%PDF-1.4", content_type="application/pdf")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(
                    url="https://example.com/doc",
                    purpose="discover",
                    allowed_content_types=frozenset({"text/html"}),
                )
            )
    assert exc.value.error_code == "unsupported_content_type"


async def test_fetch_returns_error_status_despite_disallowed_content_type():
    """A 429 served as ``text/plain`` is a rate limit, not a content-type error.

    The allowlist guards CONTENT. Applying it to an error response hid the
    status: a WAF rate limit (``429`` + ``text/plain`` + a few bytes, the shape
    real sites return) surfaced as a TERMINAL ``unsupported_content_type``, so
    the discover task never retried and the whole crawl failed on a transient
    block.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(429, body=b"Too Many Requests", content_type="text/plain")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(
                url="https://example.com/",
                purpose="discover",
                allowed_content_types=frozenset({"text/html"}),
            )
        )
    assert result.status_code == 429
    assert result.content_type == "text/plain"


async def test_fetch_still_rejects_disallowed_content_type_on_200():
    """The gate is unchanged for the success path it exists to guard."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(body=b"%PDF-1.4", content_type="application/pdf")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(
                    url="https://example.com/doc",
                    purpose="discover",
                    allowed_content_types=frozenset({"text/html"}),
                )
            )
    assert exc.value.error_code == "unsupported_content_type"
    assert exc.value.status_code == 200


# --- 4xx / 5xx are returned, not raised -----------------------------------


@pytest.mark.parametrize("status", [404, 410, 429, 500, 503])
async def test_fetch_returns_http_error_statuses(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(status, body=b"x")

    resolver = _FakeResolver({})
    fetcher = SecureFetcher(
        resolver=resolver,
        transport=httpx.MockTransport(handler),
    )
    async with fetcher:
        result = await fetcher.fetch(
            FetchRequest(
                url="https://example.com/",
                purpose="discover",
                allowed_content_types=frozenset({"text/html"}),
            )
        )
    assert result.status_code == status
    # Exactly one network call: an error status is returned, never retried.
    assert len(result.attempts) == 1


# --- redirects ------------------------------------------------------------


async def test_fetch_follows_in_scope_redirect_and_records_chain():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://example.com/new"})
        return _html_response(body=b"<html></html>")

    resolver = _FakeResolver({"example.com": [_PUBLIC_IP]})
    async with _fetcher(handler, resolver) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(
                url="https://example.com/old",
                purpose="discover",
                allowed_content_types=frozenset({"text/html"}),
            ),
            root_registrable_domain="example.com",
            enforce_scope=True,
        )
    assert result.status_code == 200
    assert result.final_url == "https://example.com/new"
    assert len(result.redirect_chain) == 1
    assert result.redirect_chain[0].to_url == "https://example.com/new"


async def test_fetch_redirect_limit_exceeded():
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        # Always redirect to a new in-scope path -> exceeds the cap.
        counter["n"] += 1
        return httpx.Response(
            302,
            headers={"location": f"https://example.com/r{counter['n']}"},
        )

    resolver = _FakeResolver({"example.com": [_PUBLIC_IP]})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(
                    url="https://example.com/r0",
                    purpose="discover",
                    max_redirects=2,
                ),
                root_registrable_domain="example.com",
                enforce_scope=True,
            )
    assert exc.value.error_code == "redirect_limit"


async def test_fetch_redirect_escaping_scope_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://evil.com/x"})

    resolver = _FakeResolver({"example.com": [_PUBLIC_IP], "evil.com": [_PUBLIC_IP]})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(url="https://example.com/x", purpose="discover"),
                root_registrable_domain="example.com",
                enforce_scope=True,
            )
    # Out-of-scope redirect is wrapped as an SSRF block.
    assert exc.value.error_code == "ssrf_blocked"


async def test_fetch_redirect_to_private_ip_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "https://internal.example.com/x"}
        )

    # The redirect target resolves to a private IP -> pick_connect_ip rejects.
    resolver = _FakeResolver(
        {
            "example.com": [_PUBLIC_IP],
            "internal.example.com": ["10.0.0.5"],
        }
    )
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(url="https://example.com/x", purpose="discover"),
                root_registrable_domain="example.com",
                enforce_scope=True,
            )
    assert exc.value.error_code == "ssrf_blocked"


# --- transport failure ----------------------------------------------------


async def test_fetch_send_transport_failure_is_connection_failed():
    """A send-phase transport error is ``connection_failed``, NOT ``ssrf_blocked``.

    ``ssrf_blocked`` is in ``POLICY_BLOCKING_ERROR_CODES``, so labelling a
    refused connection with it made a transient network error present the page
    as ``blocked`` (a policy denial) instead of ``error``. Only real policy
    denials — raised from ``_resolve`` — may use that token.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(url="https://example.com/", purpose="discover")
            )
    assert exc.value.error_code == "connection_failed"
    assert exc.value.retryable is True
    # The failed call is still traced (one entry, carrying the same token).
    assert [e.error_code for e in exc.value.attempts] == ["connection_failed"]


# --- deflate variants -----------------------------------------------------


@pytest.mark.parametrize("raw", [False, True])
async def test_fetch_decodes_zlib_wrapped_and_raw_deflate(raw: bool):
    """``Content-Encoding: deflate`` decodes whether or not it is zlib-wrapped.

    The spec says zlib-wrapped, but plenty of servers send bare DEFLATE. That
    used to raise ``zlib.error`` on the first chunk and surface as
    ``malformed_response``, losing a perfectly healthy page.
    """
    html = b"<html><body>hello deflate</body></html>"
    if raw:
        obj = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        payload = obj.compress(html) + obj.flush()
    else:
        payload = zlib.compress(html)

    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(body=payload, content_encoding="deflate")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(
                url="https://example.com/",
                purpose="discover",
                allowed_content_types=frozenset({"text/html"}),
            )
        )
    assert result.body == html
    assert result.decoded_bytes == len(html)


async def test_fetch_corrupt_deflate_still_fails():
    """The raw fallback is scoped to the header — corrupt bodies still fail."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(
            body=b"\x00\x01not-deflate-at-all\xff\xfe", content_encoding="deflate"
        )

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(
                    url="https://example.com/",
                    purpose="discover",
                    allowed_content_types=frozenset({"text/html"}),
                )
            )
    assert exc.value.error_code == "malformed_response"


# --- timeout --------------------------------------------------------------


async def test_fetch_timeout_is_classified():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(url="https://example.com/", purpose="discover")
            )
    assert exc.value.error_code == "timeout"
    assert exc.value.retryable is True


# --- size caps ------------------------------------------------------------


async def test_fetch_wire_byte_cap_aborts():
    big = b"x" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(body=big)

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(
                    url="https://example.com/",
                    purpose="discover",
                    allowed_content_types=frozenset({"text/html"}),
                    max_wire_bytes=1000,
                )
            )
    assert exc.value.error_code == "response_too_large"


async def test_fetch_decoded_byte_cap_gzip_bomb_aborts():
    # A small gzip payload that decompresses far past the decoded cap.
    payload = gzip.compress(b"A" * 100_000)

    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(body=payload, content_encoding="gzip")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(
                    url="https://example.com/",
                    purpose="discover",
                    allowed_content_types=frozenset({"text/html"}),
                    max_wire_bytes=1_000_000,
                    max_decoded_bytes=1000,
                )
            )
    assert exc.value.error_code == "response_too_large"


# --- truncated / malformed compressed bodies (handoff finding 6) ----------


async def test_fetch_truncated_gzip_body_raises_malformed():
    # A well-formed gzip stream that is cut off before its final block: the
    # incremental decompressor never reaches ``eof``, so the fetcher must
    # treat it as truncated rather than silently accepting a partial body.
    full = gzip.compress(b"<html><body>" + b"Z" * 5000 + b"</body></html>")
    truncated = full[: len(full) - 20]

    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(body=truncated, content_encoding="gzip")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(
                    url="https://example.com/",
                    purpose="discover",
                    allowed_content_types=frozenset({"text/html"}),
                    max_wire_bytes=1_000_000,
                    max_decoded_bytes=1_000_000,
                )
            )
    assert exc.value.error_code == "malformed_response"
    assert exc.value.retryable is True


async def test_fetch_truncated_deflate_body_raises_malformed():
    import zlib

    full = zlib.compress(b"<html><body>" + b"Q" * 4000 + b"</body></html>")
    truncated = full[: len(full) - 15]

    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(body=truncated, content_encoding="deflate")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(
                    url="https://example.com/",
                    purpose="discover",
                    allowed_content_types=frozenset({"text/html"}),
                    max_wire_bytes=1_000_000,
                    max_decoded_bytes=1_000_000,
                )
            )
    assert exc.value.error_code == "malformed_response"


async def test_fetch_complete_gzip_body_succeeds():
    # A complete gzip stream reaches ``eof`` and decodes fully.
    payload = gzip.compress(b"<html><body>ok</body></html>")

    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(body=payload, content_encoding="gzip")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(
                url="https://example.com/",
                purpose="discover",
                allowed_content_types=frozenset({"text/html"}),
            )
        )
    assert result.body == b"<html><body>ok</body></html>"


async def test_fetch_flushed_tail_still_enforces_decoded_cap():
    # A body whose decoded size only crosses the cap once the decompressor's
    # buffered tail is flushed must still abort as too-large (the cap is
    # enforced on flushed output, not only on per-chunk output).
    payload = gzip.compress(b"B" * 50_000)

    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(body=payload, content_encoding="gzip")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(
                    url="https://example.com/",
                    purpose="discover",
                    allowed_content_types=frozenset({"text/html"}),
                    max_wire_bytes=1_000_000,
                    max_decoded_bytes=1000,
                )
            )
    assert exc.value.error_code == "response_too_large"


# --- charset extraction (handoff finding 5 support) -----------------------


async def test_fetch_extracts_declared_charset():
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(content_type="text/html; charset=ISO-8859-1")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(
                url="https://example.com/",
                purpose="discover",
                allowed_content_types=frozenset({"text/html"}),
            )
        )
    assert result.charset == "iso-8859-1"
    # ``content_type`` still strips parameters.
    assert result.content_type == "text/html"


async def test_fetch_missing_charset_is_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(content_type="text/html")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(
                url="https://example.com/",
                purpose="discover",
                allowed_content_types=frozenset({"text/html"}),
            )
        )
    assert result.charset == ""


# --- acquisition ladder ---------------------------------------------------


async def test_hard_excluded_url_never_calls_any_acquisition_rung():
    calls = {"httpx": 0}

    def direct_handler(request: httpx.Request) -> httpx.Response:
        calls["httpx"] += 1
        return _html_response()

    browser = _StubBrowserTransport(_rendered_result())
    settings = SiteHealthSettings(curl_cffi_enabled=True, browser_enabled=True)
    async with _fetcher(
        direct_handler,
        _FakeResolver({}),
        settings=settings,
        browser_transport=browser,
        curl_pinned_resolution_supported=False,
    ) as fetcher:
        with pytest.raises(FetchError) as exc:
            await fetcher.fetch(
                FetchRequest(url="https://example.com/login", purpose="discover")
            )
    assert exc.value.error_code == "url_admission_rejected"
    assert exc.value.attempts == ()
    assert calls == {"httpx": 0}
    assert browser.calls == []


async def test_challenge_uses_browser_after_explicit_curl_unavailable():
    def direct_handler(request: httpx.Request) -> httpx.Response:
        return _html_response(403, body=b"<title>Just a moment...</title>")

    browser = _StubBrowserTransport(_rendered_result(title="actual page"))
    settings = SiteHealthSettings(curl_cffi_enabled=True, browser_enabled=True)
    async with _fetcher(
        direct_handler,
        _FakeResolver({}),
        settings=settings,
        browser_transport=browser,
        curl_pinned_resolution_supported=False,
    ) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(
                url="https://example.com/",
                purpose="discover",
                allowed_content_types=frozenset({"text/html"}),
            )
        )

    assert result.status_code == 200
    assert result.acquisition is not None
    assert result.acquisition.transport == "patchright"
    assert result.acquisition.rung == 3
    assert result.acquisition.trigger == "challenge"
    assert [entry.acquisition.transport for entry in result.attempts] == [
        "httpx",
        "patchright",
    ]
    # The browser only ever sees the already-validated target.
    assert browser.calls == ["https://example.com/"]


async def test_challenge_uses_pinned_curl_rung_when_available():
    def direct_handler(request: httpx.Request) -> httpx.Response:
        return _html_response(403, body=b"<title>Just a moment...</title>")

    curl_transport = _FakeAcquisitionTransport(_acquisition_result())
    settings = SiteHealthSettings(curl_cffi_enabled=True)
    async with _fetcher(
        direct_handler,
        _FakeResolver({}),
        settings=settings,
        curl_transport=curl_transport,
        curl_pinned_resolution_supported=True,
    ) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(
                url="https://example.com/",
                purpose="discover",
                allowed_content_types=frozenset({"text/html"}),
            )
        )

    assert result.acquisition is not None
    assert result.acquisition.transport == "curl_cffi"
    assert result.acquisition.rung == 2
    assert result.acquisition.trigger == "challenge"
    assert result.acquisition.impersonation_profile == "chrome"
    assert [entry.acquisition.transport for entry in result.attempts] == [
        "httpx",
        "curl_cffi",
    ]
    assert curl_transport.targets[0].connect_ip == _PUBLIC_IP


async def test_curl_redirect_uses_transient_location_and_revalidates_next_hop():
    def direct_handler(request: httpx.Request) -> httpx.Response:
        return _html_response(403, body=b"<title>Just a moment...</title>")

    first = replace(
        _acquisition_result(status=302, body=b""),
        redirect_location="/next",
        redacted_headers={"content-type": "text/html"},
    )
    second = replace(
        _acquisition_result(),
        final_url="https://example.com/next",
    )
    curl_transport = _SequenceAcquisitionTransport([first, second])
    settings = SiteHealthSettings(curl_cffi_enabled=True)

    async with _fetcher(
        direct_handler,
        _FakeResolver({}),
        settings=settings,
        curl_transport=curl_transport,
        curl_pinned_resolution_supported=True,
    ) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(url="https://example.com/", purpose="discover")
        )

    assert [target.url for target in curl_transport.targets] == [
        "https://example.com/",
        "https://example.com/next",
    ]
    assert [hop.to_url for hop in result.redirect_chain] == ["https://example.com/next"]


@pytest.mark.parametrize(
    ("direct_status", "direct_body", "low_content_bytes", "expected_trigger"),
    [
        (429, b"rate limited", 0, "block_status"),
        (200, b"tiny", 512, "low_content"),
    ],
)
async def test_browser_keeps_evidence_trigger_when_curl_is_unavailable(
    direct_status: int,
    direct_body: bytes,
    low_content_bytes: int,
    expected_trigger: str,
):
    def direct_handler(request: httpx.Request) -> httpx.Response:
        return _html_response(direct_status, body=direct_body)

    browser = _StubBrowserTransport(_rendered_result())
    settings = SiteHealthSettings(
        curl_cffi_enabled=True,
        curl_cffi_low_content_bytes=low_content_bytes,
        browser_enabled=True,
    )
    async with _fetcher(
        direct_handler,
        _FakeResolver({}),
        settings=settings,
        browser_transport=browser,
        curl_pinned_resolution_supported=False,
    ) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(url="https://example.com/", purpose="discover")
        )

    assert result.acquisition is not None
    assert result.acquisition.transport == "patchright"
    assert result.acquisition.trigger == expected_trigger


async def test_browser_shell_below_the_content_floor_keeps_prior_evidence():
    """A render under ``browser_low_content_bytes`` is the shell, not the page.

    The floor was configured and never consulted, so a challenge interstitial
    the browser rendered came back as the crawl's answer and overwrote the
    thin-but-real server evidence rung 1 already had.
    """

    def direct_handler(request: httpx.Request) -> httpx.Response:
        return _html_response(403, body=b"<title>Just a moment...</title>")

    shell = _acquisition_result(body=b"<html><body>checking...</body></html>")
    browser = _StubBrowserTransport(shell)
    settings = SiteHealthSettings(
        curl_cffi_enabled=True, browser_enabled=True, browser_low_content_bytes=512
    )
    async with _fetcher(
        direct_handler,
        _FakeResolver({}),
        settings=settings,
        browser_transport=browser,
        curl_pinned_resolution_supported=False,
    ) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(url="https://example.com/", purpose="discover")
        )

    assert browser.calls == ["https://example.com/"]
    # The prior 403 evidence survives; the shell did not become the answer.
    assert result.status_code == 403
    assert b"checking" not in result.body


async def test_browser_rung_failure_keeps_prior_server_evidence():
    """An unusable last rung must not turn thin evidence into a hard failure."""

    def direct_handler(request: httpx.Request) -> httpx.Response:
        return _html_response(403, body=b"cf-chl")

    browser = _StubBrowserTransport(
        FetchError("render failed", error_code="connection_failed", retryable=True)
    )
    settings = SiteHealthSettings(curl_cffi_enabled=True, browser_enabled=True)
    async with _fetcher(
        direct_handler,
        _FakeResolver({}),
        settings=settings,
        browser_transport=browser,
        curl_pinned_resolution_supported=False,
    ) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(url="https://example.com/", purpose="discover")
        )

    # The 403 challenge response survives as the crawl's answer, and the failed
    # browser call is still recorded as a real attempt.
    assert result.status_code == 403
    assert [entry.acquisition.transport for entry in result.attempts] == [
        "httpx",
        "patchright",
    ]
    # Provenance names the rung that actually produced this evidence. Carrying
    # the failed browser attempt here would attribute a server response to a
    # render that never happened.
    assert result.acquisition is not None
    assert result.acquisition.transport == "httpx"
    assert result.acquisition.rung == 1
    # The failed rung keeps its own classified reason on its own attempt row.
    browser_attempt = result.attempts[-1]
    assert browser_attempt.error_code == "connection_failed"
    assert browser_attempt.acquisition.rung == 3


async def test_injected_browser_transport_is_not_closed_by_the_fetcher():
    """An injected transport belongs to the caller, who may share it.

    ``CommerceDiscoveryWorker`` builds one fetcher per task around a single
    shared browser transport; closing it on the first fetcher's exit would
    leave every later task with a dead rung.
    """

    # A challenge response, so the ladder actually REACHES the browser rung —
    # asserting "not closed" after a fetch that never used it would pass even
    # if the close path were broken.
    def direct_handler(request: httpx.Request) -> httpx.Response:
        return _html_response(403, body=b"<title>Just a moment...</title>")

    browser = _StubBrowserTransport(_rendered_result())
    settings = SiteHealthSettings(browser_enabled=True)
    async with _fetcher(
        direct_handler,
        _FakeResolver({}),
        settings=settings,
        browser_transport=browser,
        curl_pinned_resolution_supported=False,
    ) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(url="https://example.com/", purpose="discover")
        )

    assert browser.calls == ["https://example.com/"]
    assert result.acquisition is not None
    assert result.acquisition.transport == "patchright"
    assert browser.closed is False


async def test_browser_rung_is_skipped_when_disabled():
    def direct_handler(request: httpx.Request) -> httpx.Response:
        return _html_response(403, body=b"cf-chl")

    browser = _StubBrowserTransport(_rendered_result())
    settings = SiteHealthSettings(curl_cffi_enabled=True, browser_enabled=False)
    async with _fetcher(
        direct_handler,
        _FakeResolver({}),
        settings=settings,
        browser_transport=browser,
        curl_pinned_resolution_supported=False,
    ) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(url="https://example.com/", purpose="discover")
        )

    assert result.status_code == 403
    assert browser.calls == []
