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

import httpx
import pytest

from app.connectors.web_evidence.contracts import FetchError, FetchRequest
from app.connectors.web_evidence.fetcher import SecureFetcher, redact_headers

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


def _fetcher(handler, resolver) -> SecureFetcher:
    return SecureFetcher(resolver=resolver, transport=httpx.MockTransport(handler))


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


# --- 4xx / 5xx are returned, not raised -----------------------------------


@pytest.mark.parametrize("status", [404, 410, 429, 500, 503])
async def test_fetch_returns_http_error_statuses(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return _html_response(status, body=b"x")

    resolver = _FakeResolver({})
    async with _fetcher(handler, resolver) as fetcher:
        result = await fetcher.fetch(
            FetchRequest(
                url="https://example.com/",
                purpose="discover",
                allowed_content_types=frozenset({"text/html"}),
            )
        )
    assert result.status_code == status


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
