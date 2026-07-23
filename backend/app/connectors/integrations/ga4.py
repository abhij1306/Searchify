"""Google Analytics 4 (GA4) Data API client (I11).

Pages the ``runReport`` endpoint behind the sync worker
(``app/workers/integration_worker.py``) over httpx with an injected
transport (test seam), mirroring the GSC reference client
(``app/connectors/integrations/gsc.py``) contract-for-contract:

- Endpoints come from ``app.core.config.integrations`` and every URL is
  checked against the config-owned approved-host allow-list before a
  request is issued (SSRF policy). GA4 rides the ONE shared Google grant
  (no new OAuth) — the grant's access token authorizes both the GSC and
  the GA4 connection.
- Paging (``limit``/``offset``), timeout, and the per-provider
  requests/minute budget are read from ``integration_settings``
  (invariant 1); the caller (the worker) owns paging exactly as for GSC.
- The dataset templates are config-owned (contract C1): the request's
  dimensions/metrics are the template's declared tuples, resolved by
  matching the paged dimensions against the GA4 templates — never
  hard-coded here.
- The Bearer access token passes through this module but is NEVER logged
  (invariant 6): raised errors carry only HTTP status codes and
  config-owned error tokens, with provider error text length-capped.

**Row-shape mapping (the derivation contract):** the GA4 ``runReport``
response rows (``dimensionValues``/``metricValues``, all values strings)
are normalized into the SAME row shape the GSC client emits and the
worker/derivation consumes — ``{"rows": [{"keys": [...dims in declared
order...], "<metric>": <number>, ...}]}`` (the I9 derivation transform is
pinned against this shape). The page ``payload`` persisted on the
immutable import artifact is that faithful normalized document: every
returned row, every template metric, original values (GA4's compact
``"20260720"`` date form preserved), positional ``dimensionValues``/
``metricValues`` mapped by the config template. Metric strings are
coerced to numbers deterministically (integer-valued → ``int``, else
``float``); a row with the wrong arity or a non-numeric metric value is
dropped, never guessed. Rows with no data simply omit the ``rows`` key
(GA4 mirrors GSC's empty-result shape).

The cheap authenticated grant probe already exists as
``IntegrationOAuthClient.probe_access_token`` (I3 — the GSC site list
validates the ONE shared Google grant behind either connection) and is
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
    GA4_API_BASE_URL,
    GA4_RUN_REPORT_PATH,
    INTEGRATION_APPROVED_ENDPOINT_HOSTS,
    INTEGRATION_DATASET_TEMPLATES,
    INTEGRATION_PROVIDER_GA4,
    IntegrationDatasetTemplate,
    integration_settings,
)

# Cap on provider-supplied error text surfaced in exceptions (defensive:
# keeps messages short even if the provider returns a huge error body).
_ERROR_DETAIL_MAX_LEN = 240


class Ga4ApiError(RuntimeError):
    """A GA4 API call failed; carries a config-owned error token."""

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
class Ga4ReportPage:
    """One fetched ``runReport`` page (the worker/derivation contract).

    ``payload`` is the normalized report document the immutable import
    artifact persists + hashes; ``rows`` is its ``rows`` list (absent on
    an empty result) with each entry the normalized row dict
    (``keys`` + one entry per template metric).
    """

    payload: dict
    rows: tuple[dict, ...]


def _ga4_template_for_dimensions(
    dimensions: Sequence[str],
) -> IntegrationDatasetTemplate:
    """Resolve the config-owned GA4 dataset template being paged.

    The worker pages each config dataset by its declared dimensions; the
    matching template owns the request's metric set (contract C1). An
    unknown dimension tuple fails loud — the config templates are the
    only dataset vocabulary.
    """
    for template in INTEGRATION_DATASET_TEMPLATES.values():
        if template.provider == INTEGRATION_PROVIDER_GA4 and tuple(
            template.dimensions
        ) == tuple(dimensions):
            return template
    raise Ga4ApiError(
        f"no GA4 dataset template for dimensions {tuple(dimensions)!r}",
        error_code=ERROR_PROVIDER_API,
    )


def _assert_approved_url(url: str) -> None:
    """SSRF guard: integration clients only call allow-listed hosts (config)."""
    host = (urlsplit(url).hostname or "").lower()
    if host not in INTEGRATION_APPROVED_ENDPOINT_HOSTS:
        raise Ga4ApiError(
            f"GA4 endpoint host is not approved: {host or '<none>'}",
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
    """Extract the length-capped ``error.message`` from a GA4 error body."""
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


def _coerce_metric_value(raw: object) -> int | float | None:
    """Coerce one GA4 string metric value to a number (deterministic).

    GA4 ``metricValues`` are always strings. Integer-valued strings become
    ``int``, other numeric strings ``float``; a non-numeric value is
    malformed provider data — ``None`` drops the row, never guessed.
    """
    text = str(raw).strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_row(
    row: object, template: IntegrationDatasetTemplate
) -> dict | None:
    """Map one raw ``runReport`` row into the GSC-shaped contract row.

    Positional ``dimensionValues``/``metricValues`` are mapped by the
    template's declared tuples; the date dimension keeps GA4's compact
    raw value (the derivation transform parses it). Returns ``None`` for
    a row whose arity or metric values are malformed.
    """
    if not isinstance(row, dict):
        return None
    dimension_values = row.get("dimensionValues")
    metric_values = row.get("metricValues")
    if not isinstance(dimension_values, list) or not isinstance(metric_values, list):
        return None
    if len(dimension_values) != len(template.dimensions):
        return None
    if len(metric_values) < len(template.metrics):
        return None
    keys: list[str] = []
    for entry in dimension_values:
        if not isinstance(entry, dict) or "value" not in entry:
            return None
        keys.append(str(entry["value"]))
    normalized: dict = {"keys": keys}
    for index, name in enumerate(template.metrics):
        entry = metric_values[index]
        if not isinstance(entry, dict) or "value" not in entry:
            return None
        value = _coerce_metric_value(entry["value"])
        if value is None:
            return None
        normalized[name] = value
    return normalized


class Ga4Client:
    """GA4 ``runReport`` client with pacing + injected transport.

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
        min_interval = 60.0 / integration_settings.ga4_requests_per_minute
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
    ) -> Ga4ReportPage:
        """Fetch ONE page of report rows (the worker's uniform contract).

        The method name + signature mirror the GSC reference client — the
        worker pages every provider through this one seam; here it issues
        a GA4 ``runReport`` request. The caller owns paging: request pages
        at ``start_row`` offsets of ``sync_page_size`` until a page
        returns fewer rows than the page size. Raises ``Ga4ApiError`` on
        any failure (classified, never carrying the token).
        """
        template = _ga4_template_for_dimensions(dimensions)
        url = GA4_API_BASE_URL + GA4_RUN_REPORT_PATH.format(
            property_ref=quote(property_ref, safe="")
        )
        _assert_approved_url(url)
        body = {
            "dateRanges": [
                {
                    "startDate": start_date.isoformat(),
                    "endDate": end_date.isoformat(),
                }
            ],
            "dimensions": [{"name": name} for name in template.dimensions],
            "metrics": [{"name": name} for name in template.metrics],
            "limit": integration_settings.sync_page_size,
            "offset": start_row,
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
            raise Ga4ApiError(
                f"GA4 runReport request failed: {type(exc).__name__}",
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
            raise Ga4ApiError(
                f"GA4 runReport returned HTTP {response.status_code}{suffix}",
                error_code=error_code,
                retryable=retryable,
                retry_after_seconds=_parse_retry_after(response),
            )
        try:
            report = response.json()
        except ValueError as exc:
            raise Ga4ApiError(
                "GA4 runReport returned a non-JSON body",
                error_code=ERROR_PROVIDER_API,
            ) from exc
        if not isinstance(report, dict):
            raise Ga4ApiError(
                "GA4 runReport returned an unexpected body",
                error_code=ERROR_PROVIDER_API,
            )
        raw_rows = report.get("rows") or []
        if not isinstance(raw_rows, list):
            raise Ga4ApiError(
                "GA4 runReport returned malformed rows",
                error_code=ERROR_PROVIDER_API,
            )
        rows = tuple(
            normalized
            for normalized in (_normalize_row(row, template) for row in raw_rows)
            if normalized is not None
        )
        # The persisted payload is the faithful normalized report document
        # (the derivation contract); ``rowCount`` is carried through when
        # the provider reports it.
        payload: dict = {"rows": list(rows)}
        if "rowCount" in report:
            payload["rowCount"] = report["rowCount"]
        return Ga4ReportPage(payload=payload, rows=rows)


def build_ga4_client(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> Ga4Client:
    """Build a GA4 client (``transport`` = test seam)."""
    return Ga4Client(transport=transport)
