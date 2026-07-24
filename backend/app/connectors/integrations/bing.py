"""Bing Webmaster API client (I12).

Reads the Bing Webmaster API v1 stats endpoints behind the sync worker
(``app/workers/integration_worker.py``) over httpx with an injected
transport (test seam), mirroring the GSC reference client
(``app/connectors/integrations/gsc.py``) contract-for-contract.

Pinned literals (plan R3, from Microsoft documentation):

- Host ``ssl.bing.com``, JSON endpoint root ``/webmaster/api.svc/json/``
  (learn.microsoft.com/dotnet/api/microsoft.bing.webmaster.api.interfaces
  .iwebmasterapi + /bingwebmaster/getting-access) — config-owned
  (``BING_API_BASE_URL`` / ``BING_API_JSON_ROOT``) and allow-listed.
- Read endpoints ``GetPageStats`` / ``GetQueryStats`` (GET, ``siteUrl``
  query parameter), returning ``{"d": [...]}`` rows with ``Clicks`` /
  ``Impressions`` / ``Date`` / ``Query`` fields; the OAuth2 user-token
  flow authenticates with the ``Authorization: Bearer`` header
  (learn.microsoft.com/bingwebmaster/oauth2). The Microsoft grant rides
  the identity-platform transport (I3) with the pinned
  ``webmaster.manage`` scope — the apikey variant is NOT used.
- The stats endpoints take NO date-range or paging parameters: one GET
  returns the provider's trailing stats window. The run's window is
  therefore projected at DERIVATION time (out-of-window rows are dropped
  there, never here — the artifact keeps the faithful full response),
  and a request at ``start_row > 0`` short-circuits to an empty page.

Row-shape mapping (the derivation contract): rows are normalized into
the SAME shape the GSC client emits — ``{"rows": [{"keys": [<leading
dimension>, <ISO date>], "clicks": n, "impressions": n}]}``. The leading
dimension is the response's ``Query`` field (the page URL for
``GetPageStats``, the query text for ``GetQueryStats``); Bing's
serialized ``/Date(<epoch-ms><offset>)/`` form is normalized to ISO
``YYYY-MM-DD`` (the UTC calendar day of the epoch instant). A row with a
malformed date or non-numeric metric is dropped, never guessed.

The Bearer access token passes through this module but is NEVER logged
(invariant 6): raised errors carry only HTTP status codes and
config-owned error tokens, with provider error text length-capped.

``BingClient.probe_access_token`` is the cheap authenticated grant probe
behind ``POST /integrations/{id}/test`` for Microsoft grants (the I12
replacement for the refresh round-trip placeholder): ``GetSites``
returns the caller's verified-site list — the analogue of the GSC
``GET /webmasters/v3/sites`` probe.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

from app.connectors.integrations._http import (
    IntegrationApiError,
    RequestPacer,
    assert_approved_url,
    classify_status,
    flat_error_detail,
    parse_retry_after,
)
from app.core.config.integrations import (
    BING_API_BASE_URL,
    BING_API_JSON_ROOT,
    BING_SITES_PROBE_METHOD,
    ERROR_PROVIDER_API,
    INTEGRATION_DATASET_TEMPLATES,
    INTEGRATION_PROVIDER_BING,
    IntegrationDatasetTemplate,
    integration_settings,
)

# Bing's JSON-serialized date form: ``/Date(<epoch-ms><+/-hhmm>)/``.
_BING_DATE_RE = re.compile(r"^/Date\((\d+)([+-]\d{4})?\)/$")


class BingApiError(IntegrationApiError):
    """A Bing API call failed; carries a config-owned error token."""


@dataclass(frozen=True)
class BingStatsPage:
    """One fetched stats page (the worker/derivation contract).

    ``payload`` is the normalized stats document the immutable import
    artifact persists + hashes; ``rows`` is its ``rows`` list with each
    entry the normalized row dict (``keys`` + ``clicks``/``impressions``).
    """

    payload: dict
    rows: tuple[dict, ...]


def _bing_template_for_dimensions(
    dimensions: Sequence[str],
) -> IntegrationDatasetTemplate:
    """Resolve the config-owned Bing dataset template being paged.

    The template owns the pinned endpoint literal (``api_method``) and
    the metric set. An unknown dimension tuple fails loud — the config
    templates are the only dataset vocabulary.
    """
    for template in INTEGRATION_DATASET_TEMPLATES.values():
        if template.provider == INTEGRATION_PROVIDER_BING and tuple(
            template.dimensions
        ) == tuple(dimensions):
            return template
    raise BingApiError(
        f"no Bing dataset template for dimensions {tuple(dimensions)!r}",
        error_code=ERROR_PROVIDER_API,
    )


def _parse_bing_date(raw: object) -> str | None:
    """Normalize Bing's ``/Date(<epoch-ms><offset>)/`` to ISO YYYY-MM-DD.

    The epoch milliseconds are an absolute instant; the UTC calendar day
    is the deterministic projection (the trailing offset is the server's
    local zone, not data). Returns ``None`` for any other shape.
    """
    match = _BING_DATE_RE.match(str(raw).strip())
    if match is None:
        return None
    instant = datetime.fromtimestamp(int(match.group(1)) / 1000, UTC)
    return instant.date().isoformat()


def _coerce_count(raw: object) -> int | None:
    """Coerce one Bing click/impression count; ``None`` = malformed."""
    try:
        value = int(str(raw).strip())
    except ValueError:
        return None
    return value if value >= 0 else None


def _normalize_row(
    row: object, template: IntegrationDatasetTemplate
) -> dict | None:
    """Map one raw ``d``-array row into the GSC-shaped contract row.

    ``Query`` carries the leading dimension value (page URL or query
    text); ``Date`` is normalized to ISO. Returns ``None`` for a row
    whose fields are malformed.
    """
    if not isinstance(row, dict):
        return None
    leading = row.get("Query")
    if leading is None:
        return None
    row_date = _parse_bing_date(row.get("Date"))
    if row_date is None:
        return None
    normalized: dict = {"keys": [str(leading), row_date]}
    # Bing field names are the PascalCase forms of the template's metric
    # tokens (Clicks/Impressions) — match case-insensitively.
    fields = {str(key).lower(): value for key, value in row.items()}
    for name in template.metrics:
        value = _coerce_count(fields.get(name))
        if value is None:
            return None
        normalized[name] = value
    return normalized


class BingClient:
    """Bing Webmaster stats client with pacing + injected transport.

    ``transport`` is the test seam (``httpx.MockTransport`` or any
    ``httpx.AsyncBaseTransport``); production passes nothing and the client
    uses the real network.
    """

    def __init__(
        self, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._transport = transport
        self._pacer = RequestPacer()

    async def _get(
        self, url: str, *, access_token: str, params: dict[str, str], action: str
    ) -> dict:
        """Issue one authenticated GET and return the JSON object.

        The Bearer token is set per-request and never logged (invariant
        6); raised errors carry only the HTTP status and the provider's
        capped detail.
        """
        assert_approved_url(url, label="Bing", error_type=BingApiError)
        await self._pacer.wait(
            integration_settings.requests_per_minute(INTEGRATION_PROVIDER_BING)
        )
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=integration_settings.sync_request_timeout_seconds,
            ) as client:
                response = await client.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise BingApiError(
                f"Bing {action} request failed: {type(exc).__name__}",
                error_code=ERROR_PROVIDER_API,
                retryable=True,
            ) from exc
        if response.status_code != 200:
            error_code, retryable = classify_status(response.status_code)
            try:
                detail = flat_error_detail(
                    response.json(), ("Message", "message", "error")
                )
            except ValueError:
                detail = ""
            suffix = f" ({detail})" if detail else ""
            raise BingApiError(
                f"Bing {action} returned HTTP {response.status_code}{suffix}",
                error_code=error_code,
                retryable=retryable,
                retry_after_seconds=parse_retry_after(response),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise BingApiError(
                f"Bing {action} returned a non-JSON body",
                error_code=ERROR_PROVIDER_API,
            ) from exc
        if not isinstance(payload, dict):
            raise BingApiError(
                f"Bing {action} returned an unexpected body",
                error_code=ERROR_PROVIDER_API,
            )
        return payload

    async def query_search_analytics(
        self,
        *,
        access_token: str,
        property_ref: str,
        dimensions: Sequence[str],
        start_date: date,
        end_date: date,
        start_row: int,
    ) -> BingStatsPage:
        """Fetch the stats page for one dataset (the worker's contract).

        The method name + signature mirror the GSC reference client — the
        worker pages every provider through this one seam; here it issues
        one ``GetPageStats``/``GetQueryStats`` GET. The Bing stats API is
        unpaged (no date-range or offset parameters — the window is
        projected at derivation), so a request at ``start_row > 0``
        short-circuits to an empty page and the single fetched page ends
        paging. Raises ``BingApiError`` on any failure (classified, never
        carrying the token).
        """
        template = _bing_template_for_dimensions(dimensions)
        if start_row > 0:
            # Unpaged single-shot API: there is no second page.
            return BingStatsPage(payload={"rows": []}, rows=())
        url = f"{BING_API_BASE_URL}{BING_API_JSON_ROOT}{template.api_method}"
        payload = await self._get(
            url,
            access_token=access_token,
            params={"siteUrl": property_ref},
            action=template.api_method,
        )
        raw_rows = payload.get("d") or []
        if not isinstance(raw_rows, list):
            raise BingApiError(
                f"Bing {template.api_method} returned malformed rows",
                error_code=ERROR_PROVIDER_API,
            )
        rows = tuple(
            normalized
            for normalized in (_normalize_row(row, template) for row in raw_rows)
            if normalized is not None
        )
        # The persisted payload is the faithful normalized stats document
        # (the derivation contract).
        return BingStatsPage(payload={"rows": list(rows)}, rows=rows)

    async def probe_access_token(self, *, access_token: str) -> None:
        """Cheap authenticated probe validating a Microsoft grant's token.

        GETs the caller's verified-site list (``GetSites``) with the
        Bearer token (never logged) — the analogue of the GSC sites probe
        on the shared Google grant. Raises ``BingApiError`` on any failure.
        """
        url = f"{BING_API_BASE_URL}{BING_API_JSON_ROOT}{BING_SITES_PROBE_METHOD}"
        await self._get(url, access_token=access_token, params={}, action="probe")


def build_bing_client(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> BingClient:
    """Build a Bing client (``transport`` = test seam)."""
    return BingClient(transport=transport)
