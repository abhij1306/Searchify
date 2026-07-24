"""Component tests for the Traffic read API (A10, httpx ASGITransport).

Pins the A10 acceptance:
  - contract C6: every served DTO mirrors the frontend zod ``.strict()``
    schemas EXACTLY — asserted as exact key-set comparisons (the A9
    ``_HEADLINE_KEYS`` pattern) plus exact served values;
  - contract C4: the pages/queries tables are keyset-paged envelopes whose
    opaque cursor is fingerprint-bound to the endpoint + active filters
    (replay against different filters -> 400, malformed -> 400,
    cross-endpoint -> 400);
  - invariant 7: projections only — an absent snapshot serves an EMPTY
    payload / empty page, never a read-time recomputation; paging/sorting
    hits the stored aggregates only (sort restricted to the config
    whitelist, anything else -> 422);
  - invariant 5: cross-workspace project access is a 404;
  - query contract: bad granularity/window/sort -> 422.

A served-snapshot fixture drives the A7 executor directly over a seeded
GSC+GA4 import chain (unpersisted task: nothing to cancel against), then
asserts the exact served projection. Requires a real Postgres.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.analytics import ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH
from app.core.config.integrations import (
    DATASET_GA4_CHANNEL_DAILY,
    DATASET_GA4_LANDING_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    DATASET_GSC_PAGE_DAILY,
    DATASET_GSC_QUERY_DAILY,
    INTEGRATION_PROVIDER_GA4,
    INTEGRATION_PROVIDER_GSC,
)
from app.core.config.traffic import (
    TRAFFIC_FORMULA_VERSION,
    TRAFFIC_MAX_WINDOW_DAYS,
    TRAFFIC_NORMALIZATION_VERSION,
)
from app.domain.site_health.normalization import canonical_identity
from app.domain.traffic import service as traffic_service
from app.domain.traffic.service import refresh_traffic_snapshot
from app.models.analytics import AnalyticsTask
from app.models.integrations import IntegrationConnection
from app.models.site_health import SiteUrl
from tests.component.analytics_helpers import (
    seed_ga4_import,
    seed_metric_row,
)

WINDOW = (date(2026, 7, 20), date(2026, 7, 22))
GSC_PROPERTY = "https://example.com/"
GA4_PROPERTY = "properties/123456789"
PAGE_A = "https://example.com/blog"
PAGE_B = "https://example.com/pricing"
PAGE_C = "https://example.com/about"

# Exact key sets mirroring the frontend zod .strict() schemas (contract C6).
_DASHBOARD_KEYS = {
    "project_id",
    "window_start",
    "window_end",
    "granularity",
    "totals",
    "series",
    "formula_version",
    "normalization_version",
}
_TOTALS_KEYS = {
    "impressions",
    "clicks",
    "ctr",
    "position",
    "sessions",
    "conversions",
}
_SERIES_NAMES = _TOTALS_KEYS
_POINT_KEYS = {"date", "value"}
_PAGE_ROW_KEYS = {
    "canonical_url",
    "site_url_id",
    "impressions",
    "clicks",
    "ctr",
    "position",
    "sessions",
    "conversions",
}
_QUERY_ROW_KEYS = {"normalized_query", "impressions", "clicks", "ctr", "position"}
_ENVELOPE_KEYS = {"items", "next_cursor"}


# ---------------------------------------------------------------------------
# API + seed helpers
# ---------------------------------------------------------------------------
async def _register(client: httpx.AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201


async def _create_project(client: httpx.AsyncClient) -> tuple[str, str]:
    """Create a project in the caller's default workspace.

    Returns ``(project_id, workspace_id)`` so the traffic rows can be
    seeded straight into the same workspace the API authorizes against.
    """
    resp = await client.post("/api/v1/projects", json={"name": "Traffic Project"})
    assert resp.status_code == 201
    body = resp.json()
    return body["id"], body["workspace_id"]


async def _seed_traffic_chain(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    """Seed the GSC + GA4 import graph the A7 projection consumes.

    One shared Google grant carrying a GSC and a GA4 connection; three GSC
    pages (PAGE_C carries NO position measure -> its position buckets are
    null) and three queries, plus GA4 organic-channel + AI-referrer rows
    (Paid Search / google-organic rows are excluded by the inclusion
    rule). Committed by the caller.
    """
    pages = await seed_ga4_import(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        dataset=DATASET_GSC_PAGE_DAILY,
        provider=INTEGRATION_PROVIDER_GSC,
        property_ref=GSC_PROPERTY,
        window=WINDOW,
    )
    await seed_metric_row(
        session,
        seed=pages,
        row_date=date(2026, 7, 20),
        dimension_values=[PAGE_A, "2026-07-20"],
        metrics={"clicks": 10, "impressions": 100, "ctr": 0.1, "position": 10.0},
    )
    await seed_metric_row(
        session,
        seed=pages,
        row_date=date(2026, 7, 21),
        dimension_values=[PAGE_A, "2026-07-21"],
        metrics={"clicks": 20, "impressions": 200, "ctr": 0.1, "position": 20.0},
    )
    await seed_metric_row(
        session,
        seed=pages,
        row_date=date(2026, 7, 20),
        dimension_values=[PAGE_B, "2026-07-20"],
        metrics={"clicks": 5, "impressions": 50, "ctr": 0.1, "position": 5.0},
    )
    await seed_metric_row(
        session,
        seed=pages,
        row_date=date(2026, 7, 22),
        dimension_values=[PAGE_C, "2026-07-22"],
        # No ``position`` measure: the page's position stays NULL.
        metrics={"clicks": 1, "impressions": 10},
    )
    gsc_connection = await session.get(IntegrationConnection, pages.connection_id)
    assert gsc_connection is not None
    # The second connection rides the SAME Google grant (one consent).
    ga4_connection = IntegrationConnection(
        workspace_id=workspace_id,
        grant_id=pages.grant_id,
        provider=INTEGRATION_PROVIDER_GA4,
        label="ga4 connection",
        account_ref="ga4-account-1",
    )
    session.add(ga4_connection)
    await session.flush()

    queries = await seed_ga4_import(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        dataset=DATASET_GSC_QUERY_DAILY,
        provider=INTEGRATION_PROVIDER_GSC,
        property_ref=GSC_PROPERTY,
        connection=gsc_connection,
        # Second run on the same connection/window needs a bumped seq
        # (uq_integration_sync_run_window_seq).
        resync_seq=1,
    )
    await seed_metric_row(
        session,
        seed=queries,
        row_date=date(2026, 7, 21),
        dimension_values=["Best  CRM", "2026-07-21"],
        metrics={"clicks": 3, "impressions": 30, "position": 8.0},
        resync_seq=1,
    )
    await seed_metric_row(
        session,
        seed=queries,
        row_date=date(2026, 7, 20),
        dimension_values=["aeo guide", "2026-07-20"],
        metrics={"clicks": 8, "impressions": 96, "position": 9.1},
        resync_seq=1,
    )
    await seed_metric_row(
        session,
        seed=queries,
        row_date=date(2026, 7, 22),
        dimension_values=["pricing", "2026-07-22"],
        metrics={"clicks": 1, "impressions": 5},
        resync_seq=1,
    )

    channels = await seed_ga4_import(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        dataset=DATASET_GA4_CHANNEL_DAILY,
        property_ref=GA4_PROPERTY,
        connection=ga4_connection,
    )
    await seed_metric_row(
        session,
        seed=channels,
        row_date=date(2026, 7, 20),
        dimension_values=["Organic Search", "20260720"],
        metrics={"sessions": 7, "conversions": 2},
    )
    await seed_metric_row(
        session,
        seed=channels,
        row_date=date(2026, 7, 21),
        dimension_values=["Paid Search", "20260721"],
        metrics={"sessions": 100, "conversions": 50},
    )
    source_medium = await seed_ga4_import(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        dataset=DATASET_GA4_SOURCE_MEDIUM_DAILY,
        property_ref=GA4_PROPERTY,
        connection=ga4_connection,
        resync_seq=1,
    )
    await seed_metric_row(
        session,
        seed=source_medium,
        row_date=date(2026, 7, 21),
        dimension_values=["chatgpt.com", "referral", "20260721"],
        metrics={"sessions": 4, "conversions": 1},
        resync_seq=1,
    )
    await seed_metric_row(
        session,
        seed=source_medium,
        row_date=date(2026, 7, 22),
        dimension_values=["google", "organic", "20260722"],
        metrics={"sessions": 999, "conversions": 9},
        resync_seq=1,
    )
    landing = await seed_ga4_import(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        dataset=DATASET_GA4_LANDING_DAILY,
        property_ref=GA4_PROPERTY,
        connection=ga4_connection,
        resync_seq=2,
    )
    await seed_metric_row(
        session,
        seed=landing,
        row_date=date(2026, 7, 21),
        dimension_values=[PAGE_A, "chatgpt.com", "referral", "20260721"],
        metrics={"sessions": 2, "conversions": 1},
        resync_seq=2,
    )
    await seed_metric_row(
        session,
        seed=landing,
        row_date=date(2026, 7, 20),
        dimension_values=[PAGE_B, "google", "organic", "20260720"],
        metrics={"sessions": 50, "conversions": 5},
        resync_seq=2,
    )
    await seed_metric_row(
        session,
        seed=landing,
        row_date=date(2026, 7, 22),
        dimension_values=[PAGE_C, "chatgpt.com", "referral", "20260722"],
        metrics={"sessions": 5, "conversions": 0},
        resync_seq=2,
    )


async def _seed_site_url(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> uuid.UUID:
    """Seed the crawled SiteUrl identity that PAGE_A joins to."""
    canonical, url_hash = canonical_identity(PAGE_A)
    site_url = SiteUrl(
        workspace_id=workspace_id,
        project_id=project_id,
        normalized_url=canonical,
        url_hash=url_hash,
    )
    session.add(site_url)
    await session.flush()
    return site_url.id


async def _seed_served_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
) -> uuid.UUID:
    """Seed the chain + drive the A7 executor (unpersisted task)."""
    async with session_factory() as session:
        await _seed_traffic_chain(
            session, workspace_id=workspace_id, project_id=project_id
        )
        site_url_id = await _seed_site_url(
            session, workspace_id=workspace_id, project_id=project_id
        )
        await session.commit()
    task = AnalyticsTask(
        workspace_id=workspace_id,
        project_id=project_id,
        task_kind=ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH,
        payload={
            "window_start": WINDOW[0].isoformat(),
            "window_end": WINDOW[1].isoformat(),
        },
        idempotency_key=uuid.uuid4().hex,
    )
    await refresh_traffic_snapshot(session_factory, task)
    return site_url_id


# ---------------------------------------------------------------------------
# Auth + workspace scoping (invariant 5)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    base = f"/api/v1/projects/{uuid.uuid4()}/traffic"
    assert (await client.get(base)).status_code == 401
    assert (await client.get(f"{base}/pages")).status_code == 401
    assert (await client.get(f"{base}/queries")).status_code == 401


@pytest.mark.asyncio
async def test_cross_workspace_project_is_404(client: httpx.AsyncClient) -> None:
    """User B cannot read user A's project traffic (invariant 5)."""
    await _register(client, "traffic-owner-a@example.com")
    project_id, _workspace_id = await _create_project(client)

    # Switch to user B (fresh session cookie in the same client).
    client.cookies.clear()
    await _register(client, "traffic-owner-b@example.com")

    base = f"/api/v1/projects/{project_id}/traffic"
    assert (await client.get(base)).status_code == 404
    assert (await client.get(f"{base}/pages")).status_code == 404
    assert (await client.get(f"{base}/queries")).status_code == 404


