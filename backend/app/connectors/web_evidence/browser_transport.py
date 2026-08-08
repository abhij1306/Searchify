"""Bundled headless-browser acquisition transport (rung 3 of the ladder).

The last rung. It renders a page locally with Patchright when server-rendered
evidence stays unusable — a JS shell, a challenge interstitial, or a response
too thin to analyze. There is deliberately no paid acquisition vendor and no
real-Chrome escalation anywhere in this module.

Ported from internal CrawlerAI (source commit
``bfc7663660285a70c88181c18005137d5f738d57``) as a MINIMAL generic observation
capability only:

| CrawlerAI source              | Ported here                                |
|-------------------------------|--------------------------------------------|
| ``browser_pool.py``           | ``_BrowserPool`` launch/context lifecycle   |
| ``browser_readiness.py``      | ``_wait_for_readiness`` DOM-settle wait     |
| ``browser_block_detection.py``| challenge diagnostics via the shared config |

Its extraction API, persistence, Celery/Redis wiring, UI, semantic extraction,
and real-Chrome code are deliberately NOT ported. Neither is its network-response
capture: it recorded same-site JSON descriptors that nothing downstream ever
read, so it paid for downloading and buffering payloads that were discarded.
Raw HTML stays a worker-memory input; this returns a bounded ``FetchResult`` and
nothing here reaches PostgreSQL except normalized facts and hashes.

``SecureFetcher`` still owns canonicalization, scope, admission, and DNS
pinning. The transport receives a target that already passed every check and
must not navigate anywhere else — enforced by ``_validate_resolved_target`` and
by aborting any main-frame navigation that leaves the validated authority.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

from app.connectors.web_evidence.contracts import (
    FetchError,
    FetchRequest,
    FetchResult,
    ResolvedTarget,
)
from app.connectors.web_evidence.targets import validate_resolved_target
from app.core.config.site_health import (
    ERROR_ACQUISITION_UNAVAILABLE,
    ERROR_CONNECTION_FAILED,
    ERROR_RESPONSE_TOO_LARGE,
    ERROR_TIMEOUT,
    PERSISTED_RESPONSE_HEADERS,
    SITE_HEALTH_USER_AGENT,
    site_health_settings,
)

logger = logging.getLogger("app.connectors.web_evidence.browser_transport")

# Resource kinds that never contribute to analyzable evidence. Blocking them is
# a latency and politeness measure, not a stealth one: a crawl that renders a
# page does not need its fonts, images, or media.
_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})
# Floor for the navigation timeout. Playwright treats 0 as "no timeout", so an
# already-exhausted budget must still be a short deadline, never an unbounded
# wait that holds a crawl slot forever.
_MIN_NAVIGATION_TIMEOUT_MS = 1_000


def _load_patchright() -> ModuleType:
    """Import Patchright, or fail closed with the standard unavailable token."""

    try:
        from patchright import async_api
    # An absent/incompatible optional dependency must not crash a crawl: the
    # caller treats an unavailable last rung as "keep the prior evidence".
    except Exception as exc:  # noqa: BLE001
        raise FetchError(
            "browser acquisition transport unavailable",
            error_code=ERROR_ACQUISITION_UNAVAILABLE,
        ) from exc
    return async_api


def _redacted_headers(headers: dict[str, str]) -> dict[str, str]:
    """Keep only the config allowlist, matched case-insensitively."""

    lowered = {str(key).casefold(): str(value) for key, value in headers.items()}
    return {
        key: value
        for key in sorted(PERSISTED_RESPONSE_HEADERS)
        if (value := lowered.get(key.casefold(), ""))
    }


def _content_type(headers: dict[str, str]) -> str:
    for key, value in headers.items():
        if str(key).casefold() == "content-type":
            return str(value).split(";", 1)[0].strip().lower()
    return ""


def _timeout_error_type() -> type[BaseException]:
    """Patchright's navigation-timeout class, or a never-matching fallback.

    Classifying on the exception TYPE rather than a message substring keeps a
    timeout distinguishable from a connection failure even when the driver
    changes its wording or runs in another locale.
    """

    try:
        return _load_patchright().TimeoutError
    # With no driver present nothing can raise its timeout: fall back to a
    # class that never matches so the general handler stays in charge.
    except Exception:  # noqa: BLE001
        return _NeverRaised


class _NeverRaised(Exception):
    """Sentinel exception type that is never raised."""


def _normalize_host(host: str) -> str:
    """Casefolded, trailing-dot-free host — the one comparison form."""
    return str(host or "").casefold().rstrip(".")


def _authority(url: str) -> tuple[str, str, int] | None:
    """``(scheme, host, port)`` with the default port made explicit, or ``None``.

    The port is resolved because the resolver validated exactly ONE authority:
    ``https://host:8443/`` is a different service from ``https://host/`` and was
    never admitted, so comparing hostnames alone would let a page reach it.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = (parts.scheme or "").casefold()
    host = _normalize_host(parts.hostname or "")
    if not host or scheme not in {"http", "https"}:
        return None
    return scheme, host, port or (443 if scheme == "https" else 80)


