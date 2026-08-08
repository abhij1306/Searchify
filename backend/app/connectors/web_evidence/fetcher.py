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
# The acquisition ladder is frozen at three rungs, each entered only on
# config-owned evidence (``curl_trigger_for_result``) that the previous rung's
# response is unusable:
#   1. ``secure_httpx``   — ordinary server-rendered evidence;
#   2. ``curl_cffi``      — transport/challenge evidence justifies one retry;
#   3. ``patchright``     — a JS shell still needs local rendering.
# There is deliberately NO paid acquisition vendor and no real-Chrome
# escalation. A site that still blocks a well-identified crawler after the
# ladder is telling us it is not AEO-ready, and that answer is the signal we
# report (``ERROR_BOT_BLOCKED``) rather than something to work around further.
# ``is_bot_block_result`` classifies that outcome; nothing retries past rung 3.
#
# Every REAL network call (every redirect hop) appends one ``FetchCallTrace``
# entry; the immutable trace is returned on BOTH ``FetchResult`` and
# ``FetchError`` so it survives failure. Persisting one ``SiteFetchAttempt``
# row per entry is T8's job.
#
# The DNS resolver and (optionally) the httpx transport are injected so tests
# run entirely offline with a fake resolver and ``httpx.MockTransport`` (no
# live internet — subplan test contract). There is NO raw-body persistence:
# the decoded bytes are handed back in-process for bounded parsing only.
from __future__ import annotations

import time
import zlib
from collections.abc import Iterable
from dataclasses import replace
from typing import cast
from urllib.parse import urljoin, urlsplit

import httpx

from app.connectors.web_evidence.acquisition import (
    curl_cffi_pinned_resolution_supported,
    curl_trigger_for_result,
)
from app.connectors.web_evidence.browser_transport import PatchrightTransport
from app.connectors.web_evidence.contracts import (
    AcquisitionProvenance,
    AcquisitionTransport,
    DnsResolver,
    FetchCallTrace,
    FetchError,
    FetchRequest,
    FetchResult,
    RedirectHop,
    ResolvedTarget,
)
from app.connectors.web_evidence.curl_transport import CurlCffiTransport
from app.connectors.web_evidence.url_policy import (
    UrlPolicyError,
    classify_url_admission,
    resolve_target,
)
from app.core.config.site_health import (
    ACQUISITION_TRANSPORT_BROWSER,
    ACQUISITION_TRANSPORT_CURL_CFFI,
    ACQUISITION_TRANSPORT_HTTPX,
    ACQUISITION_TRIGGER_INITIAL,
    BOT_BLOCK_BODY_MARKERS,
    BOT_BLOCK_MARKER_SCAN_BYTES,
    ERROR_ACQUISITION_UNAVAILABLE,
    ERROR_CONNECTION_FAILED,
    ERROR_MALFORMED_RESPONSE,
    ERROR_REDIRECT_LIMIT,
    ERROR_RESPONSE_TOO_LARGE,
    ERROR_SSRF_BLOCKED,
    ERROR_TIMEOUT,
    ERROR_UNSUPPORTED_CONTENT_TYPE,
    ERROR_URL_ADMISSION_REJECTED,
    FETCH_PURPOSE_ANALYZE,
    FETCH_PURPOSE_DISCOVER,
    FETCH_PURPOSE_LINK_CHECK,
    PERSISTED_RESPONSE_HEADERS,
    POLICY_BLOCKING_ERROR_CODES,
    SITE_HEALTH_USER_AGENT,
    URL_EXCLUSION_HARD_ASSET,
    URL_EXCLUSION_HARD_PATH,
    URL_EXCLUSION_HARD_QUERY,
    URL_EXCLUSION_TRACKING,
    site_health_settings,
)

_REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})

# Config-owned bot-block body markers as matchable bytes (ASCII-only tokens).
_BOT_BLOCK_MARKER_BYTES: tuple[bytes, ...] = tuple(
    marker.encode("ascii") for marker in BOT_BLOCK_BODY_MARKERS
)

_ADMISSION_ENFORCED_PURPOSES = frozenset(
    {FETCH_PURPOSE_DISCOVER, FETCH_PURPOSE_ANALYZE, FETCH_PURPOSE_LINK_CHECK}
)
_HARD_ADMISSION_EXCLUSION_CODES = frozenset(
    {
        URL_EXCLUSION_HARD_PATH,
        URL_EXCLUSION_HARD_ASSET,
        URL_EXCLUSION_HARD_QUERY,
        URL_EXCLUSION_TRACKING,
    }
)


