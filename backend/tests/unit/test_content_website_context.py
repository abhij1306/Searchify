"""Deterministic Website-context projection tests (real Postgres schema).

Seeds Site Health evidence rows (crawl -> url -> task -> artifact ->
analysis) directly through the ORM and proves the projection is: newest
usable terminal crawl only, allowlist-only fields, homepage -> active
monitored -> stable-URL ordering, sanitised, bounded (pages + chars), and
byte-for-byte deterministic. No fetching, no provider calls.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.content import (
    CONTENT_CONTEXT_FIELD_MAX_CHARS,
    CONTENT_CONTEXT_MAX_CHARS,
    CONTENT_CONTEXT_MAX_PAGES,
    CONTENT_CONTEXT_PER_PAGE_BODY_CHARS,
    CONTEXT_MAX_H1,
    CONTEXT_MAX_H2,
    CONTEXT_STATUS_INCLUDED,
    CONTEXT_STATUS_UNAVAILABLE,
)
from app.core.config.site_health import (
    CRAWL_STATUS_COMPLETED,
    CRAWL_STATUS_FAILED,
    CRAWL_STATUS_PARTIALLY_COMPLETED,
    CRAWL_STATUS_RUNNING,
)
from app.domain.content.website_context import build_website_context
from app.models.project import Project
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteCrawlTask,
    SiteFetchArtifact,
    SiteHealthProfile,
    SitePageAnalysis,
    SiteUrl,
)
from app.models.workspace import Workspace

_ROOT = "https://example.com/"
_BASE_TIME = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def _hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:64]


async def _seed_project(
    session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Workspace + project + Site Health profile; returns their ids."""
    workspace = Workspace(name="Ctx WS")
    session.add(workspace)
    await session.flush()
    project = Project(
        workspace_id=workspace.id,
        name="Ctx Project",
        brand_name="Ctx Brand",
        country_code="AU",
        language_code="en-AU",
        benchmark_mode="consumer_like",
        default_repetitions=1,
        website_url=_ROOT,
    )
    session.add(project)
    await session.flush()
    profile = SiteHealthProfile(
        workspace_id=workspace.id,
        project_id=project.id,
        root_url=_ROOT,
        root_host="example.com",
        registrable_domain="example.com",
    )
    session.add(profile)
    await session.flush()
    return workspace.id, project.id, profile.id


async def _seed_crawl(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    profile_id: uuid.UUID,
    status: str = CRAWL_STATUS_COMPLETED,
    created_at: datetime = _BASE_TIME,
) -> SiteCrawl:
    crawl = SiteCrawl(
        workspace_id=workspace_id,
        project_id=project_id,
        profile_id=profile_id,
        status=status,
        root_url=_ROOT,
        random_seed="1",
        created_at=created_at,
        completed_at=created_at + timedelta(minutes=5),
        extractor_version="ex-v1",
        analyzer_version="an-v1",
    )
    session.add(crawl)
    await session.flush()
    return crawl


async def _seed_page(
    session: AsyncSession,
    *,
    crawl: SiteCrawl,
    url: str,
    facts: dict | None,
    monitored: bool = False,
    monitored_active: bool = True,
    profile_id: uuid.UUID | None = None,
) -> SiteUrl:
    """One analysed page: SiteUrl -> task -> artifact(facts) -> analysis."""
    site_url = SiteUrl(
        workspace_id=crawl.workspace_id,
        project_id=crawl.project_id,
        normalized_url=url,
        url_hash=_hash(f"{crawl.project_id}:{url}"),
    )
    session.add(site_url)
    await session.flush()
    task = SiteCrawlTask(
        crawl_id=crawl.id,
        workspace_id=crawl.workspace_id,
        site_url_id=site_url.id,
        requested_url=url,
        url_hash=site_url.url_hash,
        idempotency_key=f"{crawl.id}:{uuid.uuid4().hex}",
    )
    session.add(task)
    await session.flush()
    artifact = SiteFetchArtifact(
        task_id=task.id,
        crawl_id=crawl.id,
        workspace_id=crawl.workspace_id,
        requested_url=url,
        final_url=url,
        content_hash=_hash(url)[:32],
        extractor_version="ex-v1",
        normalized_facts=facts,
        fetched_at=_BASE_TIME,
    )
    session.add(artifact)
    await session.flush()
    session.add(
        SitePageAnalysis(
            workspace_id=crawl.workspace_id,
            project_id=crawl.project_id,
            crawl_id=crawl.id,
            site_url_id=site_url.id,
            artifact_id=artifact.id,
            analyzer_version="an-v1",
        )
    )
    if monitored and profile_id is not None:
        session.add(
            MonitoredSiteUrl(
                workspace_id=crawl.workspace_id,
                project_id=crawl.project_id,
                profile_id=profile_id,
                site_url_id=site_url.id,
                active=monitored_active,
            )
        )
    await session.flush()
    return site_url