def _same_authority(url: str, target: ResolvedTarget) -> bool:
    """Whether ``url`` is on the exact authority the fetcher validated.

    Scheme, host, AND port must match. Hostname-only matching would admit an
    HTTPS-to-HTTP downgrade and any other port on the same machine — neither of
    which passed admission or the SSRF checks.
    """
    observed = _authority(url)
    if observed is None:
        return False
    validated = _authority(target.url)
    if validated is None:
        return False
    return observed == validated


def _host_resolver_rule(target: ResolvedTarget) -> str:
    """Chromium host-resolver rule pinning the one validated address.

    The browser must connect to exactly the address ``resolve_target``
    validated, while still sending the original Host header and TLS SNI — the
    same DNS-rebinding protection the HTTP rungs get by pinning the socket.
    ``MAP <host> <ip>`` pins the target. Everything else maps to ``~NOTFOUND``
    so NO other name resolves through this browser at all — not loopback, not
    link-local, not an internal hostname. Without that catch-all rule a page
    could still reach any address by naming it, which is precisely the SSRF
    surface the HTTP rungs close by pinning their socket.
    """

    address = (
        f"[{target.connect_ip}]" if ":" in target.connect_ip else target.connect_ip
    )
    # Normalized to the same form the request guard compares against. A host
    # carrying a trailing dot would otherwise produce a MAP rule the browser
    # never matches, and every request would fall through to ``~NOTFOUND``.
    return f"MAP {_normalize_host(target.host)} {address},MAP * ~NOTFOUND"


def _warn_if_unbalanced(rule: str, balance: int) -> None:
    """Report a lease released more times than it was acquired.

    Harmless where it is caught — the entry is cleared either way — but it means
    some path releases twice or acquires without a lease, and the next such bug
    could evict a browser out from under a live fetch. Silence would hide it.
    """
    if balance >= 0:
        return
    logger.warning(
        "browser pool lease released more times than acquired",
        extra={"resolver_rule": rule, "lease_balance": balance},
    )


async def _close_quietly(browser) -> None:
    """Close a browser without letting a dead process break the caller."""

    try:
        await browser.close()
    except Exception:  # noqa: BLE001
        pass


