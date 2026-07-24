"""Component tests for the LLM Analytics read API (A9, httpx ASGITransport).

Pins the A9 acceptance:
  - contract C6: every served DTO mirrors the frontend zod ``.strict()``
    schemas EXACTLY — asserted as exact key-set comparisons (the
    integrations API ``_LIST_KEYS`` pattern) plus exact served values;
  - contract C4: the referrals drill-down is a keyset-paged envelope whose
    opaque cursor is fingerprint-bound to the endpoint + active filters
    (replay against different filters -> 400, malformed -> 400);
  - invariant 7: projections only — an absent snapshot serves an EMPTY
    payload / empty list, never a read-time recomputation;
  - invariant 5: cross-workspace project access is a 404;
  - query contract: bad granularity/window/source -> 422.

A served-snapshot fixture drives the A8 executor directly over the same
seeded import/audit chain as ``test_analytics_snapshot`` (unpersisted task:
nothing to cancel against), then asserts the exact served projection.
Requires a real Postgres (``--test-db-url``).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.analysis import ANALYZER_VERSION, SCORING_RULE_VERSION
from app.core.config.analytics import (
    AI_SOURCE_CHATGPT,
    AI_SOURCE_GEMINI,
    AI_SOURCE_OTHER,
    AI_SOURCE_PERPLEXITY,
    ANALYTICS_MAX_WINDOW_DAYS,
    ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH,
    CONFIDENCE_EXACT,
    MATCH_SIGNAL_REFERRER,
)
from app.domain.analytics import service as analytics_service
from app.domain.analytics.snapshot import refresh_analytics_snapshot
from app.models.analytics import AnalyticsTask
from app.models.integrations import IntegrationConnection
from tests.component.analytics_helpers import (
    DEFAULT_WINDOW,
    seed_ga4_import,
    seed_metric_row,
    seed_referral_classification,
    seed_referral_event,
    seed_theme_analysis,
    seed_visibility_snapshot,
)

WINDOW = DEFAULT_WINDOW  # 2026-07-20 -> 2026-07-22

# Exact key sets mirroring the frontend zod .strict() schemas (contract C6).
_HEADLINE_KEYS = {
    "project_id",
    "window_start",
    "window_end",
    "granularity",
    "referral_volume",
    "referral_share",
    "sources",
    "engine_visibility",
    "correlation",
    "analyzer_version",
    "formula_version",
}
_SERIES_POINT_KEYS = {"date", "value"}
_SOURCE_ROW_KEYS = {"ai_source", "sessions", "share"}
_ENGINE_VISIBILITY_KEYS = {"logical_engine", "series"}
_CORRELATION_KEYS = {"state", "coefficient", "sample_size"}
_REFERRAL_ROW_KEYS = {
    "id",
    "occurred_at",
    "landing_url",
    "referrer_host",
    "is_ai_referral",
    "ai_source",
    "logical_engine",
    "confidence",
    "match_signal",
}
_PAGE_KEYS = {"items", "next_cursor"}
_THEME_ROW_KEYS = {
    "theme",
    "intent",
    "total_completed",
    "brand_mention_rate",
    "visibility_score",
    "share_of_voice",
}


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

    Returns ``(project_id, workspace_id)`` so the analytics rows can be
    seeded straight into the same workspace the API authorizes against.
    """
    resp = await client.post("/api/v1/projects", json={"name": "Analytics Project"})
    assert resp.status_code == 201
    body = resp.json()
    return body["id"], body["workspace_id"]