def is_bot_block_result(result: FetchResult) -> bool:
    """Config-owned bot-block signature on a fetch RESULT (spec §5.4).

    True when a distinctive challenge-platform marker appears within the first
    ``BOT_BLOCK_MARKER_SCAN_BYTES`` of the decoded body. This is a
    CLASSIFICATION, not a retry trigger: the worker turns it into
    ``ERROR_BOT_BLOCKED`` so the page presents as ``blocked``.

    Deliberately marker-only. A bare 401/403/503 is NOT enough: those statuses
    used to be a cheap trigger to RETRY with impersonation, and only a second
    blocked response promoted the outcome to ``bot_blocked``. With no retry
    there is no corroborating evidence, so a status-only rule would relabel
    every members-only 401 and every transient 503 as bot protection. The
    challenge markers stand on their own; the statuses keep their ordinary
    ``http_4xx``/``http_5xx`` classification.
    """
    if not _BOT_BLOCK_MARKER_BYTES:
        return False
    prefix = result.body[:BOT_BLOCK_MARKER_SCAN_BYTES].lower()
    return any(marker in prefix for marker in _BOT_BLOCK_MARKER_BYTES)


def redact_headers(headers: httpx.Headers | dict) -> dict[str, str]:
    """Keep only the config-allowlisted response headers (lowercased keys).

    Everything else (Set-Cookie, Authorization echoes, etc.) is dropped so no
    sensitive header is ever persisted or logged.
    """
    out: dict[str, str] = {}
    items: Iterable[tuple[str, str]] = (
        headers.items() if hasattr(headers, "items") else []
    )
    for key, value in items:
        lk = str(key).lower()
        if lk in PERSISTED_RESPONSE_HEADERS:
            out[lk] = str(value)
    return out


def _content_type(headers: httpx.Headers) -> str:
    raw = headers.get("content-type", "")
    return str(raw).split(";", 1)[0].strip().lower()


def _content_type_gate_applies(status_code: int | None) -> bool:
    """Whether the content-type allowlist may reject this response.

    The allowlist exists to stop us downloading and parsing non-HTML CONTENT.
    An HTTP ERROR response is not content: its body is a diagnostic, and for a
    bot block it carries the very challenge markers we classify on. Rejecting
    one here hid the status behind ``unsupported_content_type`` — a 429 served
    as ``text/plain`` (the common WAF rate-limit shape) surfaced as a TERMINAL
    content-type failure instead of the retryable rate limit it is, and the
    whole crawl died on a transient block.

    So the gate applies to 2xx only — the responses whose body we actually
    keep as content. Error statuses are returned as results and classified
    from ``status_code`` by the caller; a 3xx body is discarded in favour of
    its ``Location``. The wire and decoded byte caps still bound the body on
    every path. An unknown status (``None``) keeps the gate ON — the
    conservative direction.
    """
    if status_code is None:
        return True
    return 200 <= status_code < 300


def _charset(headers: httpx.Headers) -> str:
    """Return the lowercased ``charset`` parameter of Content-Type, if any.

    Preserved separately from ``_content_type()`` (which intentionally strips
    parameters) so downstream HTML parsing can honor a non-UTF-8 charset
    instead of hard-coding UTF-8.
    """
    raw = str(headers.get("content-type", ""))
    for part in raw.split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == "charset":
            return value.strip().strip('"').strip("'").lower()
    return ""


class _DeflateDecoder:
    """``deflate`` decoder that tolerates the raw, headerless variant.

    ``Content-Encoding: deflate`` is specified as zlib-WRAPPED, but a good
    number of servers send bare DEFLATE with no zlib header. A default
    ``decompressobj()`` raises ``zlib.error`` on such a stream's first chunk,
    which ``_read_body`` turns into ``malformed_response`` — a healthy page
    lost to a server quirk rather than a real problem.

    So: try zlib-wrapped, and if the header is rejected before ANY output has
    been produced, retry the bytes seen so far raw (``-MAX_WBITS``) once and
    continue with that decompressor. Scoped to the header decision — once a
    format is settled, a mid-body ``zlib.error`` propagates and still fails the
    fetch, so genuinely corrupt bodies are not smuggled through.

    Exposes the ``decompress``/``flush``/``eof`` surface ``_read_body`` uses, so
    the swap stays invisible to the truncation check (a stale reference to the
    discarded object would have reported every raw stream as truncated).
    """

    __slots__ = ("_obj", "_pending", "_settled")

    def __init__(self) -> None:
        self._obj = zlib.decompressobj()
        # Bytes fed so far, kept only until the format is settled so the raw
        # retry can replay them; dropped immediately after (never a full body).
        self._pending = b""
        self._settled = False

    def decompress(self, chunk: bytes) -> bytes:
        if self._settled:
            return self._obj.decompress(chunk)
        self._pending += chunk
        try:
            out = self._obj.decompress(chunk)
        except zlib.error:
            # zlib header rejected: replay everything as raw deflate.
            self._obj = zlib.decompressobj(-zlib.MAX_WBITS)
            out = self._obj.decompress(self._pending)
            self._settled = True
            self._pending = b""
            return out
        # A zlib header is 2 bytes, so that is when the verdict is final.
        if len(self._pending) >= 2:
            self._settled = True
            self._pending = b""
        return out

    def flush(self) -> bytes:
        return self._obj.flush()

    @property
    def eof(self) -> bool:
        return self._obj.eof