class _BrowserPool:
    """One launched browser per pinned address, with a fresh context per fetch.

    Chromium takes ``--host-resolver-rules`` only as a LAUNCH argument, so the
    validated-IP pin is a property of the browser process, not of a context.
    That makes the pin the pool's cache key: a target resolving to a different
    address gets its own browser rather than silently reusing one pinned
    elsewhere, which would let the second target dial an address it never
    validated. Contexts stay per-fetch so cookies and storage never leak
    between crawled pages.
    """

    def __init__(self, *, settings, user_agent: str) -> None:
        self._settings = settings
        self._user_agent = user_agent
        # Typed loosely: the driver is an optional dependency, so this module
        # must import and type-check without Patchright installed.
        self._playwright: Any = None
        # Insertion-ordered, so the first key is the least recently used once
        # every hit moves its key to the end.
        self._browsers: OrderedDict[str, Any] = OrderedDict()
        # In-flight fetches per pinned rule. A browser with a live lease is
        # never evicted: closing it would kill the context of a fetch already
        # navigating, turning an unrelated multi-host crawl into a stream of
        # spurious connection failures.
        self._leases: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def _launch_args(self, rule: str) -> list[str]:
        args = ["--disable-dev-shm-usage", f"--host-resolver-rules={rule}"]
        # Chromium's sandbox is a real containment boundary around code fetched
        # from crawled sites, so it is NOT disabled by default. Platforms that
        # genuinely cannot provide the required kernel capability opt in
        # explicitly rather than every deployment silently running unsandboxed.
        if self._settings.browser_disable_sandbox:
            args.append("--no-sandbox")
        return args

    async def _browser_for(self, rule: str):
        async with self._lock:
            existing = self._browsers.get(rule)
            if existing is not None:
                self._browsers.move_to_end(rule)
                # The cache hit takes a lease too. Counting only new launches
                # left every reused browser at zero, so it was immediately
                # evictable — the exact case the lease exists to prevent, and
                # the common one on a single-host crawl.
                self._leases[rule] = self._leases.get(rule, 0) + 1
                return existing
            api = _load_patchright()
            try:
                if self._playwright is None:
                    self._playwright = await api.async_playwright().start()
                browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=self._launch_args(rule),
                )
            except FetchError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise FetchError(
                    "browser acquisition could not launch",
                    error_code=ERROR_ACQUISITION_UNAVAILABLE,
                ) from exc
            # Each entry is a live browser PROCESS, so an unbounded pool leaks
            # one per distinct resolved address across a multi-host crawl.
            # Evict the least recently used IDLE browser before storing the new
            # one; a leased browser is skipped rather than closed under a fetch
            # that is still using it.
            await self._evict_idle()
            self._browsers[rule] = browser
            self._leases[rule] = self._leases.get(rule, 0) + 1
            return browser

    async def _evict_idle(self) -> None:
        """Close least-recently-used browsers with no live lease.

        The caller holds the pool lock.
        """
        limit = self._settings.browser_pool_max_browsers
        while len(self._browsers) >= limit:
            idle = next(
                (key for key in self._browsers if not self._leases.get(key)), None
            )
            if idle is None:
                # Every pooled browser is in use. Exceeding the soft cap is the
                # right call: refusing to launch would fail a fetch that already
                # passed admission, and the excess is released on completion.
                return
            evicted = self._browsers.pop(idle)
            self._leases.pop(idle, None)
            await _close_quietly(evicted)

    async def new_context(self, *, target: ResolvedTarget):
        """A fresh context on a browser pinned to this target's address.

        Takes a lease on that browser; the caller MUST ``release`` it.
        """

        rule = _host_resolver_rule(target)
        browser = await self._browser_for(rule)
        try:
            return await browser.new_context(
                user_agent=self._user_agent,
                ignore_https_errors=False,
                service_workers="block",
            )
        except Exception as exc:  # noqa: BLE001
            await self.release(target=target)
            raise FetchError(
                "browser context could not be opened",
                error_code=ERROR_ACQUISITION_UNAVAILABLE,
            ) from exc

    async def release(self, *, target: ResolvedTarget) -> None:
        """Drop one lease so an idle browser becomes evictable again."""

        rule = _host_resolver_rule(target)
        async with self._lock:
            remaining = self._leases.get(rule, 0) - 1
            if remaining > 0:
                self._leases[rule] = remaining
                return
            _warn_if_unbalanced(rule, remaining)
            self._leases.pop(rule, None)

    async def aclose(self) -> None:
        async with self._lock:
            browsers = list(self._browsers.values())
            playwright, self._playwright = self._playwright, None
            self._browsers.clear()
            self._leases.clear()
            for browser in browsers:
                await _close_quietly(browser)
            if playwright is not None:
                try:
                    await playwright.stop()
                # Shutdown is best-effort: a driver that already died must not
                # mask an error or block the fetcher's close path.
                except Exception:  # noqa: BLE001
                    pass


