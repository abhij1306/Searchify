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
    GA4_API_BASE_URL,
    GA4_RUN_REPORT_PATH,
    INTEGRATION_DATASET_TEMPLATES,
    INTEGRATION_PROVIDER_GA4,
    IntegrationDatasetTemplate,
    integration_settings,
)


class Ga4ApiError(IntegrationApiError):
    """A GA4 API call failed; carries a config-owned error token."""


@dataclass(frozen=True)
class Ga4ReportPage:
    """One fetched ``runReport`` page (the worker/derivation contract).

    ``payload`` is the normalized report document the immutable import
    artifact persists + hashes; ``rows`` is its ``rows`` list (absent on
    an empty result) with each entry the normalized row dict
    (``keys`` + one entry per template metric). ``raw_row_count`` is the
    provider's row count for the page BEFORE normalization dropped any
    malformed rows — the worker's paging-termination measure (a full raw
    page with dropped rows must not look like a short final page).
    """

    payload: dict
    rows: tuple[dict, ...]
    raw_row_count: int


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
    ) -> Ga4ReportPage:
        """Fetch ONE page of report rows (the worker's uniform contract).

        The method name + signature mirror the GSC reference client — the
        worker pages every provider through this one seam; here it issues
        a GA4 ``runReport`` request. The caller owns paging: request pages
        at ``start_row`` offsets of ``sync_page_size`` until a page's RAW
        row count (``raw_row_count``, BEFORE normalization drops malformed
        rows) comes back short of the page size. Raises ``Ga4ApiError``
        on any failure (classified, never carrying the token).
        """
        template = _ga4_template_for_dimensions(dimensions)
        url = GA4_API_BASE_URL + GA4_RUN_REPORT_PATH.format(
            property_ref=quote(property_ref, safe="")
        )
        assert_approved_url(url, label="GA4", error_type=Ga4ApiError)
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
        await self._pacer.wait(
            integration_settings.requests_per_minute(INTEGRATION_PROVIDER_GA4)
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
            raise Ga4ApiError(
                f"GA4 runReport request failed: {type(exc).__name__}",
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
            raise Ga4ApiError(
                f"GA4 runReport returned HTTP {response.status_code}{suffix}",
                error_code=error_code,
                retryable=retryable,
                retry_after_seconds=parse_retry_after(response),
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
        return Ga4ReportPage(
            payload=payload, rows=rows, raw_row_count=len(raw_rows)
        )


def build_ga4_client(
    *, transport: httpx.AsyncBaseTransport | None = None
) -> Ga4Client:
    """Build a GA4 client (``transport`` = test seam)."""
    return Ga4Client(transport=transport)