def _incremental_decoder(content_encoding: str):
    """Return ``(decode_chunk, decompressor)`` for the wire encoding.

    ``decode_chunk`` is a ``callable(chunk)->bytes`` that feeds a chunk into the
    decompressor. ``decompressor`` is the underlying ``zlib`` object (``None``
    for identity/unknown encodings) so the caller can, after the stream ends,
    flush any buffered tail and inspect ``.eof`` — a gzip/deflate stream that
    was cut off mid-way never sets ``eof``, which is how a truncated response is
    detected (a truncated stream does not necessarily raise ``zlib.error``).

    Supports gzip and deflate (the encodings a compression bomb would use);
    ``identity``/unknown pass bytes through unchanged. brotli is not a
    dependency, so a ``br`` body is treated as opaque wire bytes (the wire cap
    still bounds it). ``deflate`` also accepts the raw headerless variant many
    servers send — see ``_DeflateDecoder``.
    """
    enc = str(content_encoding or "").strip().lower()
    if enc == "gzip":
        obj = zlib.decompressobj(16 + zlib.MAX_WBITS)
        return (lambda chunk: obj.decompress(chunk)), obj
    if enc == "deflate":
        deflate = _DeflateDecoder()
        return deflate.decompress, deflate
    return (lambda chunk: chunk), None