# ---------------------------------------------------------------------------
# Empty states (invariant 7: absent snapshot -> empty payload, never recompute)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_states_when_no_snapshot(client: httpx.AsyncClient) -> None:
    await _register(client, "traffic-empty@example.com")
    project_id, _workspace_id = await _create_project(client)
    base = f"/api/v1/projects/{project_id}/traffic"

    resp = await client.get(base)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _DASHBOARD_KEYS
    assert body["project_id"] == project_id
    # No window supplied: the empty payload echoes empty window bounds.
    assert body["window_start"] == ""
    assert body["window_end"] == ""
    assert body["granularity"] == "day"
    assert body["totals"] == {
        "impressions": 0,
        "clicks": 0,
        "ctr": None,
        "position": None,
        "sessions": None,
        "conversions": None,
    }
    assert body["series"] == {name: [] for name in _SERIES_NAMES}
    assert body["formula_version"] == TRAFFIC_FORMULA_VERSION
    assert body["normalization_version"] == TRAFFIC_NORMALIZATION_VERSION

    # An explicit window is echoed in the empty payload.
    resp = await client.get(base, params={"from": "2026-07-01", "to": "2026-07-07"})
    assert resp.status_code == 200
    assert resp.json()["window_start"] == "2026-07-01"
    assert resp.json()["window_end"] == "2026-07-07"

    pages = await client.get(f"{base}/pages")
    assert pages.status_code == 200
    assert pages.json() == {"items": [], "next_cursor": None}

    queries = await client.get(f"{base}/queries")
    assert queries.status_code == 200
    assert queries.json() == {"items": [], "next_cursor": None}