def _facts(
    *,
    title: str = "Title",
    body: str = "Body text.",
    h1: list[str] | None = None,
    h2: list[str] | None = None,
) -> dict:
    return {
        "title": title,
        "meta_description": f"Meta for {title}",
        "headings": {"h1_texts": h1 or ["H1"], "h2_texts": h2 or ["H2"]},
        "body": {"text": body},
        # Non-allowlisted keys that must never leak into the projection.
        "links": {"internal": ["https://example.com/secret"]},
        "scripts": ["https://cdn.example.com/app.js"],
    }


async def test_unavailable_without_usable_crawl(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No crawl at all, and terminal crawls without facts, are unavailable."""
    async with session_factory() as session:
        ws_id, project_id, profile_id = await _seed_project(session)
        context = await build_website_context(
            session, workspace_id=ws_id, project_id=project_id
        )
        assert context.status == CONTEXT_STATUS_UNAVAILABLE
        assert context.pages == []
        assert context.snapshot() == {
            "status": CONTEXT_STATUS_UNAVAILABLE,
            "pages": [],
            "summary": None,
        }

        # A terminal crawl whose only artifact has no normalized facts still
        # yields unavailable (the existence check requires usable facts).
        crawl = await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
            status=CRAWL_STATUS_FAILED,
        )
        await _seed_page(session, crawl=crawl, url=f"{_ROOT}empty", facts=None)
        await session.commit()
        context = await build_website_context(
            session, workspace_id=ws_id, project_id=project_id
        )
        assert context.status == CONTEXT_STATUS_UNAVAILABLE


async def test_allowlist_fields_and_heading_caps(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Only allowlisted fields are emitted; h1/h2 lists are capped."""
    async with session_factory() as session:
        ws_id, project_id, profile_id = await _seed_project(session)
        crawl = await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
        )
        await _seed_page(
            session,
            crawl=crawl,
            url=f"{_ROOT}about",
            facts=_facts(
                title="About",
                h1=[f"H1 {i}" for i in range(CONTEXT_MAX_H1 + 2)],
                h2=[f"H2 {i}" for i in range(CONTEXT_MAX_H2 + 4)],
            ),
        )
        await session.commit()
        context = await build_website_context(
            session, workspace_id=ws_id, project_id=project_id
        )
        assert context.status == CONTEXT_STATUS_INCLUDED
        assert len(context.pages) == 1
        page = context.pages[0]
        assert set(page) == {
            "final_url",
            "title",
            "meta_description",
            "h1",
            "h2",
            "body_text",
        }
        assert page["title"] == "About"
        assert len(page["h1"]) == CONTEXT_MAX_H1
        assert len(page["h2"]) == CONTEXT_MAX_H2
        # Non-allowlisted fact content never leaks anywhere in the snapshot.
        assert "secret" not in str(context.snapshot())
        assert "app.js" not in str(context.snapshot())