class SecureFetcher:
    """Shared SSRF-safe HTTP fetcher (httpx).

    Construct with the injected DNS ``resolver`` and, optionally, an httpx
    ``transport`` (tests pass ``httpx.MockTransport``). When a transport is
    injected the fetcher sends to the canonical URL as-is (so the mock can
    match it); in production (no transport) it pins the validated connection
    IP while preserving Host + SNI.
    """

    def __init__(
        self,
        *,
        resolver: DnsResolver,
        transport: httpx.AsyncBaseTransport | None = None,
        settings=site_health_settings,
        browser_transport: AcquisitionTransport | None = None,
        curl_transport: AcquisitionTransport | None = None,
        curl_pinned_resolution_supported: bool | None = None,
        user_agent: str = SITE_HEALTH_USER_AGENT,
    ) -> None:
        self._resolver = resolver
        self._settings = settings
        self._user_agent = user_agent
        self._injected_transport = transport
        self._browser_transport = browser_transport
        self._curl_pinned_resolution_supported = (
            curl_cffi_pinned_resolution_supported()
            if curl_pinned_resolution_supported is None
            else curl_pinned_resolution_supported
        )
        self._curl_transport = curl_transport
        self._owns_curl_transport = False
        if (
            self._curl_transport is None
            and settings.curl_cffi_enabled
            and self._curl_pinned_resolution_supported
        ):
            self._curl_transport = CurlCffiTransport(
                impersonation_profile=settings.curl_cffi_impersonation_profile,
                user_agent=user_agent,
            )
            self._owns_curl_transport = True
        # Only a transport WE created may be closed on exit. An injected one is
        # owned by the caller and is commonly shared across fetchers (see
        # ``CommerceDiscoveryWorker``, which builds one fetcher per task);
        # closing it here would shut down the shared browser after the first
        # fetch and leave every later task with a dead rung.
        self._owns_browser_transport = False
        if self._browser_transport is None and settings.browser_enabled:
            self._browser_transport = PatchrightTransport(
                settings=settings,
                user_agent=user_agent,
            )
            self._owns_browser_transport = True
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
        """Close every rung this fetcher constructed.

        Each teardown runs in a ``finally`` so an earlier failure cannot strand
        a later one — the browser rung owns OS PROCESSES, not just sockets, and
        leaving one to garbage collection strands a headless browser per
        fetcher. An INJECTED transport belongs to the caller (it is commonly
        shared across fetchers) and is deliberately left running.
        """
        try:
            await self._client.aclose()
        finally:
            try:
                if self._owns_curl_transport and self._curl_transport is not None:
                    await self._curl_transport.aclose()
            finally:
                if self._owns_browser_transport and self._browser_transport is not None:
                    await self._browser_transport.aclose()

    def _limits(self, request: FetchRequest) -> tuple[int, int, float, int]:
        s = self._settings
        return (
            request.max_wire_bytes or s.max_response_wire_bytes,
            request.max_decoded_bytes or s.max_response_decoded_bytes,
            request.timeout_seconds or s.request_timeout_seconds,
            request.max_redirects
            if request.max_redirects is not None
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
            dial_url = parts._replace(netloc=f"{ip_literal}:{target.port}").geturl()
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

        A bot-blocked response is returned as the result it is — there is no
        retry and no impersonation. The per-network-call trace is carried on
        the returned ``FetchResult.attempts`` AND on any raised
        ``FetchError.attempts`` (dual-field design), so the trace survives
        failure; the entry describing a returned result is always the last.
        """
        admission = classify_url_admission(
            request.url,
            root_registrable_domain=root_registrable_domain,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            infrastructure_purpose=request.purpose,
        )
        if (
            request.purpose in _ADMISSION_ENFORCED_PURPOSES
            and admission.reason_code in _HARD_ADMISSION_EXCLUSION_CODES
        ):
            raise FetchError(
                "URL rejected by admission policy",
                error_code=ERROR_URL_ADMISSION_REJECTED,
            )

        limits = self._limits(request)
        attempts: list[FetchCallTrace] = []
        initial = AcquisitionProvenance(
            transport=ACQUISITION_TRANSPORT_HTTPX,
            rung=1,
            trigger=ACQUISITION_TRIGGER_INITIAL,
            policy_version=self._settings.acquisition_policy_version,
        )
        try:
            result = await self._fetch_http(
                request,
                root_registrable_domain=root_registrable_domain,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                enforce_scope=enforce_scope,
                purpose=request.purpose,
                limits=limits,
                attempts=attempts,
                acquisition=initial,
            )
        except FetchError as exc:
            if not exc.attempts:
                exc.attempts = tuple(attempts)
            raise
        result = replace(result, attempts=tuple(attempts), acquisition=initial)
        trigger = self._ladder_trigger(result)
        # Continue while ANY later rung is enabled. Gating the whole ladder on
        # ``curl_cffi_enabled`` alone made rung 3 unreachable for a deployment
        # that runs the browser without curl — the evidence said "retry" and
        # the ladder stopped anyway.
        ladder_available = (
            self._settings.curl_cffi_enabled or self._settings.browser_enabled
        )
        if trigger is None or not ladder_available:
            return result
        return await self._continue_acquisition_ladder(
            request=request,
            root_registrable_domain=root_registrable_domain,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            enforce_scope=enforce_scope,
            limits=limits,
            attempts=attempts,
            trigger=trigger,
            prior=result,
        )

    def _ladder_trigger(self, result: FetchResult) -> str | None:
        """The config-owned reason (if any) that this result needs a later rung.

        The JS-shell signal is offered ONLY when the browser rung is enabled.
        curl-cffi replays the same request with a different TLS fingerprint, so
        it returns the identical shell — escalating a shell to rung 2 would buy
        a second fetch and no new evidence. Zeroing the thresholds here (rather
        than branching inside the pure helper) keeps rung selection a matter of
        configuration.
        """

        browser = self._settings.browser_enabled
        return curl_trigger_for_result(
            result,
            has_challenge_marker=is_bot_block_result(result),
            trigger_statuses=self._settings.curl_cffi_trigger_statuses,
            low_content_bytes=self._settings.curl_cffi_low_content_bytes,
            js_shell_min_text_chars=(
                self._settings.js_shell_min_text_chars if browser else 0
            ),
            js_shell_min_inline_script_chars=(
                self._settings.js_shell_min_inline_script_chars
            ),
            js_shell_scan_bytes=self._settings.js_shell_scan_bytes,
        )

    async def _continue_acquisition_ladder(
        self,
        *,
        request: FetchRequest,
        root_registrable_domain: str | None,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        enforce_scope: bool,
        limits: tuple[int, int, float, int],
        attempts: list[FetchCallTrace],
        trigger: str,
        prior: FetchResult,
    ) -> FetchResult:
        """Run curl when available, then the local browser rung if needed."""

        curl_result = prior
        trigger_for_browser = trigger
        if self._curl_transport is not None:
            try:
                curl_result = await self._fetch_curl(
                    request=request,
                    root_registrable_domain=root_registrable_domain,
                    include_globs=include_globs,
                    exclude_globs=exclude_globs,
                    enforce_scope=enforce_scope,
                    limits=limits,
                    attempts=attempts,
                    trigger=trigger,
                )
            except FetchError as exc:
                if exc.error_code not in self._settings.browser_continue_error_codes:
                    if not exc.attempts:
                        exc.attempts = tuple(attempts)
                    raise
            else:
                curl_still_blocked = self._ladder_trigger(curl_result)
                if curl_still_blocked is None:
                    return curl_result
                trigger_for_browser = curl_still_blocked
        return await self._continue_with_browser(
            request=request,
            root_registrable_domain=root_registrable_domain,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            enforce_scope=enforce_scope,
            limits=limits,
            attempts=attempts,
            trigger=trigger_for_browser,
            prior=curl_result,
        )

    async def _fetch_curl(
        self,
        *,
        request: FetchRequest,
        root_registrable_domain: str | None,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        enforce_scope: bool,
        limits: tuple[int, int, float, int],
        attempts: list[FetchCallTrace],
        trigger: str,
    ) -> FetchResult:
        """Run the pinned curl transport with manual redirect validation."""

        transport = self._curl_transport
        if transport is None:
            raise FetchError(
                "curl acquisition transport unavailable",
                error_code=ERROR_ACQUISITION_UNAVAILABLE,
            )
        max_wire, max_decoded, timeout, max_redirects = limits
        acquisition = AcquisitionProvenance(
            transport=ACQUISITION_TRANSPORT_CURL_CFFI,
            rung=2,
            trigger=trigger,
            impersonation_profile=self._settings.curl_cffi_impersonation_profile,
            policy_version=self._settings.acquisition_policy_version,
        )
        current_url = request.url
        redirect_chain: list[RedirectHop] = []
        for hop in range(max_redirects + 1):
            target = await self._resolve(
                current_url,
                root_registrable_domain=root_registrable_domain,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                enforce_scope=enforce_scope,
                purpose=request.purpose,
            )
            hop_started = time.monotonic()
            try:
                result = await transport.fetch(
                    request,
                    target,
                    max_wire_bytes=max_wire,
                    max_decoded_bytes=max_decoded,
                    timeout_seconds=timeout,
                )
            except FetchError as exc:
                self._trace(
                    attempts,
                    url=target.url,
                    method=request.method,
                    status_code=exc.status_code,
                    error_code=exc.error_code,
                    wire_bytes=None,
                    decoded_bytes=None,
                    ttfb_ms=None,
                    started=hop_started,
                    acquisition=acquisition,
                )
                exc.attempts = tuple(attempts)
                raise
            location = result.redirect_location
            if result.status_code not in _REDIRECT_STATUSES or not location:
                self._trace_curl_result(
                    attempts, request, result, hop_started, acquisition
                )
                # Sonar models dataclasses.replace as DataclassInstance rather than
                # preserving the concrete dataclass type; narrow that analyzer gap.
                return cast(  # type: ignore[redundant-cast]
                    FetchResult,
                    replace(
                        result,
                        requested_url=request.url,
                        redirect_chain=tuple(redirect_chain),
                        attempts=tuple(attempts),
                        acquisition=acquisition,
                    ),
                )
            if hop >= max_redirects:
                self._trace_curl_result(
                    attempts,
                    request,
                    result,
                    hop_started,
                    acquisition,
                    error_code=ERROR_REDIRECT_LIMIT,
                )
                raise FetchError(
                    "curl acquisition redirect limit",
                    error_code=ERROR_REDIRECT_LIMIT,
                    attempts=tuple(attempts),
                )
            next_url = urljoin(target.url, location)
            redirect_chain.append(
                RedirectHop(
                    from_url=target.url,
                    to_url=next_url,
                    status_code=result.status_code,
                )
            )
            self._trace_curl_result(attempts, request, result, hop_started, acquisition)
            current_url = next_url
        raise FetchError(
            "curl acquisition redirect limit",
            error_code=ERROR_REDIRECT_LIMIT,
            attempts=tuple(attempts),
        )

    def _trace_curl_result(
        self,
        attempts: list[FetchCallTrace],
        request: FetchRequest,
        result: FetchResult,
        started: float,
        acquisition: AcquisitionProvenance,
        *,
        error_code: str | None = None,
    ) -> None:
        self._trace(
            attempts,
            url=result.final_url,
            method=request.method,
            status_code=result.status_code,
            error_code=error_code,
            wire_bytes=result.wire_bytes,
            decoded_bytes=result.decoded_bytes,
            ttfb_ms=result.ttfb_ms,
            started=started,
            acquisition=acquisition,
        )

    async def _continue_with_browser(
        self,
        *,
        request: FetchRequest,
        root_registrable_domain: str | None,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        enforce_scope: bool,
        limits: tuple[int, int, float, int],
        attempts: list[FetchCallTrace],
        trigger: str,
        prior: FetchResult,
    ) -> FetchResult:
        """Render the target locally when server evidence stays unusable.

        The last rung of the frozen ladder. The target is resolved through the
        same canonicalization, scope, hard-admission, and pinned-DNS checks as
        every other rung BEFORE the browser is allowed to navigate to it, so a
        rendered page can never reach an address the HTTP rungs would refuse.

        When no browser transport is configured this returns the prior result
        unchanged: an unavailable last rung is not itself a fetch failure.
        """

        transport = self._browser_transport
        if transport is None or not self._settings.browser_enabled:
            return replace(prior, attempts=tuple(attempts))

        max_wire, max_decoded, timeout, _max_redirects = limits
        acquisition = AcquisitionProvenance(
            transport=ACQUISITION_TRANSPORT_BROWSER,
            rung=3,
            trigger=trigger,
            options={
                "readiness_timeout_seconds": float(
                    self._settings.browser_readiness_timeout_seconds
                ),
                "navigation_timeout_seconds": float(
                    self._settings.browser_navigation_timeout_seconds
                ),
            },
            policy_version=self._settings.acquisition_policy_version,
        )
        started = time.monotonic()
        try:
            target = await self._resolve(
                request.url,
                root_registrable_domain=root_registrable_domain,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                enforce_scope=enforce_scope,
                purpose=request.purpose,
            )
        except FetchError as exc:
            exc.attempts = tuple(attempts)
            # A POLICY denial must surface: robots, admission, and scope apply
            # to rung 3 exactly as they do to rung 1, and swallowing one here
            # would let a render reach a URL the crawler is not allowed to
            # fetch. Anything else — a transient DNS or resolver failure — is
            # this rung being unavailable, and the prior server evidence stays
            # the crawl's answer rather than a page that already fetched
            # successfully being turned into a hard failure.
            if exc.error_code in POLICY_BLOCKING_ERROR_CODES:
                raise
            self._trace(
                attempts,
                url=request.url,
                method=request.method,
                status_code=exc.status_code,
                error_code=exc.error_code,
                wire_bytes=None,
                decoded_bytes=None,
                ttfb_ms=None,
                started=started,
                acquisition=acquisition,
            )
            return replace(prior, attempts=tuple(attempts))

        try:
            result = await transport.fetch(
                request,
                target,
                max_wire_bytes=max_wire,
                max_decoded_bytes=max_decoded,
                timeout_seconds=timeout,
            )
        except FetchError as exc:
            self._trace(
                attempts,
                url=target.url,
                method=request.method,
                status_code=exc.status_code,
                error_code=exc.error_code,
                wire_bytes=None,
                decoded_bytes=None,
                ttfb_ms=None,
                started=started,
                acquisition=acquisition,
            )
            # The browser rung is best-effort recovery: when it cannot render,
            # the prior server evidence remains the crawl's answer rather than
            # turning a usable-but-thin response into a hard failure.
            return replace(prior, attempts=tuple(attempts))

        self._trace(
            attempts,
            url=result.final_url or target.url,
            method=request.method,
            status_code=result.status_code,
            error_code=None,
            wire_bytes=result.wire_bytes,
            decoded_bytes=result.decoded_bytes,
            ttfb_ms=result.ttfb_ms,
            started=started,
            acquisition=acquisition,
        )
        return self._browser_result_or_prior(
            result,
            prior=prior,
            request=request,
            attempts=attempts,
            acquisition=acquisition,
        )

    def _browser_result_or_prior(
        self,
        result: FetchResult,
        *,
        prior: FetchResult,
        request: FetchRequest,
        attempts: list[FetchCallTrace],
        acquisition: AcquisitionProvenance,
    ) -> FetchResult:
        """The render, unless it came back under the configured content floor.

        ``browser_low_content_bytes`` was configured and then never consulted. A
        render below it is the challenge page or the JS shell this rung was sent
        to get PAST, and returning it let that shell overwrite the thin-but-real
        server evidence the earlier rung already had. Treated exactly like a
        render failure: keep what we had.
        """
        # ``decoded_bytes`` is the measure the curl rung's own low-content check
        # uses, so the two floors mean the same thing.
        if result.decoded_bytes < self._settings.browser_low_content_bytes:
            return replace(prior, attempts=tuple(attempts))
        return cast(  # type: ignore[redundant-cast]
            FetchResult,
            replace(
                result,
                requested_url=request.url,
                attempts=tuple(attempts),
                acquisition=acquisition,
            ),
        )

    def _trace(
        self,
        attempts: list[FetchCallTrace],
        *,
        url: str,
        method: str,
        status_code: int | None,
        error_code: str | None,
        wire_bytes: int | None,
        decoded_bytes: int | None,
        ttfb_ms: int | None,
        started: float,
        acquisition: AcquisitionProvenance,
    ) -> None:
        """Append ONE immutable trace entry for ONE real network call."""
        attempts.append(
            FetchCallTrace(
                request_ordinal=len(attempts),
                url=url,
                method=method,
                status_code=status_code,
                error_code=error_code,
                wire_bytes=wire_bytes,
                decoded_bytes=decoded_bytes,
                ttfb_ms=ttfb_ms,
                latency_ms=int((time.monotonic() - started) * 1000),
                acquisition=acquisition,
            )
        )

    async def _fetch_http(
        self,
        request: FetchRequest,
        *,
        root_registrable_domain: str | None,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        enforce_scope: bool,
        purpose: str,
        limits: tuple[int, int, float, int],
        attempts: list[FetchCallTrace],
        acquisition: AcquisitionProvenance,
    ) -> FetchResult:
        """The httpx fetch. One trace entry per real network call."""
        (
            max_wire,
            max_decoded,
            timeout,
            max_redirects,
        ) = limits

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
                purpose=purpose,
            )
            httpx_request = self._build_httpx_request(
                method=request.method,
                target=target,
                extra_headers=request.headers,
                timeout=timeout,
            )
            hop_started = time.monotonic()
            try:
                response = await self._client.send(httpx_request, stream=True)
            except httpx.TimeoutException as exc:
                self._trace(
                    attempts,
                    url=target.url,
                    method=request.method,
                    status_code=None,
                    error_code=ERROR_TIMEOUT,
                    wire_bytes=None,
                    decoded_bytes=None,
                    ttfb_ms=None,
                    started=hop_started,
                    acquisition=acquisition,
                )
                raise FetchError(
                    "request timed out",
                    error_code=ERROR_TIMEOUT,
                    retryable=True,
                ) from exc
            except httpx.HTTPError as exc:
                # Send-phase transport failure (DNS blip, refused connection,
                # reset handshake): ``connection_failed``, the same token
                # ``_read_body`` uses for the identical failure mid-body.
                #
                # This used to be ``ssrf_blocked`` — not because it was one, but
                # because the deleted TLS-block escalation hook matched on that
                # token to trigger an impersonated retry. With the retry gone the
                # label is actively wrong: ``ssrf_blocked`` is in
                # ``POLICY_BLOCKING_ERROR_CODES``, so a transient connection
                # error presented the page as ``blocked`` (a policy denial) when
                # it is an ``error``. SSRF_BLOCKED now means only what it says —
                # a real policy denial, raised from ``_resolve``.
                self._trace(
                    attempts,
                    url=target.url,
                    method=request.method,
                    status_code=None,
                    error_code=ERROR_CONNECTION_FAILED,
                    wire_bytes=None,
                    decoded_bytes=None,
                    ttfb_ms=None,
                    started=hop_started,
                    acquisition=acquisition,
                )
                raise FetchError(
                    f"connection error: {type(exc).__name__}",
                    error_code=ERROR_CONNECTION_FAILED,
                    retryable=True,
                ) from exc

            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    # A redirect status without a target: treat as final.
                    self._trace(
                        attempts,
                        url=target.url,
                        method=request.method,
                        status_code=response.status_code,
                        error_code=None,
                        wire_bytes=0,
                        decoded_bytes=0,
                        ttfb_ms=None,
                        started=hop_started,
                        acquisition=acquisition,
                    )
                    return self._finalize_no_body(
                        request,
                        target,
                        response,
                        redirect_chain,
                        started,
                        acquisition,
                    )
                if hop >= max_redirects:
                    self._trace(
                        attempts,
                        url=target.url,
                        method=request.method,
                        status_code=response.status_code,
                        error_code=ERROR_REDIRECT_LIMIT,
                        wire_bytes=None,
                        decoded_bytes=None,
                        ttfb_ms=None,
                        started=hop_started,
                        acquisition=acquisition,
                    )
                    raise FetchError(
                        "too many redirects",
                        error_code=ERROR_REDIRECT_LIMIT,
                    )
                next_url = urljoin(target.url, location)
                redirect_chain.append(
                    RedirectHop(
                        from_url=target.url,
                        to_url=next_url,
                        status_code=response.status_code,
                    )
                )
                self._trace(
                    attempts,
                    url=target.url,
                    method=request.method,
                    status_code=response.status_code,
                    error_code=None,
                    # Redirect bodies are deliberately unread.
                    wire_bytes=None,
                    decoded_bytes=None,
                    ttfb_ms=None,
                    started=hop_started,
                    acquisition=acquisition,
                )
                current_url = next_url
                continue

            # Terminal response: stream body under both caps.
            try:
                result = await self._read_body(
                    request=request,
                    target=target,
                    response=response,
                    redirect_chain=redirect_chain,
                    started=started,
                    max_wire=max_wire,
                    max_decoded=max_decoded,
                    acquisition=acquisition,
                )
            except FetchError as exc:
                self._trace(
                    attempts,
                    url=target.url,
                    method=request.method,
                    status_code=response.status_code,
                    error_code=exc.error_code,
                    wire_bytes=None,
                    decoded_bytes=None,
                    ttfb_ms=None,
                    started=hop_started,
                    acquisition=acquisition,
                )
                raise
            self._trace(
                attempts,
                url=target.url,
                method=request.method,
                status_code=result.status_code,
                error_code=None,
                wire_bytes=result.wire_bytes,
                decoded_bytes=result.decoded_bytes,
                ttfb_ms=result.ttfb_ms,
                started=hop_started,
                acquisition=acquisition,
            )
            return result

        raise FetchError("too many redirects", error_code=ERROR_REDIRECT_LIMIT)

    async def _resolve(
        self,
        url: str,
        *,
        root_registrable_domain: str | None,
        include_globs: list[str] | None,
        exclude_globs: list[str] | None,
        enforce_scope: bool,
        purpose: str,
    ) -> ResolvedTarget:
        # Redirects must use the same hard-admission policy as roots and
        # discovered links. Well-known robots/sitemap documents are connector
        # infrastructure rather than page candidates and are handled by their
        # dedicated request purposes.
        admission = classify_url_admission(
            url,
            root_registrable_domain=root_registrable_domain,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            infrastructure_purpose=purpose,
        )
        if (
            purpose in _ADMISSION_ENFORCED_PURPOSES
            and admission.reason_code in _HARD_ADMISSION_EXCLUSION_CODES
        ):
            raise FetchError(
                "URL rejected by admission policy",
                error_code=ERROR_URL_ADMISSION_REJECTED,
            )
        try:
            return await resolve_target(
                url,
                resolver=self._resolver,
                root_registrable_domain=root_registrable_domain,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                enforce_scope=enforce_scope,
                infrastructure_purpose=purpose,
            )
        except UrlPolicyError as exc:
            # Out-of-scope / disallowed scheme-port-userinfo on a redirect hop.
            raise FetchError(str(exc), error_code=ERROR_SSRF_BLOCKED) from exc

    def _finalize_no_body(
        self,
        request: FetchRequest,
        target: ResolvedTarget,
        response: httpx.Response,
        redirect_chain: list[RedirectHop],
        started: float,
        acquisition: AcquisitionProvenance,
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
            charset=_charset(response.headers),
            acquisition=acquisition,
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
        acquisition: AcquisitionProvenance,
    ) -> FetchResult:
        ttfb = int((time.monotonic() - started) * 1000)
        content_type = _content_type(response.headers)
        allowed = request.allowed_content_types
        # An empty status body (204/304) or HEAD carries no content-type; only
        # enforce the allowlist when one is set on the request. Error statuses
        # bypass the gate entirely (see ``_content_type_gate_applies``).
        if (
            allowed
            and content_type
            and content_type not in allowed
            and _content_type_gate_applies(response.status_code)
        ):
            await response.aclose()
            raise FetchError(
                f"unsupported content type: {content_type}",
                error_code=ERROR_UNSUPPORTED_CONTENT_TYPE,
                status_code=response.status_code,
            )

        decode, decompressor = _incremental_decoder(
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
                except zlib.error as exc:
                    # Malformed gzip/deflate: never mix raw wire bytes into a
                    # partially-decoded body (that would silently corrupt
                    # ``FetchResult.body``). Fail the fetch instead.
                    raise FetchError(
                        "malformed compressed response body",
                        error_code=ERROR_MALFORMED_RESPONSE,
                    ) from exc
                if out:
                    decoded_total += len(out)
                    if decoded_total > max_decoded:
                        raise FetchError(
                            "response exceeded decoded byte cap (compression bomb)",
                            error_code=ERROR_RESPONSE_TOO_LARGE,
                        )
                    decoded_chunks.append(out)
            if decompressor is not None:
                # Flush any tail buffered inside the decompressor, then confirm
                # the stream actually ended. A truncated gzip/deflate body
                # (connection cut before the final block) yields fewer bytes
                # WITHOUT raising ``zlib.error`` and leaves ``eof`` False; if
                # we accepted it we would silently persist a partial page as if
                # it were complete. The flushed tail is still subject to the
                # decoded cap so a bomb cannot smuggle bytes past the loop.
                try:
                    tail = decompressor.flush()
                except zlib.error as exc:
                    raise FetchError(
                        "malformed compressed response body",
                        error_code=ERROR_MALFORMED_RESPONSE,
                    ) from exc
                if tail:
                    decoded_total += len(tail)
                    if decoded_total > max_decoded:
                        raise FetchError(
                            "response exceeded decoded byte cap (compression bomb)",
                            error_code=ERROR_RESPONSE_TOO_LARGE,
                        )
                    decoded_chunks.append(tail)
                if not decompressor.eof:
                    raise FetchError(
                        "truncated compressed response body",
                        error_code=ERROR_MALFORMED_RESPONSE,
                        retryable=True,
                    )
        except httpx.TimeoutException as exc:
            raise FetchError(
                "request timed out",
                error_code=ERROR_TIMEOUT,
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            # A stream-read failure (e.g. connection reset mid-body) after
            # ``send()`` succeeded: classify it the same way as the request
            # phase instead of letting it propagate as an unclassified error.
            raise FetchError(
                f"connection error: {type(exc).__name__}",
                error_code=ERROR_CONNECTION_FAILED,
                retryable=True,
            ) from exc
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
            charset=_charset(response.headers),
            acquisition=acquisition,
        )