def _occurred(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


async def _seed_referral_rows(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    """Metric rows -> events -> classifications (the A8 fixture's referral side).

    Three AI referrals (chatgpt/gemini/perplexity) + one non-AI referral,
    each event linked to its source metric row exactly like the ingest
    projection writes it.
    """
    seed = await seed_ga4_import(
        session, workspace_id=workspace_id, project_id=project_id
    )
    rows = {}
    for key, row_date, referrer, sessions in (
        ("chatgpt", date(2026, 7, 20), "https://chatgpt.com/c/abc", 4),
        ("gemini", date(2026, 7, 21), "https://gemini.google.com/app", 1),
        ("other", date(2026, 7, 21), "https://example.com/blog", 6),
        ("perplexity", date(2026, 7, 22), "https://perplexity.ai/s/1", 2),
    ):
        rows[key] = await seed_metric_row(
            session,
            seed=seed,
            row_date=row_date,
            dimension_values=[referrer, row_date.strftime("%Y%m%d")],
            metrics={"sessions": sessions},
        )
    for key, is_ai, ai_source in (
        ("chatgpt", True, AI_SOURCE_CHATGPT),
        ("gemini", True, AI_SOURCE_GEMINI),
        ("other", False, AI_SOURCE_OTHER),
        ("perplexity", True, AI_SOURCE_PERPLEXITY),
    ):
        row = rows[key]
        event = await seed_referral_event(
            session,
            seed=seed,
            occurred_at=_occurred(row.date),
            referrer_url=f"https://{ai_source}.example/",
            source_metric_row_id=row.id,
        )
        await seed_referral_classification(
            session,
            event=event,
            is_ai_referral=is_ai,
            ai_source=ai_source,
            logical_engine=(
                ai_source
                if ai_source in {AI_SOURCE_CHATGPT, AI_SOURCE_GEMINI}
                else None
            ),
            matched_rule_id="host-rule" if is_ai else "",
            match_signal=MATCH_SIGNAL_REFERRER if is_ai else "",
            confidence=CONFIDENCE_EXACT if is_ai else "",
        )


async def _seed_audit_rows(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    """Two dashboard-status audits: visibility snapshots + theme analyses."""
    snapshot_a = await seed_visibility_snapshot(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        completed_at=datetime(2026, 7, 20, 12, tzinfo=UTC),
        visibility_score=50.0,
        total_completed=2,
        per_engine={"chatgpt": 0.5},
    )
    snapshot_b = await seed_visibility_snapshot(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        completed_at=datetime(2026, 7, 21, 12, tzinfo=UTC),
        visibility_score=25.0,
        total_completed=4,
        per_engine={"chatgpt": 0.25, "gemini": 0.75},
    )
    await seed_theme_analysis(
        session,
        workspace_id=workspace_id,
        audit_id=snapshot_a.audit_id,
        prompt_index=0,
        theme="pricing",
        intent="comparison",
        brand_mentioned=True,
        competitors_mentioned=["Globex"],
    )
    await seed_theme_analysis(
        session,
        workspace_id=workspace_id,
        audit_id=snapshot_a.audit_id,
        prompt_index=1,
        theme="pricing",
        intent="comparison",
        brand_mentioned=False,
    )
    await seed_theme_analysis(
        session,
        workspace_id=workspace_id,
        audit_id=snapshot_b.audit_id,
        prompt_index=0,
        theme="onboarding",
        intent="",
        brand_mentioned=True,
    )


async def _seed_chain(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    """The full A8 fixture chain (referral side + audit side), committed."""
    await _seed_referral_rows(session, workspace_id=workspace_id, project_id=project_id)
    await _seed_audit_rows(session, workspace_id=workspace_id, project_id=project_id)
    await session.commit()


async def _run_refresh(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
) -> None:
    """Drive the A8 executor directly (unpersisted task: no cancel target)."""
    task = AnalyticsTask(
        workspace_id=workspace_id,
        project_id=project_id,
        task_kind=ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH,
        payload={
            "window_start": WINDOW[0].isoformat(),
            "window_end": WINDOW[1].isoformat(),
        },
        idempotency_key=uuid.uuid4().hex,
    )
    await refresh_analytics_snapshot(session_factory, task)


# ---------------------------------------------------------------------------
# Auth + workspace scoping (invariant 5)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    base = f"/api/v1/projects/{uuid.uuid4()}/llm-analytics"
    assert (await client.get(base)).status_code == 401
    assert (await client.get(f"{base}/referrals")).status_code == 401
    assert (await client.get(f"{base}/themes")).status_code == 401


@pytest.mark.asyncio
async def test_cross_workspace_project_is_404(client: httpx.AsyncClient) -> None:
    """User B cannot read user A's project analytics (invariant 5)."""
    await _register(client, "analytics-owner-a@example.com")
    project_id, _workspace_id = await _create_project(client)

    # Switch to user B (fresh session cookie in the same client).
    client.cookies.clear()
    await _register(client, "analytics-owner-b@example.com")

    base = f"/api/v1/projects/{project_id}/llm-analytics"
    assert (await client.get(base)).status_code == 404
    assert (await client.get(f"{base}/referrals")).status_code == 404
    assert (await client.get(f"{base}/themes")).status_code == 404


# ---------------------------------------------------------------------------
# Empty states (invariant 7: absent snapshot -> empty payload, never recompute)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_states_when_no_snapshot(client: httpx.AsyncClient) -> None:
    await _register(client, "analytics-empty@example.com")
    project_id, _workspace_id = await _create_project(client)
    base = f"/api/v1/projects/{project_id}/llm-analytics"

    resp = await client.get(base)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _HEADLINE_KEYS
    assert body["project_id"] == project_id
    # No window supplied: the empty payload echoes empty window bounds.
    assert body["window_start"] == ""
    assert body["window_end"] == ""
    assert body["granularity"] == "day"
    assert body["referral_volume"] == []
    assert body["referral_share"] == []
    assert body["sources"] == []
    assert body["engine_visibility"] == []
    assert body["correlation"] == {
        "state": "insufficient_data",
        "coefficient": None,
        "sample_size": 0,
    }
    assert body["analyzer_version"] == ANALYZER_VERSION
    assert body["formula_version"] == SCORING_RULE_VERSION

    # An explicit window is echoed in the empty payload.
    resp = await client.get(base, params={"from": "2026-07-01", "to": "2026-07-07"})
    assert resp.status_code == 200
    assert resp.json()["window_start"] == "2026-07-01"
    assert resp.json()["window_end"] == "2026-07-07"

    referrals = await client.get(f"{base}/referrals")
    assert referrals.status_code == 200
    assert referrals.json() == {"items": [], "next_cursor": None}

    themes = await client.get(f"{base}/themes")
    assert themes.status_code == 200
    assert themes.json() == []


# ---------------------------------------------------------------------------
# Served snapshot (strict C6 shapes + exact A8 fixture values)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_headline_serves_persisted_snapshot(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "analytics-served@example.com")
    project_id, workspace_id = await _create_project(client)
    async with session_factory() as session:
        await _seed_chain(
            session,
            workspace_id=uuid.UUID(workspace_id),
            project_id=uuid.UUID(project_id),
        )
    await _run_refresh(
        session_factory,
        workspace_id=uuid.UUID(workspace_id),
        project_id=uuid.UUID(project_id),
    )
    base = f"/api/v1/projects/{project_id}/llm-analytics"

    resp = await client.get(base, params={"from": "2026-07-20", "to": "2026-07-22"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _HEADLINE_KEYS
    assert body["project_id"] == project_id
    assert body["window_start"] == "2026-07-20"
    assert body["window_end"] == "2026-07-22"
    assert body["granularity"] == "day"
    assert body["analyzer_version"] == ANALYZER_VERSION
    assert body["formula_version"] == SCORING_RULE_VERSION

    # Series points: strict shape + exact served values (A8 fixture numbers).
    volume = body["referral_volume"]
    assert [point["date"] for point in volume] == [
        "2026-07-20",
        "2026-07-21",
        "2026-07-22",
    ]
    assert [point["value"] for point in volume] == [4, 1, 2]
    share = body["referral_share"]
    assert share[0]["value"] == pytest.approx(1.0)  # 4 / 4
    assert share[1]["value"] == pytest.approx(1 / 7)  # 1 / (1 + 6)
    assert share[2]["value"] == pytest.approx(1.0)  # 2 / 2
    for point in volume + share:
        assert set(point) == _SERIES_POINT_KEYS

    # Per-source breakdown: AI sources only, sessions desc then name; the
    # share denominator is the SAME row set's total (AI + non-AI) sessions.
    assert body["sources"] == [
        {"ai_source": AI_SOURCE_CHATGPT, "sessions": 4, "share": 4 / 13},
        {"ai_source": AI_SOURCE_PERPLEXITY, "sessions": 2, "share": 2 / 13},
        {"ai_source": AI_SOURCE_GEMINI, "sessions": 1, "share": 1 / 13},
    ]
    for row in body["sources"]:
        assert set(row) == _SOURCE_ROW_KEYS

    # Per-engine visibility: strict shape, null for unmeasured buckets.
    engines = {
        row["logical_engine"]: row["series"] for row in body["engine_visibility"]
    }
    assert set(engines) == {"chatgpt", "gemini"}
    for row in body["engine_visibility"]:
        assert set(row) == _ENGINE_VISIBILITY_KEYS
        for point in row["series"]:
            assert set(point) == _SERIES_POINT_KEYS
    assert [point["value"] for point in engines["chatgpt"]] == [50.0, 25.0, None]
    assert [point["value"] for point in engines["gemini"]] == [None, 75.0, None]

    # Day-aligned correlation: 2 aligned days < the 8 minimum -> the
    # insufficient_data state is SERVED with a null coefficient.
    assert set(body["correlation"]) == _CORRELATION_KEYS
    assert body["correlation"] == {
        "state": "insufficient_data",
        "coefficient": None,
        "sample_size": 2,
    }

    # With no window the project's LATEST snapshot at the granularity is
    # served (the default landing renders the freshest projection).
    latest = await client.get(base)
    assert latest.status_code == 200
    assert latest.json()["window_start"] == "2026-07-20"
    assert latest.json()["window_end"] == "2026-07-22"
    assert [point["value"] for point in latest.json()["referral_volume"]] == [4, 1, 2]

    # The week granularity collapses to one bucket at the window start; the
    # day-aligned correlation is granularity-independent.
    week = await client.get(
        base,
        params={"from": "2026-07-20", "to": "2026-07-22", "granularity": "week"},
    )
    assert week.status_code == 200
    assert week.json()["granularity"] == "week"
    assert week.json()["referral_volume"] == [{"date": "2026-07-20", "value": 7}]
    assert week.json()["correlation"] == body["correlation"]


@pytest.mark.asyncio
async def test_snapshot_without_visibility_serves_insufficient_data(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A SERVED snapshot with no aligned days reports insufficient_data.

    Distinct from the absent-snapshot empty payload: the window's referral
    series are real while the correlation honestly reports a zero aligned
    sample (never a fabricated coefficient, invariant 9).
    """
    await _register(client, "analytics-no-vis@example.com")
    project_id, workspace_id = await _create_project(client)
    async with session_factory() as session:
        await _seed_referral_rows(
            session,
            workspace_id=uuid.UUID(workspace_id),
            project_id=uuid.UUID(project_id),
        )
        await session.commit()
    await _run_refresh(
        session_factory,
        workspace_id=uuid.UUID(workspace_id),
        project_id=uuid.UUID(project_id),
    )
    base = f"/api/v1/projects/{project_id}/llm-analytics"

    resp = await client.get(base, params={"from": "2026-07-20", "to": "2026-07-22"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _HEADLINE_KEYS
    assert [point["value"] for point in body["referral_volume"]] == [4, 1, 2]
    assert body["engine_visibility"] == []
    assert body["correlation"] == {
        "state": "insufficient_data",
        "coefficient": None,
        "sample_size": 0,
    }

    # The theme rollup is empty too (served from the same snapshot).
    themes = await client.get(
        f"{base}/themes", params={"from": "2026-07-20", "to": "2026-07-22"}
    )
    assert themes.status_code == 200
    assert themes.json() == []


@pytest.mark.asyncio
async def test_themes_serve_persisted_rollup(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "analytics-themes@example.com")
    project_id, workspace_id = await _create_project(client)
    async with session_factory() as session:
        await _seed_chain(
            session,
            workspace_id=uuid.UUID(workspace_id),
            project_id=uuid.UUID(project_id),
        )
    await _run_refresh(
        session_factory,
        workspace_id=uuid.UUID(workspace_id),
        project_id=uuid.UUID(project_id),
    )
    base = f"/api/v1/projects/{project_id}/llm-analytics"

    resp = await client.get(
        f"{base}/themes", params={"from": "2026-07-20", "to": "2026-07-22"}
    )
    assert resp.status_code == 200
    rows = resp.json()
    # Ordered (theme, intent) asc; exact values from the A8 fixture.
    assert rows == [
        {
            "theme": "onboarding",
            "intent": "",
            "total_completed": 1,
            "brand_mention_rate": 1.0,
            "visibility_score": 100.0,
            "share_of_voice": 1.0,
        },
        {
            "theme": "pricing",
            "intent": "comparison",
            "total_completed": 2,
            "brand_mention_rate": 0.5,
            "visibility_score": 50.0,
            "share_of_voice": 0.5,
        },
    ]
    for row in rows:
        assert set(row) == _THEME_ROW_KEYS

    # Without a window the latest default-granularity snapshot is served.
    latest = await client.get(f"{base}/themes")
    assert latest.status_code == 200
    assert latest.json() == rows


# ---------------------------------------------------------------------------
# Referrals drill-down (keyset paging + DTO mapping, contract C4)
# ---------------------------------------------------------------------------
async def _seed_referral_page_rows(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    """Five classified events for the paging tests -> name -> classification id.

    Distinct ``occurred_at`` values make the newest-first keyset order
    deterministic; the non-AI row pins the DTO null/enum mapping.
    """
    seed = await seed_ga4_import(
        session, workspace_id=workspace_id, project_id=project_id
    )
    specs = [
        ("c_newest", AI_SOURCE_CHATGPT, True, datetime(2026, 7, 22, 10, tzinfo=UTC)),
        ("c_mid", AI_SOURCE_CHATGPT, True, datetime(2026, 7, 21, 9, tzinfo=UTC)),
        ("g_only", AI_SOURCE_GEMINI, True, datetime(2026, 7, 21, 8, tzinfo=UTC)),
        ("non_ai", AI_SOURCE_OTHER, False, datetime(2026, 7, 20, 12, tzinfo=UTC)),
        ("c_oldest", AI_SOURCE_CHATGPT, True, datetime(2026, 7, 20, 7, tzinfo=UTC)),
    ]
    ids: dict[str, uuid.UUID] = {}
    for name, ai_source, is_ai, occurred_at in specs:
        event = await seed_referral_event(
            session,
            seed=seed,
            occurred_at=occurred_at,
            landing_url=f"https://acme.com/{name}",
        )
        classification = await seed_referral_classification(
            session,
            event=event,
            is_ai_referral=is_ai,
            ai_source=ai_source,
            logical_engine=(
                ai_source
                if ai_source in {AI_SOURCE_CHATGPT, AI_SOURCE_GEMINI}
                else None
            ),
            matched_rule_id="host-rule" if is_ai else "",
            match_signal=MATCH_SIGNAL_REFERRER if is_ai else "",
            confidence=CONFIDENCE_EXACT if is_ai else "",
        )
        ids[name] = classification.id
    await session.commit()
    return ids


@pytest.mark.asyncio
async def test_referrals_keyset_paging_and_source_filter(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _register(client, "analytics-paging@example.com")
    project_id, workspace_id = await _create_project(client)
    async with session_factory() as session:
        ids = await _seed_referral_page_rows(
            session,
            workspace_id=uuid.UUID(workspace_id),
            project_id=uuid.UUID(project_id),
        )
    monkeypatch.setattr(analytics_service, "ANALYTICS_REFERRALS_PAGE_SIZE", 2)
    url = f"/api/v1/projects/{project_id}/llm-analytics/referrals"

    # Page 1: the two newest rows + a continuation cursor.
    page1 = await client.get(url)
    assert page1.status_code == 200
    body1 = page1.json()
    assert set(body1) == _PAGE_KEYS
    assert [item["id"] for item in body1["items"]] == [
        str(ids["c_newest"]),
        str(ids["c_mid"]),
    ]
    assert body1["next_cursor"] is not None
    for item in body1["items"]:
        assert set(item) == _REFERRAL_ROW_KEYS

    # Page 2 via the cursor: the next two, still newest-first.
    page2 = await client.get(url, params={"cursor": body1["next_cursor"]})
    assert page2.status_code == 200
    body2 = page2.json()
    assert [item["id"] for item in body2["items"]] == [
        str(ids["g_only"]),
        str(ids["non_ai"]),
    ]
    assert body2["next_cursor"] is not None

    # Page 3: the tail, no further cursor.
    page3 = await client.get(url, params={"cursor": body2["next_cursor"]})
    assert page3.status_code == 200
    body3 = page3.json()
    assert [item["id"] for item in body3["items"]] == [str(ids["c_oldest"])]
    assert body3["next_cursor"] is None

    # No overlap + strictly descending occurred_at across the traversal.
    seen = [item["id"] for body in (body1, body2, body3) for item in body["items"]]
    assert len(seen) == len(set(seen)) == 5
    occurred = [
        item["occurred_at"] for body in (body1, body2, body3) for item in body["items"]
    ]
    assert occurred == sorted(occurred, reverse=True)

    # DTO mapping: the AI row surfaces its classification verbatim...
    ai_row = body1["items"][0]
    assert ai_row["is_ai_referral"] is True
    assert ai_row["ai_source"] == AI_SOURCE_CHATGPT
    assert ai_row["logical_engine"] == AI_SOURCE_CHATGPT
    assert ai_row["confidence"] == CONFIDENCE_EXACT
    assert ai_row["match_signal"] == MATCH_SIGNAL_REFERRER
    assert ai_row["landing_url"] == "https://acme.com/c_newest"
    assert ai_row["referrer_host"] is None  # sanitized event carried no host

    # ...and the non-AI row maps the persisted empty confidence to ``exact``
    # (the deterministic no-match verdict) with null engine/signal.
    non_ai_row = body2["items"][1]
    assert non_ai_row["is_ai_referral"] is False
    assert non_ai_row["ai_source"] == AI_SOURCE_OTHER
    assert non_ai_row["logical_engine"] is None
    assert non_ai_row["confidence"] == CONFIDENCE_EXACT
    assert non_ai_row["match_signal"] is None

    # The source filter pages within the filtered set only.
    filtered1 = await client.get(url, params={"source": AI_SOURCE_CHATGPT})
    assert filtered1.status_code == 200
    fbody1 = filtered1.json()
    assert [item["id"] for item in fbody1["items"]] == [
        str(ids["c_newest"]),
        str(ids["c_mid"]),
    ]
    assert fbody1["next_cursor"] is not None
    filtered2 = await client.get(
        url,
        params={"source": AI_SOURCE_CHATGPT, "cursor": fbody1["next_cursor"]},
    )
    assert filtered2.status_code == 200
    fbody2 = filtered2.json()
    assert [item["id"] for item in fbody2["items"]] == [str(ids["c_oldest"])]
    assert fbody2["next_cursor"] is None

    # The from/to window filters on occurred_at (inclusive days).
    windowed = await client.get(url, params={"from": "2026-07-21", "to": "2026-07-22"})
    assert windowed.status_code == 200
    assert [item["id"] for item in windowed.json()["items"]] == [
        str(ids["c_newest"]),
        str(ids["c_mid"]),
    ]


@pytest.mark.asyncio
async def test_referrals_excludes_superseded_resync_revisions(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A re-sync's duplicate of a logical referral is listed ONCE.

    The drill-down serves only events whose source metric row is at the
    latest ``resync_seq`` per row identity (the snapshot builder folds the
    same way); the superseded revision's event is stale evidence. Events
    with NO metric-row link are not re-sync duplicates and still list.
    """
    await _register(client, "analytics-resync@example.com")
    project_id, workspace_id = await _create_project(client)
    ws_id, proj_id = uuid.UUID(workspace_id), uuid.UUID(project_id)
    async with session_factory() as session:
        seed0 = await seed_ga4_import(session, workspace_id=ws_id, project_id=proj_id)
        stale_row = await seed_metric_row(
            session,
            seed=seed0,
            row_date=date(2026, 7, 20),
            dimension_values=["https://chatgpt.com/c/abc", "20260720"],
            metrics={"sessions": 4},
        )
        stale_event = await seed_referral_event(
            session,
            seed=seed0,
            occurred_at=_occurred(date(2026, 7, 20)),
            referrer_url="https://chatgpt.com/c/abc",
            source_metric_row_id=stale_row.id,
        )
        stale = await seed_referral_classification(
            session,
            event=stale_event,
            is_ai_referral=True,
            ai_source=AI_SOURCE_CHATGPT,
            confidence=CONFIDENCE_EXACT,
        )
        # An unlinked event: not a re-sync duplicate — always listed.
        unlinked_event = await seed_referral_event(
            session,
            seed=seed0,
            occurred_at=_occurred(date(2026, 7, 21)),
            referrer_url="https://gemini.google.com/app",
            source_metric_row_id=None,
        )
        unlinked = await seed_referral_classification(
            session,
            event=unlinked_event,
            is_ai_referral=True,
            ai_source=AI_SOURCE_GEMINI,
            confidence=CONFIDENCE_EXACT,
        )
        await session.commit()

        # The re-sync: same connection, same window, resync_seq=1 — a second
        # copy of the same logical referral row + event.
        connection = await session.get(IntegrationConnection, seed0.connection_id)
        assert connection is not None
        seed1 = await seed_ga4_import(
            session,
            workspace_id=ws_id,
            project_id=proj_id,
            resync_seq=1,
            connection=connection,
        )
        latest_row = await seed_metric_row(
            session,
            seed=seed1,
            row_date=date(2026, 7, 20),
            dimension_values=["https://chatgpt.com/c/abc", "20260720"],
            metrics={"sessions": 9},
            resync_seq=1,
        )
        latest_event = await seed_referral_event(
            session,
            seed=seed1,
            occurred_at=_occurred(date(2026, 7, 20)),
            referrer_url="https://chatgpt.com/c/abc",
            source_metric_row_id=latest_row.id,
        )
        latest = await seed_referral_classification(
            session,
            event=latest_event,
            is_ai_referral=True,
            ai_source=AI_SOURCE_CHATGPT,
            confidence=CONFIDENCE_EXACT,
        )
        await session.commit()

    body = (
        await client.get(f"/api/v1/projects/{project_id}/llm-analytics/referrals")
    ).json()
    ids = {item["id"] for item in body["items"]}
    # The stale revision's copy is gone; the latest copy + the unlinked
    # event remain — exactly once each.
    assert ids == {str(latest.id), str(unlinked.id)}
    assert str(stale.id) not in ids


@pytest.mark.asyncio
async def test_referrals_cursor_replay_with_different_filters_400(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cursor is fingerprint-bound to its filters (contract C4)."""
    await _register(client, "analytics-cursor@example.com")
    project_id, workspace_id = await _create_project(client)
    async with session_factory() as session:
        await _seed_referral_page_rows(
            session,
            workspace_id=uuid.UUID(workspace_id),
            project_id=uuid.UUID(project_id),
        )
    monkeypatch.setattr(analytics_service, "ANALYTICS_REFERRALS_PAGE_SIZE", 2)
    url = f"/api/v1/projects/{project_id}/llm-analytics/referrals"

    page1 = await client.get(url, params={"source": AI_SOURCE_CHATGPT})
    assert page1.status_code == 200
    cursor = page1.json()["next_cursor"]
    assert cursor is not None

    # Replayed against a different source -> 400 (never silently skips rows).
    replayed = await client.get(
        url, params={"source": AI_SOURCE_GEMINI, "cursor": cursor}
    )
    assert replayed.status_code == 400

    # Replayed with the source dropped -> 400 as well.
    dropped = await client.get(url, params={"cursor": cursor})
    assert dropped.status_code == 400

    # Replayed against the SAME filters still works (the cursor is valid).
    ok = await client.get(url, params={"source": AI_SOURCE_CHATGPT, "cursor": cursor})
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_referrals_malformed_cursor_400(client: httpx.AsyncClient) -> None:
    await _register(client, "analytics-badcursor@example.com")
    project_id, _workspace_id = await _create_project(client)
    url = f"/api/v1/projects/{project_id}/llm-analytics/referrals"

    resp = await client.get(url, params={"cursor": "not-a-valid-cursor"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Query validation (422 contract)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_query_validation_422(client: httpx.AsyncClient) -> None:
    await _register(client, "analytics-validation@example.com")
    project_id, _workspace_id = await _create_project(client)
    base = f"/api/v1/projects/{project_id}/llm-analytics"

    # Unknown granularity.
    assert (await client.get(base, params={"granularity": "hourly"})).status_code == 422
    # 'to' before 'from'.
    assert (
        await client.get(base, params={"from": "2026-07-22", "to": "2026-07-20"})
    ).status_code == 422
    # 'from' without 'to' (both-or-neither).
    assert (await client.get(base, params={"from": "2026-07-20"})).status_code == 422
    # Window span beyond ANALYTICS_MAX_WINDOW_DAYS.
    too_wide_from = WINDOW[1] - timedelta(days=ANALYTICS_MAX_WINDOW_DAYS)
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
    max_from = WINDOW[1] - timedelta(days=ANALYTICS_MAX_WINDOW_DAYS - 1)
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

    # Unknown ai_source on the referrals drill-down.
    assert (
        await client.get(f"{base}/referrals", params={"source": "bogus"})
    ).status_code == 422
    # The referrals window is validated the same way.
    assert (
        await client.get(f"{base}/referrals", params={"to": "2026-07-22"})
    ).status_code == 422
    # ...and so is the themes window.
    assert (
        await client.get(
            f"{base}/themes",
            params={"from": "2026-07-22", "to": "2026-07-20"},
        )
    ).status_code == 422