# ---------------------------------------------------------------------------
# Served snapshot (strict C6 shapes + exact A7 fixture values)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_headline_serves_persisted_snapshot(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "traffic-served@example.com")
    project_id, workspace_id = await _create_project(client)
    await _seed_served_snapshot(
        session_factory,
        workspace_id=uuid.UUID(workspace_id),
        project_id=uuid.UUID(project_id),
    )
    base = f"/api/v1/projects/{project_id}/traffic"

    resp = await client.get(base, params={"from": "2026-07-20", "to": "2026-07-22"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _DASHBOARD_KEYS
    assert body["project_id"] == project_id
    assert body["window_start"] == "2026-07-20"
    assert body["window_end"] == "2026-07-22"
    assert body["granularity"] == "day"
    assert body["formula_version"] == TRAFFIC_FORMULA_VERSION
    assert body["normalization_version"] == TRAFFIC_NORMALIZATION_VERSION

    # Totals: strict shape + exact served values. PAGE_C's position-less
    # impressions stay out of the weighted mean; Paid Search + google/
    # organic GA4 rows are excluded by the inclusion rule.
    totals = body["totals"]
    assert set(totals) == _TOTALS_KEYS
    assert totals["impressions"] == 360
    assert totals["clicks"] == 36
    assert totals["ctr"] == pytest.approx(0.1)
    assert totals["position"] == pytest.approx(15.0)  # 5250 / 350
    assert totals["sessions"] == 11  # 7 organic-channel + 4 AI
    assert totals["conversions"] == 3

    # Series: strict shapes + exact values; 7/22 has a position gap (no
    # position-bearing rows) and no GA4 rows (null, never a coerced zero).
    series = body["series"]
    assert set(series) == _SERIES_NAMES
    for points in series.values():
        for point in points:
            assert set(point) == _POINT_KEYS
    assert [p["date"] for p in series["clicks"]] == [
        "2026-07-20",
        "2026-07-21",
        "2026-07-22",
    ]
    assert [p["value"] for p in series["impressions"]] == [150, 200, 10]
    assert [p["value"] for p in series["clicks"]] == [15, 20, 1]
    assert [p["value"] for p in series["sessions"]] == [7, 4, None]
    assert [p["value"] for p in series["conversions"]] == [2, 1, None]
    assert series["position"][0]["value"] == pytest.approx(1250 / 150)
    assert series["position"][1]["value"] == pytest.approx(20.0)
    assert series["position"][2]["value"] is None
    assert series["ctr"][0]["value"] == pytest.approx(0.1)

    # With no window the project's LATEST snapshot at the granularity is
    # served (the default landing renders the freshest projection).
    latest = await client.get(base)
    assert latest.status_code == 200
    assert latest.json()["window_start"] == "2026-07-20"
    assert latest.json()["window_end"] == "2026-07-22"
    assert [p["value"] for p in latest.json()["series"]["impressions"]] == [
        150,
        200,
        10,
    ]

    # The week granularity collapses to one bucket at the window start.
    week = await client.get(
        base,
        params={"from": "2026-07-20", "to": "2026-07-22", "granularity": "week"},
    )
    assert week.status_code == 200
    assert week.json()["granularity"] == "week"
    assert week.json()["series"]["clicks"] == [{"date": "2026-07-20", "value": 36}]
    assert week.json()["totals"]["sessions"] == 11


# ---------------------------------------------------------------------------
# Pages table (keyset paging + whitelist sorting on stored aggregates)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pages_sorting_paging_and_dto_mapping(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _register(client, "traffic-pages@example.com")
    project_id, workspace_id = await _create_project(client)
    site_url_id = await _seed_served_snapshot(
        session_factory,
        workspace_id=uuid.UUID(workspace_id),
        project_id=uuid.UUID(project_id),
    )
    url = f"/api/v1/projects/{project_id}/traffic/pages"
    window = {"from": "2026-07-20", "to": "2026-07-22"}

    # Default sort = -impressions (the "top pages" view).
    resp = await client.get(url, params=window)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _ENVELOPE_KEYS
    assert [row["canonical_url"] for row in body["items"]] == [
        PAGE_A,
        PAGE_B,
        PAGE_C,
    ]
    assert body["next_cursor"] is None
    for row in body["items"]:
        assert set(row) == _PAGE_ROW_KEYS

    # DTO mapping: exact values; the SiteUrl join resolves for PAGE_A and
    # stays NULL for the unmatched pages; excluded landing rows never fold.
    page_a, page_b, page_c = body["items"]
    assert page_a["site_url_id"] == str(site_url_id)
    assert page_a["impressions"] == 300
    assert page_a["clicks"] == 30
    assert page_a["ctr"] == pytest.approx(0.1)
    assert page_a["position"] == pytest.approx(5000 / 300)
    assert page_a["sessions"] == 2
    assert page_a["conversions"] == 1
    assert page_b["site_url_id"] is None
    assert page_b["sessions"] is None  # google/organic landing excluded
    assert page_b["conversions"] is None
    assert page_c["position"] is None  # no position-bearing rows
    assert page_c["sessions"] == 5
    assert page_c["conversions"] == 0

    # Ascending bare-key sort.
    asc = await client.get(url, params={**window, "sort": "clicks"})
    assert asc.status_code == 200
    assert [row["canonical_url"] for row in asc.json()["items"]] == [
        PAGE_C,
        PAGE_B,
        PAGE_A,
    ]

    # NULLS LAST on both ratio and GA4-absent sorts.
    by_sessions = await client.get(url, params={**window, "sort": "-sessions"})
    assert [row["canonical_url"] for row in by_sessions.json()["items"]] == [
        PAGE_C,
        PAGE_A,
        PAGE_B,
    ]
    by_position = await client.get(url, params={**window, "sort": "-position"})
    assert [row["canonical_url"] for row in by_position.json()["items"]] == [
        PAGE_A,
        PAGE_B,
        PAGE_C,
    ]

    # Keyset traversal: page size 2 -> two pages, no overlap, stable order.
    monkeypatch.setattr(traffic_service, "TRAFFIC_TABLE_PAGE_SIZE", 2)
    page1 = await client.get(url, params=window)
    assert page1.status_code == 200
    body1 = page1.json()
    assert [row["canonical_url"] for row in body1["items"]] == [PAGE_A, PAGE_B]
    assert body1["next_cursor"] is not None
    page2 = await client.get(url, params={**window, "cursor": body1["next_cursor"]})
    assert page2.status_code == 200
    body2 = page2.json()
    assert [row["canonical_url"] for row in body2["items"]] == [PAGE_C]
    assert body2["next_cursor"] is None

    # Same traversal across the NULL boundary of a ratio sort.
    pos1 = await client.get(url, params={**window, "sort": "-position"})
    pbody1 = pos1.json()
    assert [row["canonical_url"] for row in pbody1["items"]] == [PAGE_A, PAGE_B]
    pos2 = await client.get(
        url,
        params={**window, "sort": "-position", "cursor": pbody1["next_cursor"]},
    )
    assert [row["canonical_url"] for row in pos2.json()["items"]] == [PAGE_C]

    # No window: the latest default-granularity snapshot's stat rows serve.
    latest = await client.get(url)
    assert latest.status_code == 200
    assert [row["canonical_url"] for row in latest.json()["items"]] == [
        PAGE_A,
        PAGE_B,
    ]


@pytest.mark.asyncio
async def test_pages_cursor_replay_and_sort_validation(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cursor is fingerprint-bound to endpoint + filters (contract C4)."""
    await _register(client, "traffic-cursor@example.com")
    project_id, workspace_id = await _create_project(client)
    await _seed_served_snapshot(
        session_factory,
        workspace_id=uuid.UUID(workspace_id),
        project_id=uuid.UUID(project_id),
    )
    monkeypatch.setattr(traffic_service, "TRAFFIC_TABLE_PAGE_SIZE", 2)
    base = f"/api/v1/projects/{project_id}/traffic"
    window = {"from": "2026-07-20", "to": "2026-07-22"}

    page1 = await client.get(f"{base}/pages", params={**window, "sort": "-impressions"})
    assert page1.status_code == 200
    cursor = page1.json()["next_cursor"]
    assert cursor is not None

    # Replayed against a different sort -> 400.
    replayed = await client.get(
        f"{base}/pages", params={**window, "sort": "-clicks", "cursor": cursor}
    )
    assert replayed.status_code == 400
    # Replayed against a different window -> 400.
    other_window = await client.get(
        f"{base}/pages",
        params={"from": "2026-07-20", "to": "2026-07-21", "cursor": cursor},
    )
    assert other_window.status_code == 400
    # Replayed with the window dropped -> 400 as well.
    dropped = await client.get(f"{base}/pages", params={"cursor": cursor})
    assert dropped.status_code == 400
    # Replayed on the OTHER table endpoint (different scope) -> 400.
    cross_endpoint = await client.get(
        f"{base}/queries", params={**window, "cursor": cursor}
    )
    assert cross_endpoint.status_code == 400
    # Replayed against the SAME filters still works (the cursor is valid).
    ok = await client.get(
        f"{base}/pages", params={**window, "sort": "-impressions", "cursor": cursor}
    )
    assert ok.status_code == 200
    # A malformed cursor is a 400, never a 500.
    malformed = await client.get(
        f"{base}/pages", params={**window, "cursor": "not-a-valid-cursor"}
    )
    assert malformed.status_code == 400

    # Non-whitelisted sorts are 422 (sorting hits stored aggregates only).
    assert (
        await client.get(f"{base}/pages", params={**window, "sort": "bogus"})
    ).status_code == 422
    # ``sessions`` is not a query-table aggregate (GSC-only measures).
    assert (
        await client.get(f"{base}/queries", params={**window, "sort": "sessions"})
    ).status_code == 422
    # The window is validated on the tables too.
    assert (
        await client.get(f"{base}/pages", params={"from": "2026-07-20"})
    ).status_code == 422


# ---------------------------------------------------------------------------
# Queries table (keyset paging + normalized keys + GSC-only measures)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_queries_sorting_paging_and_dto_mapping(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _register(client, "traffic-queries@example.com")
    project_id, workspace_id = await _create_project(client)
    await _seed_served_snapshot(
        session_factory,
        workspace_id=uuid.UUID(workspace_id),
        project_id=uuid.UUID(project_id),
    )
    url = f"/api/v1/projects/{project_id}/traffic/queries"
    window = {"from": "2026-07-20", "to": "2026-07-22"}

    # Default sort = -impressions; keys are the normalized query strings.
    resp = await client.get(url, params=window)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _ENVELOPE_KEYS
    assert [row["normalized_query"] for row in body["items"]] == [
        "aeo guide",
        "best crm",
        "pricing",
    ]
    for row in body["items"]:
        assert set(row) == _QUERY_ROW_KEYS
    best_crm = body["items"][1]
    assert best_crm["impressions"] == 30
    assert best_crm["clicks"] == 3
    assert best_crm["ctr"] == pytest.approx(0.1)
    assert best_crm["position"] == pytest.approx(8.0)
    pricing = body["items"][2]
    assert pricing["ctr"] == pytest.approx(0.2)
    assert pricing["position"] is None  # no position-bearing rows

    # Ascending position sort: NULLS LAST.
    asc = await client.get(url, params={**window, "sort": "position"})
    assert [row["normalized_query"] for row in asc.json()["items"]] == [
        "best crm",
        "aeo guide",
        "pricing",
    ]

    # Keyset traversal with page size 2.
    monkeypatch.setattr(traffic_service, "TRAFFIC_TABLE_PAGE_SIZE", 2)
    page1 = await client.get(url, params=window)
    body1 = page1.json()
    assert [row["normalized_query"] for row in body1["items"]] == [
        "aeo guide",
        "best crm",
    ]
    assert body1["next_cursor"] is not None
    page2 = await client.get(url, params={**window, "cursor": body1["next_cursor"]})
    body2 = page2.json()
    assert [row["normalized_query"] for row in body2["items"]] == ["pricing"]
    assert body2["next_cursor"] is None

    # Replay against a different sort -> 400 (fingerprint-bound, C4).
    replayed = await client.get(
        url, params={**window, "sort": "clicks", "cursor": body1["next_cursor"]}
    )
    assert replayed.status_code == 400


# ---------------------------------------------------------------------------
# Query validation (422 contract)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_query_validation_422(client: httpx.AsyncClient) -> None:
    await _register(client, "traffic-validation@example.com")
    project_id, _workspace_id = await _create_project(client)
    base = f"/api/v1/projects/{project_id}/traffic"

    # Unknown granularity.
    assert (await client.get(base, params={"granularity": "hourly"})).status_code == 422
    # 'to' before 'from'.
    assert (
        await client.get(base, params={"from": "2026-07-22", "to": "2026-07-20"})
    ).status_code == 422
    # 'from' without 'to' (both-or-neither).
    assert (await client.get(base, params={"from": "2026-07-20"})).status_code == 422
    # Window span beyond TRAFFIC_MAX_WINDOW_DAYS.
    too_wide_from = WINDOW[1] - timedelta(days=TRAFFIC_MAX_WINDOW_DAYS)
    assert (
        await client.get(
            base,
            params={
                "from": too_wide_from.isoformat(),
                "to": WINDOW[1].isoformat(),
            },
        )
    ).status_code == 422
    # ...while a span exactly AT the maximum is accepted.
    max_from = WINDOW[1] - timedelta(days=TRAFFIC_MAX_WINDOW_DAYS - 1)
    assert (
        await client.get(
            base,
            params={"from": max_from.isoformat(), "to": WINDOW[1].isoformat()},
        )
    ).status_code == 200
    # A garbage date is rejected by FastAPI's own parsing (also 422).
    assert (
        await client.get(base, params={"from": "not-a-date", "to": "2026-07-22"})
    ).status_code == 422