class PatchrightTransport:
    """Render one admitted target locally and return bounded evidence."""

    def __init__(
        self,
        *,
        settings=site_health_settings,
        user_agent: str = SITE_HEALTH_USER_AGENT,
    ) -> None:
        self._settings = settings
        self._user_agent = user_agent
        self._pool = _BrowserPool(settings=settings, user_agent=user_agent)

    async def aclose(self) -> None:
        await self._pool.aclose()

    async def fetch(
        self,
        request: FetchRequest,
        target: ResolvedTarget,
        *,
        max_wire_bytes: int,
        max_decoded_bytes: int,
        timeout_seconds: float,
    ) -> FetchResult:
        """Navigate, wait for readiness, and return the rendered document."""

        validate_resolved_target(target)
        del max_wire_bytes  # the rendered DOM is bounded by the decoded cap only
        started = time.monotonic()

        # Session setup runs INSIDE the guarded flow. A driver that cannot open
        # a context or install the route guard must surface as a ``FetchError``
        # like every other rung failure, because the caller treats an
        # unavailable last rung as "keep the prior server evidence" and catches
        # only ``FetchError`` — a raw driver exception would instead fail the
        # whole page that rung 1 had already fetched successfully.
        context = await self._pool.new_context(target=target)
        try:
            try:
                page = await context.new_page()
                await self._guard_requests(page, target=target)
            except Exception as exc:  # noqa: BLE001
                raise FetchError(
                    "browser session could not be prepared",
                    error_code=ERROR_ACQUISITION_UNAVAILABLE,
                ) from exc
            # Navigation gets the tighter of the caller's budget and the
            # configured navigation ceiling: a generous per-request timeout
            # must not let one render hold a crawl slot indefinitely.
            navigation_budget = min(
                timeout_seconds, self._settings.browser_navigation_timeout_seconds
            )
            response = await self._navigate(page, target, navigation_budget)
            # Readiness shares the caller's deadline rather than extending it:
            # navigation + readiness must not exceed the budget the fetcher
            # allotted this rung, or a slow page silently doubles the timeout.
            elapsed = time.monotonic() - started
            await self._wait_for_readiness(page, remaining=timeout_seconds - elapsed)
            body = await self._rendered_body(page, max_decoded_bytes)
            headers = dict(response.headers) if response is not None else {}
            status = int(response.status) if response is not None else 200
            final_url = str(page.url or target.url)
            latency_ms = int((time.monotonic() - started) * 1000)
            return FetchResult(
                requested_url=request.url,
                final_url=final_url,
                status_code=status,
                redacted_headers=_redacted_headers(headers),
                content_type=_content_type(headers) or "text/html",
                http_version="",
                body=body,
                wire_bytes=len(body),
                decoded_bytes=len(body),
                ttfb_ms=None,
                latency_ms=latency_ms,
                charset="utf-8",
            )
        finally:
            # The context owns the page, its listeners, and its share of
            # browser memory, so closing it is what keeps a long crawl bounded.
            # Releasing the pool lease last is what lets an idle browser be
            # evicted again — and what stopped this fetch's browser from being
            # closed underneath it.
            try:
                await context.close()
            except Exception:  # noqa: BLE001
                pass
            await self._pool.release(target=target)

    async def _rendered_body(self, page, max_decoded_bytes: int) -> bytes:
        """Serialize the rendered DOM, bounded IN BYTES inside the page.

        The cap is applied by the browser rather than after materializing the
        whole document in this process, so a hostile page cannot force an
        unbounded string across the CDP boundary first.

        The bound is measured in UTF-8 BYTES, not characters. Slicing by
        characters and then checking the encoded length rejected any page whose
        multibyte content encoded above the cap while its character count was
        well under it — which on a Hindi or Chinese site is an ordinary page,
        refused for being written in a non-Latin script.

        The page measures the encoded size and returns the document only when
        it fits, so an oversized one costs one integer across the CDP boundary
        rather than the whole string. It is then REFUSED rather than silently
        truncated: partial evidence analyzed as if complete is worse than no
        evidence at all.
        """

        script = """
            limit => {
                const encoder = new TextEncoder();
                const html = document.documentElement.outerHTML;
                const bytes = encoder.encode(html);
                return {
                    size: bytes.length,
                    html: bytes.length > limit ? '' : html,
                };
            }
        """
        try:
            result = await page.evaluate(script, max_decoded_bytes)
        # A page that cannot be serialized (navigated away, closed) has no
        # rendered evidence to offer.
        except Exception as exc:  # noqa: BLE001
            raise FetchError(
                "rendered document could not be read",
                error_code=ERROR_CONNECTION_FAILED,
                retryable=True,
            ) from exc
        payload = result if isinstance(result, dict) else {}
        if int(payload.get("size") or 0) > max_decoded_bytes:
            raise FetchError(
                "rendered document exceeded the decoded cap",
                error_code=ERROR_RESPONSE_TOO_LARGE,
            )
        return str(payload.get("html") or "").encode("utf-8", errors="replace")

    async def _guard_requests(self, page, *, target: ResolvedTarget) -> None:
        """Confine every request to the exact authority the fetcher validated.

        The fetcher validated exactly ONE authority. Guarding only main-frame
        navigation would still let subresources, XHR, and iframes reach any
        host the page names — addresses that never passed admission or the SSRF
        checks. So every request outside that authority is aborted, whatever its
        resource type, and the comparison includes the scheme and port: another
        port on the same machine is a different service, and an HTTPS-to-HTTP
        downgrade is a different guarantee.
        """

        await page.route("**/*", self.route_handler(target))

    @staticmethod
    def route_handler(target: ResolvedTarget):
        """The production route guard, as an installable handler.

        Exposed so a live test can exercise the SHIPPED guard rather than a
        re-implementation of it — a duplicated handler in a test proves only
        that the duplicate works.
        """

        async def _route(route, request):
            try:
                if request.resource_type in _BLOCKED_RESOURCE_TYPES:
                    await route.abort()
                    return
                if not _same_authority(request.url, target):
                    await route.abort()
                    return
                await route.continue_()
            # A route can be resolved by the browser before we answer it; that
            # is not an acquisition failure.
            except Exception:  # noqa: BLE001
                pass

        return _route

    async def _navigate(self, page, target: ResolvedTarget, timeout_seconds: float):
        timeout_error = _timeout_error_type()
        # Playwright reads ``timeout=0`` as "no timeout at all", so a budget
        # that has already been consumed would remove the deadline instead of
        # tripping it immediately — the opposite of what an exhausted budget
        # means. Clamped to a floor, matching the readiness wait's own guard.
        budget_ms = max(_MIN_NAVIGATION_TIMEOUT_MS, int(timeout_seconds * 1000))
        try:
            return await page.goto(
                target.url,
                timeout=budget_ms,
                wait_until="domcontentloaded",
            )
        except timeout_error as exc:
            raise FetchError(
                "browser navigation timed out",
                error_code=ERROR_TIMEOUT,
                retryable=True,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise FetchError(
                "browser navigation failed",
                error_code=ERROR_CONNECTION_FAILED,
                retryable=True,
            ) from exc

    async def _wait_for_readiness(self, page, *, remaining: float) -> None:
        """Give client-rendered content a bounded chance to appear.

        ``domcontentloaded`` fires before a JS shell has rendered anything, so
        rendering without this wait routinely captures the same empty shell the
        HTTP rungs already saw. Waiting for the network to settle is bounded by
        whatever is LEFT of the caller's deadline and is best-effort: a page
        that never goes idle still yields whatever it rendered by then, which
        is strictly better evidence than the shell.
        """

        budget = min(self._settings.browser_readiness_timeout_seconds, remaining)
        if budget <= 0:
            return
        try:
            await page.wait_for_load_state("networkidle", timeout=budget * 1000)
        # A busy page (polling, websockets, ads) never reaches networkidle.
        # That is an expected outcome, not an acquisition failure.
        except Exception:  # noqa: BLE001
            pass
