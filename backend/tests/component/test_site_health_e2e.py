"""End-to-end (broad) integration coverage for the Site Health API (Slice 9).

The focused component tests in ``test_site_health_api.py`` prove each endpoint
and each Slice 6 reconciliation invariant in isolation. This module adds the
*journey* coverage the Slice 9 handoff calls for, reusing the same seed helpers
(no fixture duplication) rather than re-proving individual endpoints:

  - the full read journey a user takes across a completed crawl:
    crawl summary -> inventory -> monitored selection view -> pages (dashboard)
    -> issues catalog -> per-URL detail -> issue history -> CSV/MD export;
  - crawl lifecycle mutations over HTTP: create (201) and cancel (terminal),
    which the isolated endpoint tests never drive through the router;
  - the stale monitored-selection conflict (HTTP 409) after a Starter workspace
    commits one selection version and then submits a now-stale expected version;
  - a partial/error crawl: a ``partially_completed`` crawl whose failed page
    surfaces the ``error`` presentation status + its error code and null scores
    (never a fabricated zero);
  - Free-sample redaction end to end: the crawl projection and the event replay
    never leak a discovered/total count for a non-disclosing (Free) workspace;
  - the same journey resolving in a NON-default workspace via ``X-Workspace-Id``.

These are deliberately broad, deterministic, and DB-backed. They do not attempt
the live SSE / real-worker dry run the final testing agent owns (that needs a
migrated live server, per the handoff).
"""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.site_health import (
    CAPABILITY_STARTER,
    CRAWL_STATUS_CANCELLED,
    CRAWL_STATUS_COMPLETED,
    CRAWL_STATUS_PARTIALLY_COMPLETED,
    INITIAL_TASK_GENERATION,
    PAGE_ANALYSIS_STATUS_FAILED,
    TASK_KIND_ANALYZE,
)
from app.core.config.task_queue import TASK_STATUS_FAILED
from app.domain.site_health.entitlements import set_entitlement
from app.models.project import Project
from app.models.site_health import (
    SiteCrawl,
    SiteCrawlTask,
    SiteFetchArtifact,
    SiteHealthProfile,
    SitePageAnalysis,
    SiteUrl,
    SiteUrlObservation,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember

# Reuse the exact seed helpers the focused component suite uses so the E2E
# journey exercises the same fixtures/shapes (no duplicated seeding logic).
from tests.component.test_site_health_api import (
    _register,
    _seed_scenario,
)

pytestmark = pytest.mark.asyncio


async def test_full_read_journey_completed_crawl(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One user walks the whole completed-crawl surface in sequence.

    create -> discover -> select -> analyze are represented by the seeded
    completed crawl (the discover/analyze workers are unit/worker-tested
    separately). Here we prove the *read* journey the UI drives is coherent
    end to end: every downstream view resolves and cross-references the same
    ids the earlier view returned.
    """
    await _register(client, "journey@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="journey@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    # 1. Crawl summary (dashboard header).
    summary = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}", headers=headers
    )
    assert summary.status_code == 200
    assert summary.json()["status"] == CRAWL_STATUS_COMPLETED

    # 2. Inventory (selection screen source of truth): 3 admitted URLs.
    inventory = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/inventory", headers=headers
    )
    assert inventory.status_code == 200
    inv_ids = {row["site_url_id"] for row in inventory.json()["items"]}
    assert str(scn.monitored_url_id) in inv_ids
    assert str(scn.issue_url_id) in inv_ids

    # 3. Monitored selection view for the project.
    monitored = await client.get(
        f"/api/v1/projects/{scn.project_id}/monitored-urls", headers=headers
    )
    assert monitored.status_code == 200
    m_body = monitored.json()
    monitored_ids = {
        row["site_url_id"] for row in m_body["monitored_urls"]
    }
    assert str(scn.monitored_url_id) in monitored_ids

    # 4. Pages (dashboard rows) reflect analysis status + monitored flag.
    pages = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages", headers=headers
    )
    assert pages.status_code == 200
    page_rows = {row["site_url_id"]: row for row in pages.json()["items"]}
    assert page_rows[str(scn.monitored_url_id)]["monitored"] is True

    # 5. Issues catalog: the failing-title group with its canonical id.
    issues = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/issues", headers=headers
    )
    assert issues.status_code == 200
    group = issues.json()["items"][0]
    assert group["id"] == str(scn.canonical_issue_id)
    assert group["affected_url_count"] == 1

    # 6. Navigate from the issue's affected URL to that URL's detail.
    detail = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages/{scn.issue_url_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert any(
        iss["rule_id"] == "technical.title_present"
        for iss in detail.json()["issues"]
    )

    # 7. That URL's crawl-bounded issue history.
    history = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages/{scn.issue_url_id}"
        "/issue-history",
        headers=headers,
    )
    assert history.status_code == 200
    assert len(history.json()["items"]) == 1

    # 8. Export the same catalog the user just browsed (auth blob).
    csv_resp = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/export.csv?view=issues",
        headers=headers,
    )
    assert csv_resp.status_code == 200
    assert "technical.title_present" in csv_resp.text

    md_resp = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/export.md?view=pages",
        headers=headers,
    )
    assert md_resp.status_code == 200
    assert md_resp.text.startswith("# ")


async def test_create_and_cancel_crawl_lifecycle(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Drive the crawl lifecycle mutations through the router.

    The seeded crawl is terminal (completed), so the project has no active
    crawl and a new one can be created. Creating returns 201 with an active
    (non-terminal) status; cancelling drives it to the ``cancelled`` terminal
    status. A second create while one is active is a 409.
    """
    await _register(client, "lifecycle@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="lifecycle@example.com")
        # The base seed roots the project on the reserved ``.test`` TLD, which
        # has no registrable domain, so ``create_crawl`` would reject it (422).
        # Point the project at a real registrable domain for the create path.
        project = await session.get(Project, scn.project_id)
        project.website_url = "https://example.com/"
        await session.commit()
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    created = await client.post(
        "/api/v1/site-crawls",
        headers=headers,
        json={"project_id": str(scn.project_id), "seed": "7"},
    )
    assert created.status_code == 201
    new_crawl = created.json()
    new_crawl_id = new_crawl["id"]
    assert new_crawl_id != str(scn.crawl_id)
    assert new_crawl["status"] not in {
        CRAWL_STATUS_COMPLETED,
        CRAWL_STATUS_CANCELLED,
    }

    # A second create while one is active is rejected with a coded 409.
    conflict = await client.post(
        "/api/v1/site-crawls",
        headers=headers,
        json={"project_id": str(scn.project_id)},
    )
    assert conflict.status_code == 409

    # Cancel drives the active crawl to the cancelled terminal status.
    cancelled = await client.post(
        f"/api/v1/site-crawls/{new_crawl_id}/cancel", headers=headers
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == CRAWL_STATUS_CANCELLED

    # Cancelling from another workspace the caller does not own is a 404.
    async with session_factory() as session:
        other = Workspace(name="Foreign WS")
        session.add(other)
        await session.flush()
        user = await session.scalar(
            select(User).where(User.email == "lifecycle@example.com")
        )
        session.add(
            WorkspaceMember(
                workspace_id=other.id, user_id=user.id, role="owner"
            )
        )
        await session.commit()
        other_id = other.id
    foreign = await client.post(
        f"/api/v1/site-crawls/{new_crawl_id}/cancel",
        headers={"X-Workspace-Id": str(other_id)},
    )
    assert foreign.status_code == 404


async def test_stale_monitored_selection_conflict_409(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A Starter workspace's second commit with a stale version is a 409.

    Free may not select (that gate is proven elsewhere), so the workspace is
    upgraded to Starter first. The first PUT (expected version 0) succeeds and
    bumps the persisted ``selection_version``; a second PUT replaying the now
    stale expected version 0 must be rejected with a coded 409 carrying the
    current version, and must NOT overwrite the committed selection.
    """
    await _register(client, "stale@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="stale@example.com")
        await set_entitlement(session, scn.workspace_id, CAPABILITY_STARTER)
        await session.commit()
        # Resolve two discoverable URL ids for the project.
        rows = (
            await session.scalars(
                select(SiteUrl).where(SiteUrl.project_id == scn.project_id)
            )
        ).all()
        url_ids = [str(u.id) for u in rows]
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    # First replacement at version 0 succeeds and bumps to version 1.
    first = await client.put(
        f"/api/v1/projects/{scn.project_id}/monitored-urls",
        headers=headers,
        json={
            "site_url_ids": url_ids[:2],
            "expected_selection_version": 0,
        },
    )
    assert first.status_code == 200
    assert first.json()["selection_version"] == 1

    # Replaying expected version 0 is now stale -> 409 with current version.
    stale = await client.put(
        f"/api/v1/projects/{scn.project_id}/monitored-urls",
        headers=headers,
        json={
            "site_url_ids": url_ids[:1],
            "expected_selection_version": 0,
        },
    )
    assert stale.status_code == 409
    body = stale.json()["detail"]
    assert body["current_selection_version"] == 1

    # The committed selection is unchanged (the stale write did not apply).
    after = await client.get(
        f"/api/v1/projects/{scn.project_id}/monitored-urls", headers=headers
    )
    assert after.status_code == 200
    assert after.json()["selection_version"] == 1


async def test_partial_error_crawl_surfaces_failed_pages(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A partially-completed crawl surfaces per-page failure without fake scores.

    We seed a NEW crawl for the same project whose status is
    ``partially_completed`` and admit one URL whose analyze task terminated with
    a non-policy failure code (and no completed analysis row). Per the projection
    contract a raw ``failed`` page-analysis status is never surfaced as page
    copy: it maps to ``error`` carrying the analyze task's error code. The
    scores must be null (rendered as an em dash in the UI), never a fabricated
    zero.
    """
    await _register(client, "partial@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="partial@example.com")
        profile = await session.scalar(
            select(SiteHealthProfile).where(
                SiteHealthProfile.project_id == scn.project_id
            )
        )
        crawl = SiteCrawl(
            workspace_id=scn.workspace_id,
            project_id=scn.project_id,
            profile_id=profile.id,
            status=CRAWL_STATUS_PARTIALLY_COMPLETED,
            root_url=profile.root_url,
            random_seed="9",
            admitted_url_count=1,
            analyzed_url_count=0,
            failed_url_count=1,
            rule_catalog_version="v1",
        )
        session.add(crawl)
        await session.flush()

        url = await session.scalar(
            select(SiteUrl).where(
                SiteUrl.project_id == scn.project_id,
                SiteUrl.normalized_url == "https://acme.test/a",
            )
        )
        session.add(
            SiteUrlObservation(
                workspace_id=scn.workspace_id,
                crawl_id=crawl.id,
                site_url_id=url.id,
                source_kind="root",
                depth=0,
                observed_url=url.normalized_url,
                final_url=url.normalized_url,
                status_code=200,
                content_type="text/html",
                title=url.latest_title or "",
            )
        )
        task = SiteCrawlTask(
            crawl_id=crawl.id,
            workspace_id=scn.workspace_id,
            task_kind=TASK_KIND_ANALYZE,
            requested_url=url.normalized_url,
            url_hash=url.url_hash,
            site_url_id=url.id,
            generation=INITIAL_TASK_GENERATION,
            idempotency_key=f"{crawl.id}:analyze:{url.id}:fail",
            status=TASK_STATUS_FAILED,
            error_code="fetch_failed",
        )
        session.add(task)
        await session.flush()
        artifact = SiteFetchArtifact(
            task_id=task.id,
            crawl_id=crawl.id,
            workspace_id=scn.workspace_id,
            fetch_purpose="analyze",
            requested_url=url.normalized_url,
            final_url=url.normalized_url,
            status_code=500,
            content_type="text/html",
            decoded_bytes=0,
            normalized_facts={},
        )
        session.add(artifact)
        await session.flush()
        session.add(
            SitePageAnalysis(
                workspace_id=scn.workspace_id,
                project_id=scn.project_id,
                crawl_id=crawl.id,
                site_url_id=url.id,
                artifact_id=artifact.id,
                status=PAGE_ANALYSIS_STATUS_FAILED,
                technical_score=None,
                aeo_score=None,
                overall_score=None,
                analyzer_version="v1",
                scoring_version="v1",
            )
        )
        await session.commit()
        crawl_id = crawl.id
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    summary = await client.get(
        f"/api/v1/site-crawls/{crawl_id}", headers=headers
    )
    assert summary.status_code == 200
    assert summary.json()["status"] == CRAWL_STATUS_PARTIALLY_COMPLETED

    pages = await client.get(
        f"/api/v1/site-crawls/{crawl_id}/pages", headers=headers
    )
    assert pages.status_code == 200
    rows = pages.json()["items"]
    assert len(rows) == 1
    row = rows[0]
    # A raw ``failed`` analysis maps to the ``error`` presentation status,
    # carrying the analyze task's (non-policy) error code.
    assert row["analysis_status"] == "error"
    assert row["error_code"] == "fetch_failed"
    # No fabricated zero scores for a failed page.
    assert row["technical_score"] is None
    assert row["aeo_score"] is None
    assert row["overall_score"] is None


async def test_free_redaction_end_to_end(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A Free workspace never leaks a discovered/total count anywhere.

    The seeded workspace is Free (fail-closed). Its crawl projection must null
    the discovered/total/has-more fields, and the event replay must not expose a
    total-bearing key in any payload.
    """
    await _register(client, "redact@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="redact@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    ent = await client.get("/api/v1/entitlements", headers=headers)
    assert ent.json()["plan_key"] == "free"

    crawl = (
        await client.get(
            f"/api/v1/site-crawls/{scn.crawl_id}", headers=headers
        )
    ).json()
    assert crawl["discovered_count"] is None
    assert crawl["total_url_count"] is None
    assert crawl["has_more_site_urls"] is None
    # The admitted (visible) count is NOT a full-site total and is allowed.
    assert crawl["visible_url_count"] == 3

    events = (
        await client.get(
            f"/api/v1/site-crawls/{scn.crawl_id}/events", headers=headers
        )
    ).json()
    forbidden = {
        "discovered_total",
        "total",
        "total_url_count",
        "discovered_url_count",
        "frontier_size",
        "overflow",
    }
    for event in events:
        payload = event.get("payload") or {}
        assert not (forbidden & set(payload)), payload


async def test_journey_resolves_in_non_default_workspace(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The read journey resolves via X-Workspace-Id in a non-default workspace.

    Registration creates the user's own default workspace; the seeded Acme
    workspace is a second, non-default one. Passing its X-Workspace-Id header
    must resolve every step of the journey (the header is honored, not just the
    default workspace).
    """
    await _register(client, "nondefault-e2e@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(
            session, email="nondefault-e2e@example.com"
        )
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    for path in (
        f"/api/v1/site-crawls/{scn.crawl_id}",
        f"/api/v1/site-crawls/{scn.crawl_id}/inventory",
        f"/api/v1/site-crawls/{scn.crawl_id}/pages",
        f"/api/v1/site-crawls/{scn.crawl_id}/issues",
        f"/api/v1/site-crawls/{scn.crawl_id}/pages/{scn.issue_url_id}",
        f"/api/v1/site-crawls/{scn.crawl_id}/export.csv?view=inventory",
    ):
        resp = await client.get(path, headers=headers)
        assert resp.status_code == 200, (path, resp.status_code)
