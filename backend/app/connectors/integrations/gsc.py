"""Google Search Console data-API client (I6).

Pages the ``searchAnalytics.query`` endpoint behind the sync worker
(``app/workers/integration_worker.py``) over httpx with an injected
transport (test seam, mirroring ``connectors/discovery_models/factory.py``
and the sibling ``oauth.py``).

- Endpoints come from ``app.core.config.integrations`` and every URL is
  checked against the config-owned approved-host allow-list before a
  request is issued (SSRF policy). The guard and the rest of the HTTP
  plumbing (status classification, error-detail capping, pacing) are
  shared with the sibling clients via ``_http`` (invariant 2); the
  allow-list itself stays config-owned (invariant 1).
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

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

import httpx

from app.connectors.integrations._http import (
    IntegrationApiError,
    RequestPacer,
    assert_approved_url,
    classify_status,
    nested_error_detail,
    parse_retry_after,
)
from app.core.config.integrations import (
    ERROR_PROVIDER_API,
    GSC_API_BASE_URL,
    GSC_SEARCH_ANALYTICS_PATH,
    INTEGRATION_PROVIDER_GSC,
    integration_settings,
)


class GscApiError(IntegrationApiError):
    """A GSC API call failed; carries a config-owned error token."""


@dataclass(frozen=True)
class GscQueryPage:
    """One fetched ``searchAnalytics.query`` page.

    ``payload`` is the exact raw response body (what the immutable import
    artifact persists + hashes); ``rows`` is its ``rows`` list (absent on an
    empty result) with each entry the raw row dict
    (``keys``/``clicks``/``impressions``/``ctr``/``position``).
    ``raw_row_count`` is the provider's row count for the page BEFORE any
    client-side filtering (the worker's paging-termination measure); GSC
    rows pass through unfiltered, so it equals ``len(rows)``.
    """

    payload: dict
    rows: tuple[dict, ...]
    raw_row_count: int


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
        self._pacer = RequestPacer()

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
        assert_approved_url(url, label="GSC", error_type=GscApiError)
        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": list(dimensions),
            "rowLimit": integration_settings.sync_page_size,
            "startRow": start_row,
        }
        await self._pacer.wait(
            integration_settings.requests_per_minute(INTEGRATION_PROVIDER_GSC)
        )
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
            error_code, retryable = classify_status(response.status_code)
            try:
                detail = nested_error_detail(response.json())
            except ValueError:
                detail = ""
            suffix = f" ({detail})" if detail else ""
            raise GscApiError(
                f"GSC query returned HTTP {response.status_code}{suffix}",
                error_code=error_code,
                retryable=retryable,
                retry_after_seconds=parse_retry_after(response),
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
        return GscQueryPage(
            payload=payload, rows=tuple(rows), raw_row_count=len(rows)
        )


def build_gsc_client(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> GscClient:
    """Build a GSC client (``transport`` = test seam)."""
    return GscClient(transport=transport)
