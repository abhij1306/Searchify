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
    INVENTORY_SOURCE_CRAWL_IDS_KEY,
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


async def _seed_scenario(session: AsyncSession, *, email: str) -> Scenario:
    """Seed a completed crawl with 3 URLs, one monitored, one with an issue."""
    root = "https://acme.test/"
    workspace = Workspace(name="Acme WS")
    session.add(workspace)
    await session.flush()

    # The user was created by `/auth/register`; attach it to this workspace.
    user = await session.scalar(select(User).where(User.email == email))
    assert user is not None
    session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner")
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
    url_a, url_b, _url_c = urls

    # Admit all three URLs to the crawl. Endpoint reads (page-detail, pages,
    # issues, history, exports) are scoped to URLs with a SiteUrlObservation
    # row for the crawl — exactly what the discover worker writes in production
    # — so the seed must record admission provenance or those reads 404.
    for depth, su in enumerate(urls):
        session.add(
            SiteUrlObservation(
                workspace_id=workspace.id,
                project_id=project.id,
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

    # url_a + url_b get analyzed (classified article / product — v2 P1);
    # url_b gets a failing rule -> issue.
    canonical_issue_id: uuid.UUID | None = None
    for su, with_issue, page_type in (
        (url_a, False, "article"),
        (url_b, True, "product"),
    ):
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
            page_type=page_type,
            classifier_version="sh-classifier-1",
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
    # A discovery-progress event carrying a protected total-bearing field, so
    # the Free-redaction test can assert the API strips it rather than only
    # asserting on an event that never carried the sensitive key.
    session.add(
        SiteCrawlEvent(
            crawl_id=crawl.id,
            event_type="discovery.progress",
            message="discovery progress",
            payload={"discovered_total": 42, "admitted": 3},
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

    summary = await client.get(f"/api/v1/site-crawls/{scn.crawl_id}", headers=headers)
    assert summary.status_code == 200
    body = summary.json()
    assert body["id"] == str(scn.crawl_id)
    assert body["status"] == CRAWL_STATUS_COMPLETED
    assert body["analyzed_count"] == 2

    listing = await client.get(
        f"/api/v1/site-crawls?project_id={scn.project_id}", headers=headers
    )
    assert listing.status_code == 200
    assert any(row["id"] == str(scn.crawl_id) for row in listing.json()["items"])


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
    monitored_flags = {row["site_url_id"]: row["monitored"] for row in items}
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
        au["site_url_id"] == str(scn.issue_url_id) for au in dbody["affected_urls"]
    )


async def test_page_type_projection_filters_and_exports(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """v2 P1: page rows/detail carry page_type, the pages/inventory/issues
    lists filter by it, and all three export views gain the column."""
    await _register(client, "pagetype@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="pagetype@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    # Page rows project the persisted page_type (None for the unanalyzed URL).
    pages = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages", headers=headers
    )
    assert pages.status_code == 200
    types = {row["site_url_id"]: row["page_type"] for row in pages.json()["items"]}
    assert types[str(scn.monitored_url_id)] == "article"
    assert types[str(scn.issue_url_id)] == "product"
    assert None in types.values()  # the third, unanalyzed URL

    # Pages page_type filter: exact match; unknown values match nothing.
    filtered = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages?page_type=article",
        headers=headers,
    )
    assert filtered.status_code == 200
    f_items = filtered.json()["items"]
    assert [row["site_url_id"] for row in f_items] == [str(scn.monitored_url_id)]
    unknown = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages?page_type=not_a_type",
        headers=headers,
    )
    assert unknown.status_code == 200
    assert unknown.json()["items"] == []

    # Inventory rows carry page_type and filter by it too.
    inventory = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/inventory?page_type=product",
        headers=headers,
    )
    assert inventory.status_code == 200
    i_items = inventory.json()["items"]
    assert [row["site_url_id"] for row in i_items] == [str(scn.issue_url_id)]
    assert i_items[0]["page_type"] == "product"

    # Per-URL detail carries page_type.
    detail = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages/{scn.issue_url_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["page_type"] == "product"

    # Issues filter by the affected analysis's page_type.
    product_issues = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/issues?page_type=product",
        headers=headers,
    )
    assert product_issues.status_code == 200
    assert len(product_issues.json()["items"]) == 1
    assert product_issues.json()["summary"]["issue_count"] == 1
    article_issues = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/issues?page_type=article",
        headers=headers,
    )
    assert article_issues.status_code == 200
    assert article_issues.json()["items"] == []
    assert article_issues.json()["summary"]["issue_count"] == 0

    # Issue detail: affected URLs carry their analysis's page_type.
    issue_detail = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/issues/{scn.canonical_issue_id}",
        headers=headers,
    )
    assert issue_detail.status_code == 200
    affected = issue_detail.json()["affected_urls"]
    assert len(affected) == 1
    assert affected[0]["page_type"] == "product"

    # All three export views carry the page_type column.
    pages_csv = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/export.csv?view=pages",
        headers=headers,
    )
    assert pages_csv.status_code == 200
    header = pages_csv.text.splitlines()[0].split(",")
    assert "page_type" in header
    assert "article" in pages_csv.text
    assert "product" in pages_csv.text

    issues_csv = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/export.csv?view=issues",
        headers=headers,
    )
    assert issues_csv.status_code == 200
    i_header = issues_csv.text.splitlines()[0].split(",")
    assert "page_type" in i_header
    # The single issue group affected a product page.
    row = issues_csv.text.splitlines()[1].split(",")
    assert row[i_header.index("page_type")] == "product"

    inventory_md = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/export.md?view=inventory",
        headers=headers,
    )
    assert inventory_md.status_code == 200
    assert "| page_type |" in inventory_md.text


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
    assert any(iss["rule_id"] == "technical.title_present" for iss in body["issues"])

    history = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages/{scn.issue_url_id}/issue-history",
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
        assert user is not None
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
        await client.get(f"/api/v1/site-crawls/{scn.crawl_id}", headers=other_headers)
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
        await client.get(f"/api/v1/site-crawls/{scn.crawl_id}", headers=ok_headers)
    ).status_code == 200


