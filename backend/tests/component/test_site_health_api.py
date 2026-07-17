"""Component tests for the Site Health workspace-scoped read/mutation API.

Exercises the real FastAPI app against a live Postgres schema (per invariant 5:
every lookup is workspace-scoped; a foreign/missing id is a 404). Covers:
  - entitlements (Free fail-closed seed) + count disclosure;
  - crawl summary/list projection;
  - inventory + pages keyset traversal, monitored + status filters;
  - grouped issues + per-issue detail + per-URL page detail/history;
  - CSV/Markdown exports (media type, filename, content);
  - second-workspace isolation (X-Workspace-Id) for reads, exports, events;
  - SSE event replay.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.site_health import (
    CRAWL_STATUS_COMPLETED,
    INITIAL_TASK_GENERATION,
    PAGE_ANALYSIS_STATUS_COMPLETED,
    RULE_OUTCOME_FAIL,
    SELECTION_SOURCE_USER,
    TASK_KIND_ANALYZE,
)
from app.core.config.task_queue import TASK_STATUS_SUCCEEDED
from app.models.project import Project
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteCrawlEvent,
    SiteCrawlTask,
    SiteFetchArtifact,
    SiteHealthProfile,
    SiteIssue,
    SitePageAnalysis,
    SiteRuleEvaluation,
    SiteUrl,
    SiteUrlObservation,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember

pytestmark = pytest.mark.asyncio


def _hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:64]


@dataclass
class Scenario:
    workspace_id: uuid.UUID
    project_id: uuid.UUID
    crawl_id: uuid.UUID
    monitored_url_id: uuid.UUID
    issue_url_id: uuid.UUID
    canonical_issue_id: uuid.UUID


async def _register(client: httpx.AsyncClient, email: str) -> None:
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert reg.status_code == 201


async def _seed_scenario(
    session: AsyncSession, *, email: str
) -> Scenario:
    """Seed a completed crawl with 3 URLs, one monitored, one with an issue."""
    root = "https://acme.test/"
    workspace = Workspace(name="Acme WS")
    session.add(workspace)
    await session.flush()

    # The user was created by `/auth/register`; attach it to this workspace.
    user = await session.scalar(select(User).where(User.email == email))
    assert user is not None
    session.add(
        WorkspaceMember(
            workspace_id=workspace.id, user_id=user.id, role="owner"
        )
    )

    project = Project(
        workspace_id=workspace.id,
        name="Acme Site",
        brand_name="Acme",
        country_code="AU",
        language_code="en-AU",
        benchmark_mode="consumer_like",
        default_repetitions=1,
        website_url=root,
    )
    session.add(project)
    await session.flush()

    profile = SiteHealthProfile(
        workspace_id=workspace.id,
        project_id=project.id,
        root_url=root,
        root_host="acme.test",
        registrable_domain="acme.test",
    )
    session.add(profile)
    await session.flush()

    crawl = SiteCrawl(
        workspace_id=workspace.id,
        project_id=project.id,
        profile_id=profile.id,
        status=CRAWL_STATUS_COMPLETED,
        root_url=root,
        random_seed="1",
        admitted_url_count=3,
        analyzed_url_count=2,
        failed_url_count=0,
        rule_catalog_version="v1",
    )
    session.add(crawl)
    await session.flush()

    # Three URLs, ordered a < b < c by normalized_url.
    urls: list[SiteUrl] = []
    for slug in ("a", "b", "c"):
        u = f"{root}{slug}"
        su = SiteUrl(
            workspace_id=workspace.id,
            project_id=project.id,
            normalized_url=u,
            url_hash=_hash(u),
            display_url=u,
            host="acme.test",
            latest_title=f"Page {slug}",
            latest_content_type="text/html",
            last_seen_crawl_id=crawl.id,
        )
        session.add(su)
        urls.append(su)
    await session.flush()
    url_a, url_b, url_c = urls

    # Admit all three URLs to the crawl. Endpoint reads (page-detail, pages,
    # issues, history, exports) are scoped to URLs with a SiteUrlObservation
    # row for the crawl — exactly what the discover worker writes in production
    # — so the seed must record admission provenance or those reads 404.
    for depth, su in enumerate(urls):
        session.add(
            SiteUrlObservation(
                workspace_id=workspace.id,
                crawl_id=crawl.id,
                site_url_id=su.id,
                source_kind="root" if depth == 0 else "link",
                depth=depth,
                observed_url=su.normalized_url,
                final_url=su.normalized_url,
                status_code=200,
                content_type="text/html",
                title=su.latest_title or "",
            )
        )
    await session.flush()

    # Monitor url_a.
    session.add(
        MonitoredSiteUrl(
            workspace_id=workspace.id,
            project_id=project.id,
            profile_id=profile.id,
            site_url_id=url_a.id,
            active=True,
            selection_source=SELECTION_SOURCE_USER,
        )
    )

    # url_a + url_b get analyzed; url_b gets a failing rule -> issue.
    canonical_issue_id: uuid.UUID | None = None
    for su, with_issue in ((url_a, False), (url_b, True)):
        task = SiteCrawlTask(
            crawl_id=crawl.id,
            workspace_id=workspace.id,
            task_kind=TASK_KIND_ANALYZE,
            requested_url=su.normalized_url,
            url_hash=su.url_hash,
            site_url_id=su.id,
            generation=INITIAL_TASK_GENERATION,
            idempotency_key=f"{crawl.id}:analyze:{su.id}:0",
            status=TASK_STATUS_SUCCEEDED,
        )
        session.add(task)
        await session.flush()

        artifact = SiteFetchArtifact(
            task_id=task.id,
            crawl_id=crawl.id,
            workspace_id=workspace.id,
            fetch_purpose="analyze",
            requested_url=su.normalized_url,
            final_url=su.normalized_url,
            status_code=200,
            content_type="text/html",
            decoded_bytes=2048,
            normalized_facts={
                "has_html": True,
                "title": su.latest_title,
                "meta_description": "desc",
                "robots": {"noindex": False, "nofollow": False},
                "canonical_url": su.normalized_url,
                "headings": {"h1_count": 1, "counts": {"h2": 2}},
                "images": {"count": 3, "missing_alt": 0},
                "body": {"word_count": 400},
                "structured_data": {"types": ["Article"], "count": 1},
                "links": {
                    "anchors": [
                        {"is_internal": True},
                        {"is_internal": False},
                    ]
                },
                "blocking_resources": {"total": 1},
            },
        )
        session.add(artifact)
        await session.flush()

        analysis = SitePageAnalysis(
            workspace_id=workspace.id,
            project_id=project.id,
            crawl_id=crawl.id,
            site_url_id=su.id,
            artifact_id=artifact.id,
            status=PAGE_ANALYSIS_STATUS_COMPLETED,
            technical_score=90.0,
            aeo_score=80.0,
            overall_score=85.0,
            analyzer_version="v1",
            scoring_version="v1",
        )
        session.add(analysis)
        await session.flush()

        if with_issue:
            evaluation = SiteRuleEvaluation(
                workspace_id=workspace.id,
                analysis_id=analysis.id,
                source_artifact_id=artifact.id,
                rule_id="technical.title_present",
                dimension="technical",
                category="meta",
                severity="critical",
                weight=1.0,
                outcome=RULE_OUTCOME_FAIL,
                evidence={"observed": "missing"},
                analyzer_version="v1",
                rule_version="v1",
            )
            session.add(evaluation)
            await session.flush()
            issue = SiteIssue(
                workspace_id=workspace.id,
                project_id=project.id,
                crawl_id=crawl.id,
                site_url_id=su.id,
                analysis_id=analysis.id,
                evaluation_id=evaluation.id,
                source_artifact_id=artifact.id,
                rule_id="technical.title_present",
                dimension="technical",
                category="meta",
                severity="critical",
                evidence={"observed": "missing"},
                remediation="Add a <title> tag.",
                analyzer_version="v1",
                rule_version="v1",
            )
            session.add(issue)
            await session.flush()
            canonical_issue_id = issue.id

    session.add(
        SiteCrawlEvent(
            crawl_id=crawl.id,
            event_type="crawl.completed",
            message="Crawl completed",
            payload={"analyzed": 2},
        )
    )
    await session.commit()

    assert canonical_issue_id is not None
    return Scenario(
        workspace_id=workspace.id,
        project_id=project.id,
        crawl_id=crawl.id,
        monitored_url_id=url_a.id,
        issue_url_id=url_b.id,
        canonical_issue_id=canonical_issue_id,
    )


async def test_entitlements_seed_free_and_disclosure(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "ent@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="ent@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    resp = await client.get("/api/v1/entitlements", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_key"] == "free"
    # Free plans cannot view the discovered total (fail-closed disclosure).
    assert body["can_view_discovered_total"] is False


async def test_crawl_summary_and_list(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "crawl@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="crawl@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    summary = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}", headers=headers
    )
    assert summary.status_code == 200
    body = summary.json()
    assert body["id"] == str(scn.crawl_id)
    assert body["status"] == CRAWL_STATUS_COMPLETED
    assert body["analyzed_count"] == 2

    listing = await client.get(
        f"/api/v1/site-crawls?project_id={scn.project_id}", headers=headers
    )
    assert listing.status_code == 200
    assert any(
        row["id"] == str(scn.crawl_id) for row in listing.json()["items"]
    )


async def test_inventory_monitored_filter(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "inv@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="inv@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    all_rows = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/inventory", headers=headers
    )
    assert all_rows.status_code == 200
    assert len(all_rows.json()["items"]) == 3

    monitored = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/inventory?monitored=true",
        headers=headers,
    )
    assert monitored.status_code == 200
    mitems = monitored.json()["items"]
    assert len(mitems) == 1
    assert mitems[0]["site_url_id"] == str(scn.monitored_url_id)
    assert mitems[0]["monitored"] is True


async def test_inventory_keyset_traversal_stable(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "keyset@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="keyset@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        path = f"/api/v1/site-crawls/{scn.crawl_id}/inventory?limit=1"
        if cursor:
            path += f"&cursor={cursor}"
        resp = await client.get(path, headers=headers)
        assert resp.status_code == 200
        page = resp.json()
        seen.extend(row["site_url_id"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 3
    assert len(set(seen)) == 3  # no duplicates across pages


async def test_pages_and_issues_projection(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "pages@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="pages@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    pages = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages", headers=headers
    )
    assert pages.status_code == 200
    items = pages.json()["items"]
    # The pages view projects the whole project URL set (3), each row carrying
    # the strict `monitored` flag and a derived presentation status.
    assert len(items) == 3
    assert all("monitored" in row for row in items)
    monitored_flags = {
        row["site_url_id"]: row["monitored"] for row in items
    }
    assert monitored_flags[str(scn.monitored_url_id)] is True
    # The analyzed URLs surface a completed status.
    statuses = {row["site_url_id"]: row["analysis_status"] for row in items}
    assert statuses[str(scn.issue_url_id)] == PAGE_ANALYSIS_STATUS_COMPLETED

    # Filtering by a monitored toggle narrows the set.
    only_monitored = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages?monitored=true",
        headers=headers,
    )
    assert only_monitored.status_code == 200
    m_items = only_monitored.json()["items"]
    assert len(m_items) == 1
    assert m_items[0]["site_url_id"] == str(scn.monitored_url_id)

    # Grouped issues: one group, critical, canonical id resolved, affected=1.
    issues = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/issues", headers=headers
    )
    assert issues.status_code == 200
    ibody = issues.json()
    assert len(ibody["items"]) == 1
    group = ibody["items"][0]
    assert group["rule_id"] == "technical.title_present"
    assert group["title"] == "Missing page title"
    assert group["severity"] == "critical"
    assert group["affected_url_count"] == 1
    assert ibody["summary"]["issue_count"] == 1
    assert ibody["summary"]["affected_url_count"] == 1

    # Issue detail: canonical row, affected URLs, current label.
    detail = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/issues/{scn.canonical_issue_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    dbody = detail.json()
    assert dbody["title"] == "Missing page title"
    assert dbody["affected_url_count"] == 1
    assert any(
        au["site_url_id"] == str(scn.issue_url_id)
        for au in dbody["affected_urls"]
    )


async def test_page_detail_and_history(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "detail@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="detail@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    detail = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages/{scn.issue_url_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["facts"]["h1_count"] == 1
    assert body["facts"]["word_count"] == 400
    assert body["facts"]["internal_link_count"] == 1
    assert body["facts"]["external_link_count"] == 1
    assert body["delivery"]["field_cwv_available"] is False
    assert body["delivery"]["html_bytes"] == 2048
    # The failing rule surfaces as an issue row on the page detail.
    assert any(
        iss["rule_id"] == "technical.title_present"
        for iss in body["issues"]
    )

    history = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages/{scn.issue_url_id}"
        "/issue-history",
        headers=headers,
    )
    assert history.status_code == 200
    assert len(history.json()["items"]) == 1


async def test_exports_media_type_and_filename(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "export@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="export@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    csv_resp = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/export.csv?view=issues",
        headers=headers,
    )
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_resp.headers["content-disposition"]
    assert str(scn.crawl_id) in csv_resp.headers["content-disposition"]
    assert "technical.title_present" in csv_resp.text

    md_resp = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/export.md?view=pages",
        headers=headers,
    )
    assert md_resp.status_code == 200
    assert md_resp.headers["content-type"].startswith("text/markdown")
    assert md_resp.text.startswith("# ")


async def test_events_replay(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "events@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="events@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    resp = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/events", headers=headers
    )
    assert resp.status_code == 200
    events = resp.json()
    # JSON replay returns a bare ordered list of redacted events.
    assert isinstance(events, list)
    assert any(e["event_type"] == "crawl.completed" for e in events)


async def test_second_workspace_isolation(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A user with two workspaces sees only the selected workspace's data.

    Reads, exports, and events for a crawl must 404 when the active
    workspace (X-Workspace-Id) is a different one the user also belongs to.
    """
    await _register(client, "multi@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="multi@example.com")
        # Second workspace the same user belongs to.
        user = await session.scalar(
            select(User).where(User.email == "multi@example.com")
        )
        other_ws = Workspace(name="Other WS")
        session.add(other_ws)
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=other_ws.id,
                user_id=user.id,
                role="owner",
            )
        )
        await session.commit()
        other_ws_id = other_ws.id

    other_headers = {"X-Workspace-Id": str(other_ws_id)}

    # Crawl summary, exports, and events are all scoped to the workspace.
    assert (
        await client.get(
            f"/api/v1/site-crawls/{scn.crawl_id}", headers=other_headers
        )
    ).status_code == 404
    assert (
        await client.get(
            f"/api/v1/site-crawls/{scn.crawl_id}/export.csv?view=issues",
            headers=other_headers,
        )
    ).status_code == 404
    assert (
        await client.get(
            f"/api/v1/site-crawls/{scn.crawl_id}/events", headers=other_headers
        )
    ).status_code == 404

    # The correct workspace still resolves the crawl.
    ok_headers = {"X-Workspace-Id": str(scn.workspace_id)}
    assert (
        await client.get(
            f"/api/v1/site-crawls/{scn.crawl_id}", headers=ok_headers
        )
    ).status_code == 200