async def test_ordering_homepage_then_monitored_then_stable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Homepage first, active monitored second, rest by URL; inactive
    monitored rows are ignored (tier 2)."""
    async with session_factory() as session:
        ws_id, project_id, profile_id = await _seed_project(session)
        crawl = await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
        )
        # Seed in an order unrelated to the expected output order.
        await _seed_page(
            session,
            crawl=crawl,
            url=f"{_ROOT}zzz-last",
            facts=_facts(title="Last"),
        )
        await _seed_page(
            session,
            crawl=crawl,
            url=f"{_ROOT}aaa-inactive",
            facts=_facts(title="Inactive monitored"),
            monitored=True,
            monitored_active=False,
            profile_id=profile_id,
        )
        await _seed_page(
            session,
            crawl=crawl,
            url=f"{_ROOT}pricing",
            facts=_facts(title="Pricing"),
            monitored=True,
            profile_id=profile_id,
        )
        await _seed_page(session, crawl=crawl, url=_ROOT, facts=_facts(title="Home"))
        await session.commit()
        context = await build_website_context(
            session, workspace_id=ws_id, project_id=project_id
        )
        titles = [page["title"] for page in context.pages]
        # Homepage, then the active monitored page, then the rest sorted by
        # normalized_url (inactive monitored falls into the stable tier).
        assert titles == ["Home", "Pricing", "Inactive monitored", "Last"]


async def test_sanitisation_and_field_caps(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Control chars stripped, whitespace collapsed, per-field caps hold."""
    async with session_factory() as session:
        ws_id, project_id, profile_id = await _seed_project(session)
        crawl = await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
        )
        # NB: \x00 itself can never reach JSONB (Postgres rejects NUL at
        # write time), so storable control chars exercise the stripper.
        await _seed_page(
            session,
            crawl=crawl,
            url=f"{_ROOT}dirty",
            facts=_facts(
                title="Ti\x01tle\x1b with\n\n   spaces\t" + "x" * 500,
                body="b\x07ody  text " * 500,
            ),
        )
        await session.commit()
        context = await build_website_context(
            session, workspace_id=ws_id, project_id=project_id
        )
        page = context.pages[0]
        assert "\x01" not in page["title"]
        assert "\x1b" not in page["title"]
        assert "  " not in page["title"]
        assert page["title"].startswith("Title with spaces")
        assert len(page["title"]) <= CONTENT_CONTEXT_FIELD_MAX_CHARS
        assert "\x07" not in page["body_text"]
        assert len(page["body_text"]) <= CONTENT_CONTEXT_PER_PAGE_BODY_CHARS