# =========================================================================
# Slice 6 reconciliation coverage (handoff items 1, 4, 5, 6, 7)
# =========================================================================
async def _add_second_crawl(
    session: AsyncSession,
    scn: Scenario,
    *,
    admit_slugs: tuple[str, ...],
) -> SiteCrawl:
    """Seed a later crawl for the same project that admits only ``admit_slugs``.

    Reuses the project's existing ``SiteUrl`` rows (a downgrade re-crawls the
    same site) but records a ``SiteUrlObservation`` only for the requested
    slugs, so the crawl's admitted set is a strict subset of the project's
    historical catalog.
    """
    profile = await session.scalar(
        select(SiteHealthProfile).where(SiteHealthProfile.project_id == scn.project_id)
    )
    assert profile is not None
    crawl = SiteCrawl(
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        profile_id=profile.id,
        status=CRAWL_STATUS_COMPLETED,
        root_url=profile.root_url,
        random_seed="2",
        admitted_url_count=len(admit_slugs),
        analyzed_url_count=0,
        failed_url_count=0,
        rule_catalog_version="v1",
    )
    session.add(crawl)
    await session.flush()

    for depth, slug in enumerate(admit_slugs):
        normalized = f"{profile.root_url}{slug}"
        su = await session.scalar(
            select(SiteUrl).where(
                SiteUrl.project_id == scn.project_id,
                SiteUrl.normalized_url == normalized,
            )
        )
        assert su is not None
        session.add(
            SiteUrlObservation(
                workspace_id=scn.workspace_id,
                project_id=scn.project_id,
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
    await session.commit()
    return crawl


async def _seed_issue_for_url(
    session: AsyncSession,
    scn: Scenario,
    *,
    crawl_id: uuid.UUID,
    site_url_id: uuid.UUID,
    rule_id: str,
    dimension: str = "technical",
    category: str = "meta",
    severity: str = "critical",
) -> uuid.UUID:
    """Seed a full analyze task + artifact + analysis + evaluation + issue.

    ``SiteIssue`` requires non-null ``analysis_id`` / ``evaluation_id`` /
    ``source_artifact_id`` (and ``evaluation_id`` is unique), so an extra issue
    cannot be a bare row — it needs its own supporting rows, exactly like the
    base scenario. Returns the new issue id.
    """
    su = await session.get(SiteUrl, site_url_id)
    assert su is not None
    task = SiteCrawlTask(
        crawl_id=crawl_id,
        workspace_id=scn.workspace_id,
        task_kind=TASK_KIND_ANALYZE,
        requested_url=su.normalized_url,
        url_hash=su.url_hash,
        site_url_id=su.id,
        generation=INITIAL_TASK_GENERATION,
        idempotency_key=f"{crawl_id}:analyze:{su.id}:{rule_id}",
        status=TASK_STATUS_SUCCEEDED,
    )
    session.add(task)
    await session.flush()
    artifact = SiteFetchArtifact(
        task_id=task.id,
        crawl_id=crawl_id,
        workspace_id=scn.workspace_id,
        fetch_purpose="analyze",
        requested_url=su.normalized_url,
        final_url=su.normalized_url,
        status_code=200,
        content_type="text/html",
        decoded_bytes=1024,
        normalized_facts={"has_html": True},
    )
    session.add(artifact)
    await session.flush()
    analysis = SitePageAnalysis(
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        crawl_id=crawl_id,
        site_url_id=su.id,
        artifact_id=artifact.id,
        status=PAGE_ANALYSIS_STATUS_COMPLETED,
        analyzer_version="v1",
        scoring_version="v1",
    )
    session.add(analysis)
    await session.flush()
    evaluation = SiteRuleEvaluation(
        workspace_id=scn.workspace_id,
        analysis_id=analysis.id,
        source_artifact_id=artifact.id,
        rule_id=rule_id,
        dimension=dimension,
        category=category,
        severity=severity,
        weight=1.0,
        outcome=RULE_OUTCOME_FAIL,
        evidence={"observed": "missing"},
        analyzer_version="v1",
        rule_version="v1",
    )
    session.add(evaluation)
    await session.flush()
    issue = SiteIssue(
        workspace_id=scn.workspace_id,
        project_id=scn.project_id,
        crawl_id=crawl_id,
        site_url_id=su.id,
        analysis_id=analysis.id,
        evaluation_id=evaluation.id,
        source_artifact_id=artifact.id,
        rule_id=rule_id,
        dimension=dimension,
        category=category,
        severity=severity,
        evidence={"observed": "missing"},
        remediation="Fix it.",
        analyzer_version="v1",
        rule_version="v1",
    )
    session.add(issue)
    await session.flush()
    return issue.id


async def test_selected_crawl_scoping_no_downgrade_leakage(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Item 1: a later crawl only surfaces the URLs IT admitted.

    The first crawl admits all 3 URLs (a/b/c). A later "downgraded" crawl of the
    same project admits only the root (a). Inventory / pages / page-detail /
    exports for the later crawl must never leak b/c from the project's fuller
    historical catalog.
    """
    await _register(client, "scope@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="scope@example.com")
        second = await _add_second_crawl(session, scn, admit_slugs=("a",))
        second_id = second.id
        # Resolve b's site_url_id (admitted to the first crawl, not the second).
        url_b = await session.scalar(
            select(SiteUrl).where(
                SiteUrl.project_id == scn.project_id,
                SiteUrl.normalized_url == "https://acme.test/b",
            )
        )
        assert url_b is not None
        url_b_id = url_b.id
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    # First crawl still admits all three.
    first_inv = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/inventory", headers=headers
    )
    assert len(first_inv.json()["items"]) == 3

    # Second crawl admitted only the root: inventory + pages scope to it.
    inv = await client.get(
        f"/api/v1/site-crawls/{second_id}/inventory", headers=headers
    )
    assert inv.status_code == 200
    inv_urls = {row["normalized_url"] for row in inv.json()["items"]}
    assert inv_urls == {"https://acme.test/a"}

    pages = await client.get(f"/api/v1/site-crawls/{second_id}/pages", headers=headers)
    assert {row["normalized_url"] for row in pages.json()["items"]} == {
        "https://acme.test/a"
    }

    # A URL the first crawl admitted but the second did not is a 404 on the
    # second crawl's page-detail (no cross-crawl catalog leak).
    leaked = await client.get(
        f"/api/v1/site-crawls/{second_id}/pages/{url_b_id}", headers=headers
    )
    assert leaked.status_code == 404

    # Exports over the later crawl carry only its admitted URL.
    csv_resp = await client.get(
        f"/api/v1/site-crawls/{second_id}/export.csv?view=inventory",
        headers=headers,
    )
    assert csv_resp.status_code == 200
    assert "https://acme.test/a" in csv_resp.text
    assert "https://acme.test/b" not in csv_resp.text
    assert "https://acme.test/c" not in csv_resp.text


async def test_starter_recrawl_keeps_prior_discovered_inventory_visible(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A fresh analysis crawl must not collapse All Discovered to its subset."""
    await _register(client, "inventory-continuity@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="inventory-continuity@example.com")
        second = await _add_second_crawl(session, scn, admit_slugs=("a",))
        second.configuration = {INVENTORY_SOURCE_CRAWL_IDS_KEY: [str(scn.crawl_id)]}
        await session.commit()
        second_id = second.id
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    inventory = await client.get(
        f"/api/v1/site-crawls/{second_id}/inventory", headers=headers
    )
    assert inventory.status_code == 200
    assert {row["normalized_url"] for row in inventory.json()["items"]} == {
        "https://acme.test/a",
        "https://acme.test/b",
        "https://acme.test/c",
    }

    pages = await client.get(f"/api/v1/site-crawls/{second_id}/pages", headers=headers)
    assert pages.status_code == 200
    by_url = {row["normalized_url"]: row for row in pages.json()["items"]}
    assert set(by_url) == {
        "https://acme.test/a",
        "https://acme.test/b",
        "https://acme.test/c",
    }
    # Current observations stay on the current detail route. Inherited-only
    # rows link to the immutable source crawl where their detail exists.
    assert by_url["https://acme.test/a"]["crawl_id"] == str(second_id)
    assert by_url["https://acme.test/b"]["crawl_id"] == str(scn.crawl_id)


async def test_issue_history_bounded_to_crawl_chronology(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Item 5: an earlier crawl's URL history never shows a later crawl's issues.

    The seeded (earlier) crawl records an issue on url_b. A later crawl records
    a second issue on the same URL. Requesting the URL's issue-history under the
    EARLIER crawl must return only the earlier issue (chronology bound).
    """
    await _register(client, "history@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="history@example.com")
        # Later crawl admitting url_b, with a NEW issue on url_b.
        second = await _add_second_crawl(session, scn, admit_slugs=("a", "b"))
        await _seed_issue_for_url(
            session,
            scn,
            crawl_id=second.id,
            site_url_id=scn.issue_url_id,
            rule_id="aeo.answerable_question",
            dimension="aeo",
            category="content",
            severity="high",
        )
        await session.commit()
        second_id = second.id
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    # Earlier crawl: only the earlier issue is in history.
    earlier = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages/{scn.issue_url_id}/issue-history",
        headers=headers,
    )
    assert earlier.status_code == 200
    earlier_rules = {i["rule_id"] for i in earlier.json()["items"]}
    assert earlier_rules == {"technical.title_present"}

    # Later crawl: history spans that crawl and prior ones (both issues).
    later = await client.get(
        f"/api/v1/site-crawls/{second_id}/pages/{scn.issue_url_id}/issue-history",
        headers=headers,
    )
    assert later.status_code == 200
    later_rules = {i["rule_id"] for i in later.json()["items"]}
    assert later_rules == {
        "technical.title_present",
        "aeo.answerable_question",
    }


async def test_issue_detail_canonicalizes_non_representative_member_id(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Item 4: a non-representative issue id resolves to the canonical group.

    Two issues share a rule (title_present) on two URLs. The earliest by
    (created_at, id) is canonical. Requesting the LATER member's id must return
    the same canonical detail (same id, both affected URLs), not a distinct
    projection.
    """
    await _register(client, "canon@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="canon@example.com")
        # Add a second issue for the SAME rule on url_c (a later member).
        url_c = await session.scalar(
            select(SiteUrl).where(
                SiteUrl.project_id == scn.project_id,
                SiteUrl.normalized_url == "https://acme.test/c",
            )
        )
        assert url_c is not None
        member_id = await _seed_issue_for_url(
            session,
            scn,
            crawl_id=scn.crawl_id,
            site_url_id=url_c.id,
            rule_id="technical.title_present",
        )
        await session.commit()
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    # Request the LATER member id: the detail canonicalizes to the earliest id.
    detail = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/issues/{member_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == str(scn.canonical_issue_id)
    assert body["id"] != str(member_id)
    # The group now spans both affected URLs.
    assert body["affected_url_count"] == 2


async def test_grouped_issues_canonical_id_stable_under_filters(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Item 4: the grouped-issue canonical id does not move when filters apply.

    Adding a same-rule issue on url_c must not change the group's canonical id
    (earliest unfiltered (created_at, id)), whether unfiltered or filtered to a
    single affected URL.
    """
    await _register(client, "stable@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="stable@example.com")
        url_c = await session.scalar(
            select(SiteUrl).where(
                SiteUrl.project_id == scn.project_id,
                SiteUrl.normalized_url == "https://acme.test/c",
            )
        )
        assert url_c is not None
        url_c_id = url_c.id
        await _seed_issue_for_url(
            session,
            scn,
            crawl_id=scn.crawl_id,
            site_url_id=url_c_id,
            rule_id="technical.title_present",
        )
        await session.commit()
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    unfiltered = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/issues", headers=headers
    )
    groups = unfiltered.json()["items"]
    assert len(groups) == 1
    assert groups[0]["id"] == str(scn.canonical_issue_id)
    assert groups[0]["affected_url_count"] == 2

    # Filter to only url_c (which is NOT the canonical row's URL): the canonical
    # id is unchanged (computed unfiltered), only the affected count narrows.
    filtered = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/issues?site_url_id={url_c_id}",
        headers=headers,
    )
    fgroups = filtered.json()["items"]
    assert len(fgroups) == 1
    assert fgroups[0]["id"] == str(scn.canonical_issue_id)
    assert fgroups[0]["affected_url_count"] == 1


async def test_tampered_cursor_returns_typed_400(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Item 6: a malformed/tampered cursor is a 400, never a 500."""
    await _register(client, "cursor@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="cursor@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    # Garbage cursor on inventory (url keyset).
    inv = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/inventory?cursor=not-a-cursor",
        headers=headers,
    )
    assert inv.status_code == 400

    # Garbage cursor on crawl list (created_at keyset) is also a typed 400.
    crawls = await client.get(
        f"/api/v1/site-crawls?project_id={scn.project_id}&cursor=%%%bad",
        headers=headers,
    )
    assert crawls.status_code == 400

    # A cursor valid for one filter set, replayed against a different filter, is
    # rejected as a scope mismatch (400), not silently accepted.
    first = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/inventory?limit=1", headers=headers
    )
    valid_cursor = first.json()["next_cursor"]
    assert valid_cursor
    replayed = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/inventory"
        f"?limit=1&monitored=true&cursor={valid_cursor}",
        headers=headers,
    )
    assert replayed.status_code == 400


async def test_export_csv_neutralizes_formula_in_url(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Item 6: an admitted URL beginning with a formula trigger is neutralized.

    A URL that begins with ``@``/``=``/``+``/``-`` must be prefixed with ``'``
    in the exported CSV so a spreadsheet renders it as text.
    """
    await _register(client, "formula@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="formula@example.com")
        # A pathological URL that begins with a formula trigger, admitted to
        # the crawl so it appears in the inventory export.
        danger = "@evil.example/=cmd"
        su = SiteUrl(
            workspace_id=scn.workspace_id,
            project_id=scn.project_id,
            normalized_url=danger,
            url_hash=_hash(danger),
            display_url=danger,
            host="evil.example",
            latest_title="danger",
            latest_content_type="text/html",
            last_seen_crawl_id=scn.crawl_id,
        )
        session.add(su)
        await session.flush()
        session.add(
            SiteUrlObservation(
                workspace_id=scn.workspace_id,
                project_id=scn.project_id,
                crawl_id=scn.crawl_id,
                site_url_id=su.id,
                source_kind="link",
                depth=1,
                observed_url=danger,
                final_url=danger,
                status_code=200,
                content_type="text/html",
                title="danger",
            )
        )
        await session.commit()
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    csv_resp = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/export.csv?view=inventory",
        headers=headers,
    )
    assert csv_resp.status_code == 200
    # The neutralizing single-quote precedes the formula trigger in the cell.
    assert "'@evil.example/=cmd" in csv_resp.text


async def test_non_default_workspace_reads_and_exports_succeed(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Item 7: a selected NON-default workspace resolves reads + exports (200).

    The isolation test proves a foreign workspace 404s. This proves the flip
    side: when the seeded workspace is NOT the user's default, passing its
    X-Workspace-Id header still resolves every read + export (the header is
    honored, not just the default workspace).
    """
    await _register(client, "nondefault@example.com")
    async with session_factory() as session:
        # A first (default-candidate) workspace with no site-health data, plus
        # the seeded workspace. Registration created the user's own default;
        # the seeded Acme workspace is a second, non-default one.
        scn = await _seed_scenario(session, email="nondefault@example.com")
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    # Reads resolve in the non-default workspace.
    assert (
        await client.get(f"/api/v1/site-crawls/{scn.crawl_id}", headers=headers)
    ).status_code == 200
    assert (
        await client.get(
            f"/api/v1/projects/{scn.project_id}/site-health", headers=headers
        )
    ).status_code == 200
    assert (
        await client.get(f"/api/v1/site-crawls/{scn.crawl_id}/issues", headers=headers)
    ).status_code == 200

    # Exports resolve in the non-default workspace (X-Workspace-Id honored).
    csv_resp = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/export.csv?view=pages",
        headers=headers,
    )
    assert csv_resp.status_code == 200
    assert "attachment" in csv_resp.headers["content-disposition"]

    # Events (SSE backing store) resolve in the non-default workspace.
    events = await client.get(
        f"/api/v1/site-crawls/{scn.crawl_id}/events", headers=headers
    )
    assert events.status_code == 200


async def test_rerun_page_from_completed_crawl_mints_new_crawl(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Handoff finding 1: 'Re-audit this page' must work from a COMPLETED crawl.

    The source crawl is terminal, so enqueuing an analyze task into it would be
    cooperatively cancelled by the worker and never run. The endpoint must mint
    a FRESH single-page rerun crawl and return its identity so the client polls
    the new run rather than the terminal source crawl.
    """
    from app.core.config.site_health import (
        CAPABILITY_STARTER,
        CRAWL_ACTIVE_STATUSES,
        TASK_KIND_ANALYZE,
    )
    from app.domain.site_health.entitlements import set_entitlement

    await _register(client, "rerun@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="rerun@example.com")
        # The monitored URL is user-source, so Starter is required to rerun it.
        await set_entitlement(session, scn.workspace_id, CAPABILITY_STARTER)
        await session.commit()
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    resp = await client.post(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages/{scn.monitored_url_id}/rerun",
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    # Response shape: identity to poll the fresh rerun.
    assert set(body) == {
        "crawl_id",
        "site_url_id",
        "task_id",
        "created_new_crawl",
        "analysis_status",
    }
    assert body["created_new_crawl"] is True
    assert body["site_url_id"] == str(scn.monitored_url_id)
    # A brand-new crawl id, distinct from the terminal source crawl.
    new_crawl_id = uuid.UUID(body["crawl_id"])
    assert new_crawl_id != scn.crawl_id
    assert body["analysis_status"] == "pending"

    async with session_factory() as session:
        new_crawl = await session.get(SiteCrawl, new_crawl_id)
        assert new_crawl is not None
        # The new crawl is active (runnable), not terminal.
        assert new_crawl.status in CRAWL_ACTIVE_STATUSES
        # Exactly one analyze task was seeded for the reran URL — and no
        # discover root task (so the worker never re-crawls the whole site).
        seeded = (
            (
                await session.execute(
                    select(SiteCrawlTask).where(SiteCrawlTask.crawl_id == new_crawl_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(seeded) == 1
        assert seeded[0].task_kind == TASK_KIND_ANALYZE
        assert seeded[0].site_url_id == scn.monitored_url_id
        assert str(seeded[0].id) == body["task_id"]
        # The returned URL is resolvable on the NEW crawl (an observation row
        # exists) so page-detail polling of the new crawl works.
        obs = await session.scalar(
            select(SiteUrlObservation.id).where(
                SiteUrlObservation.crawl_id == new_crawl_id,
                SiteUrlObservation.site_url_id == scn.monitored_url_id,
            )
        )
        assert obs is not None


async def test_rerun_page_unmonitored_url_is_conflict(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A URL that is not in the active monitored selection cannot be rerun."""
    from app.core.config.site_health import CAPABILITY_STARTER
    from app.domain.site_health.entitlements import set_entitlement

    await _register(client, "rerun-conflict@example.com")
    async with session_factory() as session:
        scn = await _seed_scenario(session, email="rerun-conflict@example.com")
        await set_entitlement(session, scn.workspace_id, CAPABILITY_STARTER)
        await session.commit()
    headers = {"X-Workspace-Id": str(scn.workspace_id)}

    # ``issue_url_id`` (url_b) is analyzed/admitted but NOT monitored.
    resp = await client.post(
        f"/api/v1/site-crawls/{scn.crawl_id}/pages/{scn.issue_url_id}/rerun",
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "rerun_not_allowed"
