"""Google Search Console data-API client (I6).

Pages the ``searchAnalytics.query`` endpoint behind the sync worker
(``app/workers/integration_worker.py``) over httpx with an injected
transport (test seam, mirroring ``connectors/discovery_models/factory.py``
and the sibling ``oauth.py``).

- Endpoints come from ``app.core.config.integrations`` and every URL is
  checked against the config-owned approved-host allow-list before a
  request is issued (SSRF policy). The OAuth module's identical guard is
  module-private, so the check is mirrored here against the SAME config
  allow-list (the allow-list itself stays config-owned — invariant 1/2).
- Paging + timeout + per-provider rate-limit knobs are read from
  ``integration_settings`` (invariant 1): one request fetches at most
  ``sync_page_size`` rows and requests are paced to the provider's
  requests/minute budget.
- The Bearer access token passes through this module but is NEVER logged
  (invariant 6): raised errors carry only HTTP status codes and
  config-owned error tokens, with provider error text length-capped.

The cheap authenticated grant probe (``GET /webmasters/v3/sites``) already
exists as ``IntegrationOAuthClient.probe_access_token`` (I3) and is
deliberately NOT duplicated here (invariant 2).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote, urlsplit

import httpx

from app.core.config.integrations import (
    ERROR_GRANT_AUTH_FAILED,
    ERROR_PROVIDER_API,
    ERROR_RATE_LIMITED,
    ERROR_UNAPPROVED_ENDPOINT,
    GSC_API_BASE_URL,
    GSC_SEARCH_ANALYTICS_PATH,
    INTEGRATION_APPROVED_ENDPOINT_HOSTS,
    integration_settings,
)

# Cap on provider-supplied error text surfaced in exceptions (defensive:
# keeps messages short even if the provider returns a huge error body).
_ERROR_DETAIL_MAX_LEN = 240


class GscApiError(RuntimeError):
    """A GSC API call failed; carries a config-owned error token."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class GscQueryPage:
    """One fetched ``searchAnalytics.query`` page.

    ``payload`` is the exact raw response body (what the immutable import
    artifact persists + hashes); ``rows`` is its ``rows`` list (absent on an
    empty result) with each entry the raw row dict
    (``keys``/``clicks``/``impressions``/``ctr``/``position``).
    """

    payload: dict
    rows: tuple[dict, ...]


def _assert_approved_url(url: str) -> None:
    """SSRF guard: integration clients only call allow-listed hosts (config)."""
    host = (urlsplit(url).hostname or "").lower()
    if host not in INTEGRATION_APPROVED_ENDPOINT_HOSTS:
        raise GscApiError(
            f"GSC endpoint host is not approved: {host or '<none>'}",
            error_code=ERROR_UNAPPROVED_ENDPOINT,
        )


def _classify_status(status_code: int) -> tuple[str, bool]:
    """Map an HTTP status to a config-owned (error_code, retryable) pair."""
    if status_code == 429:
        return ERROR_RATE_LIMITED, True
    if status_code in (401, 403):
        return ERROR_GRANT_AUTH_FAILED, False
    return ERROR_PROVIDER_API, status_code in (500, 502, 503, 504)


def _safe_error_detail(payload: object) -> str:
    """Extract the length-capped ``error.message`` from a GSC error body."""
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if not isinstance(error, dict):
        return ""
    return str(error.get("message") or "").strip()[:_ERROR_DETAIL_MAX_LEN]


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class GscClient:
    """GSC ``searchAnalytics.query`` client with pacing + injected transport.

    ``transport`` is the test seam (``httpx.MockTransport`` or any
    ``httpx.AsyncBaseTransport``); production passes nothing and the client
    uses the real network.
    """

    def __init__(
        self, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._transport = transport
        self._last_request_at: float | None = None

    async def _pace(self) -> None:
        """Enforce the provider's requests/minute budget between requests."""
        min_interval = 60.0 / integration_settings.gsc_requests_per_minute
        now = time.monotonic()
        if self._last_request_at is not None:
            delay = min_interval - (now - self._last_request_at)
            if delay > 0:
                await asyncio.sleep(delay)
        self._last_request_at = time.monotonic()

    async def query_search_analytics(
        self,
        *,
        access_token: str,
        property_ref: str,
        dimensions: Sequence[str],
        start_date: date,
        end_date: date,
        start_row: int,
    ) -> GscQueryPage:
        """Fetch ONE page of search analytics rows.

        The caller owns paging: request pages at ``start_row`` offsets of
        ``sync_page_size`` until a page returns fewer rows than the page
        size. Raises ``GscApiError`` on any failure (classified, never
        carrying the token).
        """
        url = GSC_API_BASE_URL + GSC_SEARCH_ANALYTICS_PATH.format(
            property_ref=quote(property_ref, safe="")
        )
        _assert_approved_url(url)
        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": list(dimensions),
            "rowLimit": integration_settings.sync_page_size,
            "startRow": start_row,
        }
        await self._pace()
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=integration_settings.sync_request_timeout_seconds,
            ) as client:
                response = await client.post(
                    url,
                    json=body,
                    # Set per-request and never logged (invariant 6).
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise GscApiError(
                f"GSC query request failed: {type(exc).__name__}",
                error_code=ERROR_PROVIDER_API,
                retryable=True,
            ) from exc
        if response.status_code != 200:
            error_code, retryable = _classify_status(response.status_code)
            try:
                detail = _safe_error_detail(response.json())
            except ValueError:
                detail = ""
            suffix = f" ({detail})" if detail else ""
            raise GscApiError(
                f"GSC query returned HTTP {response.status_code}{suffix}",
                error_code=error_code,
                retryable=retryable,
                retry_after_seconds=_parse_retry_after(response),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GscApiError(
                "GSC query returned a non-JSON body",
                error_code=ERROR_PROVIDER_API,
            ) from exc
        if not isinstance(payload, dict):
            raise GscApiError(
                "GSC query returned an unexpected body",
                error_code=ERROR_PROVIDER_API,
            )
        rows = payload.get("rows")
        if rows is None:
            # An empty result omits the rows key entirely.
            rows = []
        if not isinstance(rows, list):
            raise GscApiError(
                "GSC query returned malformed rows",
                error_code=ERROR_PROVIDER_API,
            )
        return GscQueryPage(payload=payload, rows=tuple(rows))


def build_gsc_client(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> GscClient:
    """Build a GSC client (``transport`` = test seam)."""
    return GscClient(transport=transport)