async def test_page_and_char_budgets(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Page cap and total char budget both bound the projection."""
    async with session_factory() as session:
        ws_id, project_id, profile_id = await _seed_project(session)
        crawl = await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
        )
        # 12 pages with maximal bodies: the page cap trims to 10, then the
        # 16k total budget drops trailing pages deterministically.
        for i in range(CONTENT_CONTEXT_MAX_PAGES + 2):
            await _seed_page(
                session,
                crawl=crawl,
                url=f"{_ROOT}page-{i:02d}",
                facts=_facts(title=f"Page {i:02d}", body="b" * 5000),
            )
        await session.commit()
        context = await build_website_context(
            session, workspace_id=ws_id, project_id=project_id
        )
        assert context.status == CONTEXT_STATUS_INCLUDED
        assert len(context.pages) <= CONTENT_CONTEXT_MAX_PAGES
        assert context.summary is not None
        assert context.summary["char_count"] <= CONTENT_CONTEXT_MAX_CHARS
        assert len(context.pages) < CONTENT_CONTEXT_MAX_PAGES + 2
        # Kept pages are the deterministic head of the ordering.
        titles = [page["title"] for page in context.pages]
        assert titles == [f"Page {i:02d}" for i in range(len(titles))]


async def test_newest_usable_terminal_crawl_wins(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """partially_completed is eligible; non-terminal and factless newer
    crawls are skipped in favour of the newest crawl with usable facts."""
    async with session_factory() as session:
        ws_id, project_id, profile_id = await _seed_project(session)
        old = await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
            status=CRAWL_STATUS_COMPLETED,
            created_at=_BASE_TIME,
        )
        await _seed_page(
            session, crawl=old, url=f"{_ROOT}old", facts=_facts(title="Old")
        )
        partial = await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
            status=CRAWL_STATUS_PARTIALLY_COMPLETED,
            created_at=_BASE_TIME + timedelta(hours=1),
        )
        await _seed_page(
            session,
            crawl=partial,
            url=f"{_ROOT}new",
            facts=_facts(title="New partial"),
        )
        # Newer failed crawl with no usable facts: skipped, not a dead end.
        newest_failed = await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
            status=CRAWL_STATUS_FAILED,
            created_at=_BASE_TIME + timedelta(hours=2),
        )
        await _seed_page(session, crawl=newest_failed, url=f"{_ROOT}broken", facts=None)
        # Non-terminal running crawl is never considered, however new.
        await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
            status=CRAWL_STATUS_RUNNING,
            created_at=_BASE_TIME + timedelta(hours=3),
        )
        await session.commit()
        context = await build_website_context(
            session, workspace_id=ws_id, project_id=project_id
        )
        assert context.status == CONTEXT_STATUS_INCLUDED
        assert context.summary is not None
        assert context.summary["crawl_id"] == str(partial.id)
        assert [p["title"] for p in context.pages] == ["New partial"]


async def test_empty_facts_object_falls_back_to_older_crawl(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A newer crawl whose only facts are ``{}`` is not usable: it must be
    skipped at the SQL predicate (not selected then rejected in memory,
    which would yield unavailable despite an older usable crawl)."""
    async with session_factory() as session:
        ws_id, project_id, profile_id = await _seed_project(session)
        old = await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
            status=CRAWL_STATUS_COMPLETED,
            created_at=_BASE_TIME,
        )
        await _seed_page(
            session, crawl=old, url=f"{_ROOT}old", facts=_facts(title="Old")
        )
        empty = await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
            status=CRAWL_STATUS_COMPLETED,
            created_at=_BASE_TIME + timedelta(hours=1),
        )
        await _seed_page(session, crawl=empty, url=f"{_ROOT}empty", facts={})
        await session.commit()
        context = await build_website_context(
            session, workspace_id=ws_id, project_id=project_id
        )
        assert context.status == CONTEXT_STATUS_INCLUDED
        assert context.summary is not None
        assert context.summary["crawl_id"] == str(old.id)
        assert [p["title"] for p in context.pages] == ["Old"]


async def test_deterministic_snapshot_and_provenance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Same inputs -> identical snapshot; provenance names its sources."""
    async with session_factory() as session:
        ws_id, project_id, profile_id = await _seed_project(session)
        crawl = await _seed_crawl(
            session,
            workspace_id=ws_id,
            project_id=project_id,
            profile_id=profile_id,
        )
        home = await _seed_page(
            session, crawl=crawl, url=_ROOT, facts=_facts(title="Home")
        )
        other = await _seed_page(
            session,
            crawl=crawl,
            url=f"{_ROOT}blog",
            facts=_facts(title="Blog"),
        )
        await session.commit()
        first = await build_website_context(
            session, workspace_id=ws_id, project_id=project_id
        )
        second = await build_website_context(
            session, workspace_id=ws_id, project_id=project_id
        )
        assert first.snapshot() == second.snapshot()
        summary = first.summary
        assert summary is not None
        assert summary["crawl_id"] == str(crawl.id)
        assert summary["page_count"] == 2
        assert summary["extractor_version"] == "ex-v1"
        assert summary["analyzer_version"] == "an-v1"
        assert set(summary["site_url_ids"]) == {str(home.id), str(other.id)}
        assert len(summary["artifact_ids"]) == 2
        assert all(summary["content_hashes"])
        assert all(summary["fetched_at"])


async def test_workspace_and_project_scoping(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Another project's crawl evidence never bleeds into the projection."""
    async with session_factory() as session:
        ws_id, project_id, profile_id = await _seed_project(session)
        other_ws, other_project, other_profile = await _seed_project(session)
        other_crawl = await _seed_crawl(
            session,
            workspace_id=other_ws,
            project_id=other_project,
            profile_id=other_profile,
        )
        await _seed_page(
            session,
            crawl=other_crawl,
            url=f"{_ROOT}other",
            facts=_facts(title="Other tenant"),
        )
        await session.commit()
        context = await build_website_context(
            session, workspace_id=ws_id, project_id=project_id
        )
        assert context.status == CONTEXT_STATUS_UNAVAILABLE
