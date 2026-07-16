"""Component tests for the Site Health discover worker (Task 3).

Runs the real ``SiteHealthWorker`` against a live Postgres schema with an
injected fake DNS resolver + ``httpx.MockTransport`` (fully offline). Covers:

  - Starter progressive inventory: the root fetch discovers in-scope links,
    admits child ``SiteUrl`` rows + child discover tasks, writes immutable
    ``SiteUrlObservation`` / ``SiteFetchArtifact`` / ``SiteFetchAttempt`` rows,
    and the crawl drains to ``completed`` with discovery ``completed``.
  - Inventory rows are observable BEFORE the crawl terminalizes (progressive).
  - Free workspace-wide stop-at-10 sample across TWO projects: the 11th URL is
    capped, only 10 ``free_sample`` monitored rows exist workspace-wide, and the
    auto-enqueued ``analyze`` tasks stay QUEUED (reserved for Task 5 — proves
    the ``kinds=[discover]`` claim filter never claims/force-fails them).
  - Lost-lease invariant: a discover task whose lease was stolen must NOT let
    ``_finalize_discovery`` prematurely complete the crawl.
  - Attempt numbering: the first attempt row is ``attempt_number == 1``.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.site_health import (
    ANALYSIS_STATUS_CANCELLED,
    ANALYSIS_STATUS_COMPLETED,
    CAPABILITY_FREE,
    CAPABILITY_STARTER,
    CRAWL_STATUS_COMPLETED,
    CRAWL_STATUS_FAILED,
    CRAWL_STATUS_PARTIALLY_COMPLETED,
    CRAWL_STATUS_RUNNING,
    DISCOVERY_STATUS_COMPLETED,
    DISCOVERY_STATUS_FAILED,
    DISCOVERY_STATUS_RUNNING,
    DISCOVERY_STATUS_SAMPLE_COMPLETED,
    SELECTION_SOURCE_FREE_SAMPLE,
    TASK_KIND_ANALYZE,
    TASK_KIND_DISCOVER,
)
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_LEASED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
)
from app.domain.site_health.entitlements import set_entitlement
from app.domain.site_health.normalization import canonical_identity
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteCrawlTask,
    SiteFetchArtifact,
    SiteFetchAttempt,
    SiteHealthSnapshot,
    SiteIssue,
    SitePageAnalysis,
    SiteRuleEvaluation,
    SiteUrl,
    SiteUrlObservation,
)
from app.workers.site_health_worker import SiteHealthWorker
from tests.component.site_health_helpers import seed_site_crawl

_PUBLIC_IP = "93.184.216.34"


class _FakeResolver:
    async def resolve(self, host: str, port: int) -> list[str]:
        return [_PUBLIC_IP]


def _html(links: list[str], *, title: str = "Page") -> bytes:
    anchors = "".join(f'<a href="{u}">l</a>' for u in links)
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body>{anchors}</body></html>"
    ).encode()


class _ByteStream(httpx.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aiter__(self):
        yield self._data

    async def aclose(self) -> None:
        return None


def _site_transport(pages: dict[str, bytes]) -> httpx.MockTransport:
    """A mock transport serving ``pages`` (keyed by path) as text/html.

    Any unknown path returns 404 so an out-of-scope/absent link is a clean
    fetch failure rather than an exception.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(
                404,
                headers={"content-type": "text/html"},
                stream=_ByteStream(b"not found"),
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            stream=_ByteStream(body),
        )

    return httpx.MockTransport(handler)


async def _configure_crawl(
    session: AsyncSession,
    *,
    crawl_id: uuid.UUID,
    sample_mode: bool,
    count_disclosure: bool,
) -> None:
    """Freeze the minimal worker-facing configuration onto a seeded crawl."""
    crawl = await session.get(SiteCrawl, crawl_id)
    crawl.sample_mode = sample_mode
    # The planner drives discovery -> running when queuing the crawl; mirror
    # that so the worker's sample_completed/completed transitions are valid.
    crawl.discovery_status = DISCOVERY_STATUS_RUNNING
    crawl.configuration = {
        "root_registrable_domain": "example.com",
        "include_globs": None,
        "exclude_globs": None,
        "count_disclosure": count_disclosure,
    }
    await session.commit()


def _worker(
    session_factory: async_sessionmaker[AsyncSession],
    pages: dict[str, bytes],
    *,
    owner: str = "site-test",
) -> SiteHealthWorker:
    return SiteHealthWorker(
        session_factory=session_factory,
        owner=owner,
        resolver=_FakeResolver(),
        transport=_site_transport(pages),
    )


# --- Starter progressive inventory ----------------------------------------


@pytest.mark.asyncio
async def test_starter_discover_admits_children_and_completes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    root = "https://example.com/"
    async with session_factory() as session:
        seed = await seed_site_crawl(session, task_count=0, root_url=root)
        await set_entitlement(session, seed.workspace_id, CAPABILITY_STARTER)
        await session.commit()
        await _configure_crawl(
            session,
            crawl_id=seed.crawl_id,
            sample_mode=False,
            count_disclosure=True,
        )
        # Seed the single root discover task (as the planner would).
        _canonical, root_hash = canonical_identity(root)
        session.add(
            SiteCrawlTask(
                crawl_id=seed.crawl_id,
                workspace_id=seed.workspace_id,
                task_kind=TASK_KIND_DISCOVER,
                requested_url=root,
                url_hash=root_hash,
                generation=0,
                idempotency_key=f"{seed.crawl_id}:{TASK_KIND_DISCOVER}:root:0",
                status=TASK_STATUS_QUEUED,
                randomized_position=0,
            )
        )
        await session.commit()

    pages = {
        "/": _html(
            [
                "https://example.com/a",
                "https://example.com/b",
                "https://external.org/x",  # out of scope -> not admitted
            ]
        ),
        "/a": _html([]),
        "/b": _html([]),
    }
    worker = _worker(session_factory, pages)
    await worker.run_until_idle()

    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl.status == CRAWL_STATUS_COMPLETED
        assert crawl.discovery_status == DISCOVERY_STATUS_COMPLETED
        assert crawl.inventory_complete is True
        assert crawl.analysis_status == ANALYSIS_STATUS_COMPLETED

        # A crawl with no analyze tasks still terminalizes the independent
        # analysis lifecycle and persists an explicit empty/null-score snapshot.
        snapshot = (
            await session.execute(
                select(SiteHealthSnapshot).where(
                    SiteHealthSnapshot.crawl_id == seed.crawl_id
                )
            )
        ).scalar_one()
        assert snapshot.selected_url_count == 0
        assert snapshot.analyzed_url_count == 0
        assert snapshot.technical_score is None
        assert snapshot.aeo_score is None
        assert snapshot.overall_score is None

        # Root + 2 in-scope children admitted; external.org excluded.
        urls = (
            await session.execute(
                select(SiteUrl.normalized_url).where(
                    SiteUrl.project_id == seed.project_id
                )
            )
        ).scalars().all()
        assert "https://example.com/" in urls
        assert "https://example.com/a" in urls
        assert "https://example.com/b" in urls
        assert not any("external.org" in u for u in urls)

        # Host populated on the identity rows (not blank).
        hosts = (
            await session.execute(
                select(SiteUrl.host).where(
                    SiteUrl.project_id == seed.project_id
                )
            )
        ).scalars().all()
        assert all(h == "example.com" for h in hosts)

        # Immutable evidence written for each fetched URL.
        obs_count = await session.scalar(
            select(func.count()).select_from(SiteUrlObservation).where(
                SiteUrlObservation.crawl_id == seed.crawl_id
            )
        )
        assert obs_count == 3  # root + a + b
        artifact_count = await session.scalar(
            select(func.count()).select_from(SiteFetchArtifact).where(
                SiteFetchArtifact.crawl_id == seed.crawl_id
            )
        )
        assert artifact_count == 3

        # Every discover task succeeded.
        statuses = (
            await session.execute(
                select(SiteCrawlTask.status).where(
                    SiteCrawlTask.crawl_id == seed.crawl_id,
                    SiteCrawlTask.task_kind == TASK_KIND_DISCOVER,
                )
            )
        ).scalars().all()
        assert statuses and all(s == TASK_STATUS_SUCCEEDED for s in statuses)

        # Every succeeded discover task points at its fetch artifact (mirrors
        # the audit worker's result_artifact_id contract).
        result_artifact_ids = (
            await session.execute(
                select(SiteCrawlTask.result_artifact_id).where(
                    SiteCrawlTask.crawl_id == seed.crawl_id,
                    SiteCrawlTask.task_kind == TASK_KIND_DISCOVER,
                )
            )
        ).scalars().all()
        assert result_artifact_ids and all(
            aid is not None for aid in result_artifact_ids
        )

        # First attempt row is numbered 1 (not 0).
        attempt_numbers = (
            await session.execute(
                select(SiteFetchAttempt.attempt_number).where(
                    SiteFetchAttempt.crawl_id == seed.crawl_id
                )
            )
        ).scalars().all()
        assert attempt_numbers and all(n == 1 for n in attempt_numbers)


@pytest.mark.asyncio
async def test_inventory_rows_present_before_crawl_terminalizes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Run only the root discover batch; children remain queued.

    Proves inventory (SiteUrl + observation) is durable progressively, before
    discovery/crawl reach a terminal state.
    """
    root = "https://example.com/"
    async with session_factory() as session:
        seed = await seed_site_crawl(session, task_count=0, root_url=root)
        await set_entitlement(session, seed.workspace_id, CAPABILITY_STARTER)
        await session.commit()
        await _configure_crawl(
            session,
            crawl_id=seed.crawl_id,
            sample_mode=False,
            count_disclosure=True,
        )
        _canonical, root_hash = canonical_identity(root)
        session.add(
            SiteCrawlTask(
                crawl_id=seed.crawl_id,
                workspace_id=seed.workspace_id,
                task_kind=TASK_KIND_DISCOVER,
                requested_url=root,
                url_hash=root_hash,
                generation=0,
                idempotency_key=f"{seed.crawl_id}:{TASK_KIND_DISCOVER}:root:0",
                status=TASK_STATUS_QUEUED,
                randomized_position=0,
            )
        )
        await session.commit()

    pages = {
        "/": _html(["https://example.com/a", "https://example.com/b"]),
        "/a": _html([]),
        "/b": _html([]),
    }
    worker = _worker(session_factory, pages)
    # A single batch: claim + run the root task only (children now queued).
    await worker.run_once()

    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        # Crawl is still active (children pending) but inventory already exists.
        assert crawl.status == CRAWL_STATUS_RUNNING
        admitted = await session.scalar(
            select(func.count()).select_from(SiteUrl).where(
                SiteUrl.project_id == seed.project_id
            )
        )
        assert admitted >= 3  # root + 2 children admitted during discovery
        pending_children = await session.scalar(
            select(func.count()).select_from(SiteCrawlTask).where(
                SiteCrawlTask.crawl_id == seed.crawl_id,
                SiteCrawlTask.task_kind == TASK_KIND_DISCOVER,
                SiteCrawlTask.status == TASK_STATUS_QUEUED,
            )
        )
        assert pending_children == 2


# --- Free workspace-wide stop-at-10 sample --------------------------------


@pytest.mark.asyncio
async def test_free_sample_stops_at_ten_across_two_projects(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two Free crawls in the SAME workspace share the 10-URL sample budget."""
    root_a = "https://example.com/"
    async with session_factory() as session:
        seed_a = await seed_site_crawl(session, task_count=0, root_url=root_a)
        # Second project in the SAME workspace.
        from app.models.project import Project
        from app.models.site_health import SiteHealthProfile

        project_b = Project(
            workspace_id=seed_a.workspace_id,
            name="Acme Site B",
            brand_name="Acme Corp",
            country_code="AU",
            language_code="en-AU",
            benchmark_mode="consumer_like",
            default_repetitions=1,
            website_url=root_a,
        )
        session.add(project_b)
        await session.flush()
        profile_b = SiteHealthProfile(
            workspace_id=seed_a.workspace_id,
            project_id=project_b.id,
            root_url=root_a,
            root_host="example.com",
            registrable_domain="example.com",
        )
        session.add(profile_b)
        await session.flush()
        crawl_b = SiteCrawl(
            workspace_id=seed_a.workspace_id,
            project_id=project_b.id,
            profile_id=profile_b.id,
            status=CRAWL_STATUS_RUNNING,
            root_url=root_a,
            random_seed="1",
            sample_mode=True,
        )
        session.add(crawl_b)
        await session.flush()
        crawl_b_id = crawl_b.id
        await set_entitlement(session, seed_a.workspace_id, CAPABILITY_FREE)
        await session.commit()

        # Configure both crawls for Free sample mode.
        await _configure_crawl(
            session,
            crawl_id=seed_a.crawl_id,
            sample_mode=True,
            count_disclosure=False,
        )
        await _configure_crawl(
            session,
            crawl_id=crawl_b_id,
            sample_mode=True,
            count_disclosure=False,
        )

        # Seed crawl A's root discover task only. Crawl B's root is seeded
        # AFTER worker A drains, so worker A cannot claim it (the discover
        # claim is workspace-global): this guarantees each worker exercises
        # exactly one project's frontier and B genuinely contributes to the
        # shared workspace budget.
        _canonical, root_hash = canonical_identity(root_a)

        def _root_task(crawl_id: uuid.UUID) -> SiteCrawlTask:
            return SiteCrawlTask(
                crawl_id=crawl_id,
                workspace_id=seed_a.workspace_id,
                task_kind=TASK_KIND_DISCOVER,
                requested_url=root_a,
                url_hash=root_hash,
                generation=0,
                idempotency_key=f"{crawl_id}:{TASK_KIND_DISCOVER}:root:0",
                status=TASK_STATUS_QUEUED,
                randomized_position=0,
            )

        session.add(_root_task(seed_a.crawl_id))
        await session.commit()

    # Each root page links to 8 in-scope children -> 16 candidates total, but
    # the workspace-wide Free budget is 10.
    links_a = [f"https://example.com/a{i}" for i in range(8)]
    links_b = [f"https://example.com/b{i}" for i in range(8)]
    pages = {"/": _html(links_a)}
    for i in range(8):
        pages[f"/a{i}"] = _html([])

    # Run crawl A's worker first: it admits up to 8 /a* URLs (under the cap).
    worker_a = _worker(session_factory, pages, owner="site-a")
    processed_a = await worker_a.run_until_idle()
    assert processed_a > 0

    # Now seed crawl B's root and run its worker: /b* URLs must top up the
    # shared workspace budget to exactly 10.
    async with session_factory() as session:
        session.add(_root_task(crawl_b_id))
        await session.commit()

    pages_b = {"/": _html(links_b)}
    for i in range(8):
        pages_b[f"/b{i}"] = _html([])
    worker_b = _worker(session_factory, pages_b, owner="site-b")
    processed_b = await worker_b.run_until_idle()
    # Worker B must actually do work, otherwise the shared-cap intent (project
    # B contributing to the workspace budget) is never exercised.
    assert processed_b > 0

    async with session_factory() as session:
        # Workspace-wide free_sample monitored rows capped at exactly 10.
        sample_count = await session.scalar(
            select(func.count()).select_from(MonitoredSiteUrl).where(
                MonitoredSiteUrl.workspace_id == seed_a.workspace_id,
                MonitoredSiteUrl.active.is_(True),
                MonitoredSiteUrl.selection_source
                == SELECTION_SOURCE_FREE_SAMPLE,
            )
        )
        assert sample_count == 10

        # Project B genuinely contributed to the shared budget: at least one
        # /b* URL was admitted as a free-sample monitored row.
        monitored_urls = (
            await session.execute(
                select(SiteUrl.normalized_url)
                .join(
                    MonitoredSiteUrl,
                    MonitoredSiteUrl.site_url_id == SiteUrl.id,
                )
                .where(
                    MonitoredSiteUrl.workspace_id == seed_a.workspace_id,
                    MonitoredSiteUrl.active.is_(True),
                    MonitoredSiteUrl.selection_source
                    == SELECTION_SOURCE_FREE_SAMPLE,
                )
            )
        ).scalars().all()
        assert any("/b" in u for u in monitored_urls)

        # Auto-enqueued analyze tasks (priority=1 by the Free sample path) are
        # now claimable and EXECUTED by the worker (Task 5): the workspace-wide
        # free-sample cap of 10 still holds (10 monitored URLs -> 10 analyze
        # tasks total), but they are succeeded rather than left queued.
        analyze_statuses = (
            await session.execute(
                select(SiteCrawlTask.status).where(
                    SiteCrawlTask.workspace_id == seed_a.workspace_id,
                    SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
                )
            )
        ).scalars().all()
        assert analyze_statuses
        assert all(s == TASK_STATUS_SUCCEEDED for s in analyze_statuses)
        assert len(analyze_statuses) == 10

        # Each executed analyze task produced a completed page analysis.
        analysis_count = await session.scalar(
            select(func.count()).select_from(SitePageAnalysis).where(
                SitePageAnalysis.workspace_id == seed_a.workspace_id
            )
        )
        assert analysis_count == 10

        # At least one crawl reached the Free cap terminal state.
        crawl_a = await session.get(SiteCrawl, seed_a.crawl_id)
        crawl_b = await session.get(SiteCrawl, crawl_b_id)
        assert crawl_a.status == CRAWL_STATUS_COMPLETED
        assert crawl_b.status == CRAWL_STATUS_COMPLETED
        assert DISCOVERY_STATUS_SAMPLE_COMPLETED in (
            crawl_a.discovery_status,
            crawl_b.discovery_status,
        )


# --- failed / partial finalization ----------------------------------------


async def _seed_root_only(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    root: str = "https://example.com/",
):
    """Seed a Starter crawl with a single root discover task, return the seed."""
    async with session_factory() as session:
        seed = await seed_site_crawl(session, task_count=0, root_url=root)
        await set_entitlement(session, seed.workspace_id, CAPABILITY_STARTER)
        await session.commit()
        await _configure_crawl(
            session,
            crawl_id=seed.crawl_id,
            sample_mode=False,
            count_disclosure=True,
        )
        _canonical, root_hash = canonical_identity(root)
        session.add(
            SiteCrawlTask(
                crawl_id=seed.crawl_id,
                workspace_id=seed.workspace_id,
                task_kind=TASK_KIND_DISCOVER,
                requested_url=root,
                url_hash=root_hash,
                generation=0,
                idempotency_key=f"{seed.crawl_id}:{TASK_KIND_DISCOVER}:root:0",
                status=TASK_STATUS_QUEUED,
                randomized_position=0,
            )
        )
        await session.commit()
    return seed


@pytest.mark.asyncio
async def test_fully_failed_root_terminalizes_crawl_as_failed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A root that 404s (no URL discovered) fails the crawl + discovery."""
    seed = await _seed_root_only(session_factory)
    # Empty page map -> the root "/" resolves to a 404 (non-retryable http_4xx).
    worker = _worker(session_factory, {}, owner="fail-root")
    await worker.run_until_idle()

    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl.discovered_url_count == 0
        assert crawl.failed_url_count >= 1
        assert crawl.status == CRAWL_STATUS_FAILED
        assert crawl.discovery_status == DISCOVERY_STATUS_FAILED
        # An empty inventory is not "complete".
        assert crawl.inventory_complete is False


@pytest.mark.asyncio
async def test_partial_failure_terminalizes_crawl_as_partially_completed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Root succeeds but a child 404s -> partially_completed / completed."""
    seed = await _seed_root_only(session_factory)
    # Root serves one in-scope child link; the child path is absent (-> 404).
    pages = {"/": _html(["https://example.com/missing"])}
    worker = _worker(session_factory, pages, owner="partial-root")
    await worker.run_until_idle()

    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl.discovered_url_count >= 1  # root succeeded
        assert crawl.failed_url_count >= 1  # child 404
        assert crawl.status == CRAWL_STATUS_PARTIALLY_COMPLETED
        # Discovery still terminalizes as completed (some inventory exists).
        assert crawl.discovery_status == DISCOVERY_STATUS_COMPLETED
        assert crawl.inventory_complete is True


# --- lost-lease invariant --------------------------------------------------


@pytest.mark.asyncio
async def test_stolen_lease_does_not_terminalize_crawl(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A task whose lease is stolen must not let the crawl complete early."""
    root = "https://example.com/"
    async with session_factory() as session:
        seed = await seed_site_crawl(session, task_count=0, root_url=root)
        await set_entitlement(session, seed.workspace_id, CAPABILITY_STARTER)
        await session.commit()
        await _configure_crawl(
            session,
            crawl_id=seed.crawl_id,
            sample_mode=False,
            count_disclosure=True,
        )
        _canonical, root_hash = canonical_identity(root)
        task = SiteCrawlTask(
            crawl_id=seed.crawl_id,
            workspace_id=seed.workspace_id,
            task_kind=TASK_KIND_DISCOVER,
            requested_url=root,
            url_hash=root_hash,
            generation=0,
            idempotency_key=f"{seed.crawl_id}:{TASK_KIND_DISCOVER}:root:0",
            status=TASK_STATUS_QUEUED,
            randomized_position=0,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    # Simulate the sweeper handing this task's lease to ANOTHER owner while it
    # is still non-terminal (LEASED to "other-owner").
    async with session_factory() as session:
        await session.execute(
            update(SiteCrawlTask)
            .where(SiteCrawlTask.id == task_id)
            .values(
                status=TASK_STATUS_LEASED,
                lease_owner="other-owner",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await session.commit()

    # This worker directly reconciles: the non-terminal task must keep the
    # crawl active (remaining discover work > 0).
    worker = _worker(session_factory, {"/": _html([])})
    await worker._reconcile_crawl_status(seed.crawl_id)

    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl.status == CRAWL_STATUS_RUNNING
        assert crawl.inventory_complete is False


# --- Task 5: analyze / link_check / reconcile ------------------------------


def _rich_html() -> bytes:
    """A page that passes most rules (title, meta, canonical, single h1, og,
    JSON-LD Organization, and >=100 words of body text)."""
    words = " ".join(f"word{i}" for i in range(140))
    return (
        "<html><head>"
        "<title>Rich Page</title>"
        '<meta name="description" content="A rich descriptive page.">'
        '<link rel="canonical" href="https://example.com/rich">'
        '<meta property="og:title" content="Rich Page">'
        '<meta property="og:description" content="Rich desc">'
        '<script type="application/ld+json">'
        '{"@type":"Organization","name":"Acme","url":"https://example.com"}'
        "</script>"
        "</head><body>"
        "<h1>Rich Page Heading</h1>"
        f"<p>{words}</p>"
        '<a href="https://example.com/other">internal</a>'
        '<a href="https://external.org/x">external</a>'
        "</body></html>"
    ).encode()


def _thin_html() -> bytes:
    """A page that FAILS several rules (no meta desc, no canonical, no h1,
    no og, no structured data, thin text)."""
    return (
        b"<html><head><title>Thin</title></head>"
        b"<body><p>too short</p></body></html>"
    )


async def _seed_analyze_ready(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    root: str = "https://example.com/rich",
    capability: str = CAPABILITY_STARTER,
):
    """Seed a Starter crawl with a monitored URL + one queued analyze task."""
    from app.core.config.site_health import (
        ANALYZER_VERSION,
        EXTRACTOR_VERSION,
        SCORING_VERSION,
        SELECTION_SOURCE_USER,
    )

    async with session_factory() as session:
        seed = await seed_site_crawl(session, task_count=0, root_url=root)
        await set_entitlement(session, seed.workspace_id, capability)
        await session.commit()
        # Discovery already finished; analysis is pending with one analyze task.
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        crawl.discovery_status = DISCOVERY_STATUS_COMPLETED
        crawl.discovered_url_count = 1
        crawl.inventory_complete = True
        crawl.extractor_version = EXTRACTOR_VERSION
        crawl.analyzer_version = ANALYZER_VERSION
        crawl.scoring_version = SCORING_VERSION
        crawl.configuration = {
            "root_registrable_domain": "example.com",
            "include_globs": None,
            "exclude_globs": None,
            "count_disclosure": True,
        }
        canonical, url_hash = canonical_identity(root)
        site_url = SiteUrl(
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            normalized_url=canonical,
            url_hash=url_hash,
            display_url=canonical,
            host="example.com",
            depth=0,
        )
        session.add(site_url)
        await session.flush()
        session.add(
            MonitoredSiteUrl(
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                profile_id=seed.profile_id,
                site_url_id=site_url.id,
                active=True,
                selection_source=SELECTION_SOURCE_USER,
            )
        )
        analyze_task = SiteCrawlTask(
            crawl_id=seed.crawl_id,
            workspace_id=seed.workspace_id,
            site_url_id=site_url.id,
            task_kind=TASK_KIND_ANALYZE,
            requested_url=root,
            url_hash=url_hash,
            generation=0,
            idempotency_key=f"{seed.crawl_id}:{TASK_KIND_ANALYZE}:{url_hash}:0",
            status=TASK_STATUS_QUEUED,
            priority=1,
            randomized_position=0,
        )
        session.add(analyze_task)
        await session.commit()
        return seed, site_url.id, analyze_task.id


@pytest.mark.asyncio
async def test_run_once_claims_one_task_to_keep_lease_heartbeated(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serial execution must not claim a batch whose later leases can expire."""
    async with session_factory() as session:
        await seed_site_crawl(session, task_count=2)

    worker = _worker(session_factory, {}, owner="single-claim")
    executed: list[uuid.UUID] = []

    async def record_only(task: SiteCrawlTask) -> None:
        executed.append(task.id)

    monkeypatch.setattr(worker, "_execute_task", record_only)
    assert await worker.run_once() == 1
    assert len(executed) == 1

    async with session_factory() as session:
        leased = await session.scalar(
            select(func.count()).select_from(SiteCrawlTask).where(
                SiteCrawlTask.status == TASK_STATUS_LEASED
            )
        )
        queued = await session.scalar(
            select(func.count()).select_from(SiteCrawlTask).where(
                SiteCrawlTask.status == TASK_STATUS_QUEUED
            )
        )
        assert leased == 1
        assert queued == 1


@pytest.mark.asyncio
async def test_analyze_guard_blocks_live_entitlement_downgrade_before_io(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed, _site_url_id, task_id = await _seed_analyze_ready(session_factory)
    async with session_factory() as session:
        await set_entitlement(session, seed.workspace_id, CAPABILITY_FREE)
        await session.commit()

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, stream=_ByteStream(b"unexpected"))

    worker = SiteHealthWorker(
        session_factory=session_factory,
        owner="downgraded",
        resolver=_FakeResolver(),
        transport=httpx.MockTransport(handler),
    )
    await worker.run_until_idle()

    async with session_factory() as session:
        task = await session.get(SiteCrawlTask, task_id)
        artifact_count = await session.scalar(
            select(func.count()).select_from(SiteFetchArtifact).where(
                SiteFetchArtifact.task_id == task_id
            )
        )
        assert requests == []
        assert task.status == TASK_STATUS_CANCELLED
        assert artifact_count == 0
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl.analysis_status == ANALYSIS_STATUS_CANCELLED
        assert crawl.status == CRAWL_STATUS_COMPLETED


@pytest.mark.asyncio
async def test_cancelled_user_analysis_does_not_penalize_applicable_free_sample(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Mixed cancelled+succeeded work is complete over applicable coverage."""
    seed, _user_site_url_id, user_task_id = await _seed_analyze_ready(
        session_factory
    )
    sample_url = "https://example.com/sample"
    canonical, sample_hash = canonical_identity(sample_url)
    async with session_factory() as session:
        sample_site_url = SiteUrl(
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            normalized_url=canonical,
            url_hash=sample_hash,
            display_url=canonical,
            host="example.com",
            depth=0,
        )
        session.add(sample_site_url)
        await session.flush()
        session.add(
            MonitoredSiteUrl(
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                profile_id=seed.profile_id,
                site_url_id=sample_site_url.id,
                active=True,
                selection_source=SELECTION_SOURCE_FREE_SAMPLE,
            )
        )
        sample_task = SiteCrawlTask(
            crawl_id=seed.crawl_id,
            workspace_id=seed.workspace_id,
            site_url_id=sample_site_url.id,
            task_kind=TASK_KIND_ANALYZE,
            requested_url=sample_url,
            url_hash=sample_hash,
            generation=0,
            idempotency_key=f"{seed.crawl_id}:analyze:{sample_hash}:0",
            status=TASK_STATUS_QUEUED,
            priority=1,
            randomized_position=1,
        )
        session.add(sample_task)
        await set_entitlement(session, seed.workspace_id, CAPABILITY_FREE)
        await session.commit()
        sample_task_id = sample_task.id

    worker = _worker(
        session_factory,
        {"/sample": _rich_html()},
        owner="mixed-applicability",
    )
    await worker.run_until_idle()

    async with session_factory() as session:
        user_task = await session.get(SiteCrawlTask, user_task_id)
        sample_task = await session.get(SiteCrawlTask, sample_task_id)
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        snapshot = (
            await session.execute(
                select(SiteHealthSnapshot).where(
                    SiteHealthSnapshot.crawl_id == seed.crawl_id
                )
            )
        ).scalar_one()
        assert user_task.status == TASK_STATUS_CANCELLED
        assert sample_task.status == TASK_STATUS_SUCCEEDED
        assert crawl.analysis_status == ANALYSIS_STATUS_COMPLETED
        assert crawl.status == CRAWL_STATUS_COMPLETED
        assert snapshot.analyzed_url_count == 1
        assert snapshot.overall_score is not None


@pytest.mark.asyncio
async def test_analyze_guard_discards_result_when_membership_removed_mid_fetch(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed, site_url_id, task_id = await _seed_analyze_ready(session_factory)
    worker = _worker(
        session_factory,
        {"/rich": _rich_html()},
        owner="removed-mid-fetch",
    )
    original_fetch = worker._fetch_analyze
    fetched = False

    async def fetch_then_remove(**kwargs):
        nonlocal fetched
        outcome = await original_fetch(**kwargs)
        fetched = True
        async with session_factory() as session:
            await session.execute(
                update(MonitoredSiteUrl)
                .where(
                    MonitoredSiteUrl.workspace_id == seed.workspace_id,
                    MonitoredSiteUrl.site_url_id == site_url_id,
                )
                .values(active=False)
            )
            await session.commit()
        return outcome

    monkeypatch.setattr(worker, "_fetch_analyze", fetch_then_remove)
    await worker.run_until_idle()

    async with session_factory() as session:
        task = await session.get(SiteCrawlTask, task_id)
        artifact_count = await session.scalar(
            select(func.count()).select_from(SiteFetchArtifact).where(
                SiteFetchArtifact.task_id == task_id
            )
        )
        analysis_count = await session.scalar(
            select(func.count()).select_from(SitePageAnalysis).where(
                SitePageAnalysis.crawl_id == seed.crawl_id
            )
        )
        assert fetched is True
        assert task.status == TASK_STATUS_CANCELLED
        assert artifact_count == 0
        assert analysis_count == 0
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl.analysis_status == ANALYSIS_STATUS_CANCELLED
        assert crawl.status == CRAWL_STATUS_COMPLETED


@pytest.mark.asyncio
async def test_reclaimed_analyze_acknowledges_already_persisted_analysis(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed, _site_url_id, task_id = await _seed_analyze_ready(session_factory)
    first = _worker(
        session_factory,
        {"/rich": _rich_html()},
        owner="ack-fails",
    )

    async def drop_queue_ack(**_kwargs) -> None:
        return None

    monkeypatch.setattr(first, "_finalize_queue_row", drop_queue_ack)
    assert await first.run_once() == 1

    async with session_factory() as session:
        task = await session.get(SiteCrawlTask, task_id)
        assert task.status == TASK_STATUS_RUNNING
        await session.execute(
            update(SiteCrawlTask)
            .where(SiteCrawlTask.id == task_id)
            .values(
                status=TASK_STATUS_QUEUED,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        await session.commit()

    requests: list[httpx.Request] = []

    def should_not_refetch(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, stream=_ByteStream(b"unexpected"))

    reclaimed = SiteHealthWorker(
        session_factory=session_factory,
        owner="reclaimed",
        resolver=_FakeResolver(),
        transport=httpx.MockTransport(should_not_refetch),
    )
    await reclaimed.run_until_idle()

    async with session_factory() as session:
        task = await session.get(SiteCrawlTask, task_id)
        artifacts = await session.scalar(
            select(func.count()).select_from(SiteFetchArtifact).where(
                SiteFetchArtifact.task_id == task_id
            )
        )
        analyses = await session.scalar(
            select(func.count()).select_from(SitePageAnalysis).where(
                SitePageAnalysis.crawl_id == seed.crawl_id
            )
        )
        assert requests == []
        assert task.status == TASK_STATUS_SUCCEEDED
        assert artifacts == 1
        assert analyses == 1


@pytest.mark.asyncio
async def test_analyze_task_persists_analysis_evaluations_issues_scores(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.core.config.site_health import (
        ANALYSIS_STATUS_COMPLETED,
        CRAWL_STATUS_COMPLETED,
        PAGE_ANALYSIS_STATUS_COMPLETED,
    )
    from app.models.site_health import (
        SiteHealthSnapshot,
        SiteIssue,
        SitePageAnalysis,
        SiteRuleEvaluation,
    )

    seed, site_url_id, _task_id = await _seed_analyze_ready(session_factory)
    pages = {"/rich": _rich_html()}
    worker = _worker(session_factory, pages, owner="analyze-rich")
    await worker.run_until_idle()

    async with session_factory() as session:
        analysis = (
            await session.execute(
                select(SitePageAnalysis).where(
                    SitePageAnalysis.crawl_id == seed.crawl_id
                )
            )
        ).scalar_one()
        assert analysis.status == PAGE_ANALYSIS_STATUS_COMPLETED
        assert analysis.overall_score is not None
        assert analysis.technical_score is not None
        assert analysis.aeo_score is not None
        assert analysis.site_url_id == site_url_id

        eval_count = await session.scalar(
            select(func.count()).select_from(SiteRuleEvaluation).where(
                SiteRuleEvaluation.analysis_id == analysis.id
            )
        )
        # One evaluation per catalog rule.
        assert eval_count == 9

        # A rich page passes every rule, so no issues are snapshotted.
        issue_count = await session.scalar(
            select(func.count()).select_from(SiteIssue).where(
                SiteIssue.crawl_id == seed.crawl_id
            )
        )
        assert issue_count == 0

        # An immutable artifact carries the normalized facts (no raw body).
        artifact = await session.get(SiteFetchArtifact, analysis.artifact_id)
        assert artifact is not None
        assert artifact.normalized_facts is not None
        assert artifact.normalized_facts.get("title") == "Rich Page"

        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl.status == CRAWL_STATUS_COMPLETED
        assert crawl.analysis_status == ANALYSIS_STATUS_COMPLETED
        assert crawl.analyzed_url_count == 1

        snapshot = (
            await session.execute(
                select(SiteHealthSnapshot).where(
                    SiteHealthSnapshot.crawl_id == seed.crawl_id
                )
            )
        ).scalar_one()
        assert snapshot.analyzed_url_count == 1
        assert snapshot.overall_score is not None
        assert snapshot.issue_count == issue_count


@pytest.mark.asyncio
async def test_thin_page_generates_multiple_issues(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.models.site_health import SiteIssue

    seed, _site_url_id, _task_id = await _seed_analyze_ready(
        session_factory, root="https://example.com/thin"
    )
    pages = {"/thin": _thin_html()}
    worker = _worker(session_factory, pages, owner="analyze-thin")
    await worker.run_until_idle()

    async with session_factory() as session:
        issues = (
            await session.execute(
                select(SiteIssue.rule_id).where(
                    SiteIssue.crawl_id == seed.crawl_id
                )
            )
        ).scalars().all()
        # Thin page fails: meta description, canonical, https, single h1,
        # structured data, open graph, sufficient text.
        assert "technical.meta_description_present" in issues
        assert "technical.canonical_present" in issues
        assert "aeo.sufficient_text" in issues
        assert len(issues) >= 5


@pytest.mark.asyncio
async def test_crawl_not_completed_while_analyze_queued(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A drained discover task must NOT complete the crawl while an analyze
    task is still queued."""
    root = "https://example.com/rich"
    async with session_factory() as session:
        seed = await seed_site_crawl(session, task_count=0, root_url=root)
        await set_entitlement(session, seed.workspace_id, CAPABILITY_STARTER)
        await session.commit()
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        crawl.discovery_status = DISCOVERY_STATUS_RUNNING
        crawl.configuration = {
            "root_registrable_domain": "example.com",
            "include_globs": None,
            "exclude_globs": None,
            "count_disclosure": True,
        }
        canonical, url_hash = canonical_identity(root)
        site_url = SiteUrl(
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            normalized_url=canonical,
            url_hash=url_hash,
            display_url=canonical,
            host="example.com",
            depth=0,
        )
        session.add(site_url)
        await session.flush()
        session.add(
            MonitoredSiteUrl(
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                profile_id=seed.profile_id,
                site_url_id=site_url.id,
                active=True,
            )
        )
        # One root discover task + one QUEUED analyze task the worker won't run.
        session.add(
            SiteCrawlTask(
                crawl_id=seed.crawl_id,
                workspace_id=seed.workspace_id,
                task_kind=TASK_KIND_DISCOVER,
                requested_url=root,
                url_hash=url_hash,
                generation=0,
                idempotency_key=f"{seed.crawl_id}:{TASK_KIND_DISCOVER}:root:0",
                status=TASK_STATUS_QUEUED,
                randomized_position=0,
            )
        )
        # Analyze task is LEASED to another owner (non-terminal, unclaimable).
        session.add(
            SiteCrawlTask(
                crawl_id=seed.crawl_id,
                workspace_id=seed.workspace_id,
                site_url_id=site_url.id,
                task_kind=TASK_KIND_ANALYZE,
                requested_url=root,
                url_hash=url_hash,
                generation=0,
                idempotency_key=f"{seed.crawl_id}:{TASK_KIND_ANALYZE}:{url_hash}:0",
                status=TASK_STATUS_LEASED,
                lease_owner="other-owner",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                priority=1,
                randomized_position=0,
            )
        )
        await session.commit()

    pages = {"/rich": _rich_html()}
    # Only claim discover so the analyze row stays non-terminal.
    worker = _worker(session_factory, pages, owner="disc-only")
    tasks = await worker._queue.claim(
        owner=worker.owner, limit=8, kinds=[TASK_KIND_DISCOVER]
    )
    for t in tasks:
        await worker._execute_task(t)

    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        # Discovery drained but the queued analyze keeps the crawl RUNNING.
        assert crawl.status == CRAWL_STATUS_RUNNING


@pytest.mark.asyncio
async def test_partial_analysis_failure_partially_completes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One analyze succeeds, one 404s -> partially_completed, no zero score."""
    from app.core.config.site_health import (
        ANALYSIS_STATUS_PARTIALLY_COMPLETED,
        SELECTION_SOURCE_USER,
    )
    from app.models.site_health import SiteHealthSnapshot

    async with session_factory() as session:
        seed = await seed_site_crawl(
            session, task_count=0, root_url="https://example.com/rich"
        )
        await set_entitlement(session, seed.workspace_id, CAPABILITY_STARTER)
        await session.commit()
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        crawl.discovery_status = DISCOVERY_STATUS_COMPLETED
        crawl.discovered_url_count = 2
        crawl.inventory_complete = True
        crawl.configuration = {
            "root_registrable_domain": "example.com",
            "include_globs": None,
            "exclude_globs": None,
            "count_disclosure": True,
        }
        for path in ("rich", "missing"):
            url = f"https://example.com/{path}"
            canonical, url_hash = canonical_identity(url)
            site_url = SiteUrl(
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                normalized_url=canonical,
                url_hash=url_hash,
                display_url=canonical,
                host="example.com",
                depth=0,
            )
            session.add(site_url)
            await session.flush()
            session.add(
                MonitoredSiteUrl(
                    workspace_id=seed.workspace_id,
                    project_id=seed.project_id,
                    profile_id=seed.profile_id,
                    site_url_id=site_url.id,
                    active=True,
                    selection_source=SELECTION_SOURCE_USER,
                )
            )
            session.add(
                SiteCrawlTask(
                    crawl_id=seed.crawl_id,
                    workspace_id=seed.workspace_id,
                    site_url_id=site_url.id,
                    task_kind=TASK_KIND_ANALYZE,
                    requested_url=url,
                    url_hash=url_hash,
                    generation=0,
                    idempotency_key=(
                        f"{seed.crawl_id}:{TASK_KIND_ANALYZE}:{url_hash}:0"
                    ),
                    status=TASK_STATUS_QUEUED,
                    priority=1,
                    randomized_position=0,
                )
            )
        await session.commit()

    # Only /rich is served; /missing 404s (non-retryable).
    pages = {"/rich": _rich_html()}
    worker = _worker(session_factory, pages, owner="partial-analyze")
    await worker.run_until_idle()

    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl.status == CRAWL_STATUS_PARTIALLY_COMPLETED
        assert crawl.analysis_status == ANALYSIS_STATUS_PARTIALLY_COMPLETED
        # Exactly one analysis succeeded; the snapshot aggregates only it and
        # never fabricates a zero for the missing URL.
        snapshot = (
            await session.execute(
                select(SiteHealthSnapshot).where(
                    SiteHealthSnapshot.crawl_id == seed.crawl_id
                )
            )
        ).scalar_one()
        assert snapshot.analyzed_url_count == 1
        assert snapshot.overall_score is not None
        assert snapshot.overall_score > 0


@pytest.mark.asyncio
async def test_snapshot_uses_only_latest_completed_analysis_and_issues(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Equal timestamps use UUID tie-break; stale scores/issues stay excluded."""
    from app.core.config.site_health import (
        FETCH_PURPOSE_ANALYZE,
        PAGE_ANALYSIS_STATUS_COMPLETED,
        RULE_OUTCOME_FAIL,
    )

    seed, site_url_id, first_task_id = await _seed_analyze_ready(session_factory)
    same_created_at = datetime.now(UTC)
    low_analysis_id = uuid.UUID(int=1)
    high_analysis_id = uuid.UUID(int=2)

    async with session_factory() as session:
        first_task = await session.get(SiteCrawlTask, first_task_id)
        first_task.status = TASK_STATUS_SUCCEEDED
        second_task = SiteCrawlTask(
            crawl_id=seed.crawl_id,
            workspace_id=seed.workspace_id,
            site_url_id=site_url_id,
            task_kind=TASK_KIND_ANALYZE,
            requested_url="https://example.com/rich",
            url_hash=first_task.url_hash,
            generation=1,
            idempotency_key=f"{seed.crawl_id}:analyze:latest:1",
            status=TASK_STATUS_SUCCEEDED,
            randomized_position=1,
        )
        session.add(second_task)
        await session.flush()

        artifacts = []
        for task in (first_task, second_task):
            artifact = SiteFetchArtifact(
                task_id=task.id,
                crawl_id=seed.crawl_id,
                workspace_id=seed.workspace_id,
                fetch_purpose=FETCH_PURPOSE_ANALYZE,
                requested_url=task.requested_url,
                final_url=task.requested_url,
            )
            session.add(artifact)
            artifacts.append(artifact)
        await session.flush()

        analyses = []
        for analysis_id, artifact, score in (
            (low_analysis_id, artifacts[0], 10.0),
            (high_analysis_id, artifacts[1], 90.0),
        ):
            analysis = SitePageAnalysis(
                id=analysis_id,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                crawl_id=seed.crawl_id,
                site_url_id=site_url_id,
                artifact_id=artifact.id,
                status=PAGE_ANALYSIS_STATUS_COMPLETED,
                technical_score=score,
                aeo_score=score,
                overall_score=score,
                created_at=same_created_at,
            )
            session.add(analysis)
            analyses.append(analysis)
        await session.flush()

        for index, (analysis, artifact) in enumerate(
            zip(analyses, artifacts, strict=True)
        ):
            evaluation = SiteRuleEvaluation(
                workspace_id=seed.workspace_id,
                analysis_id=analysis.id,
                source_artifact_id=artifact.id,
                rule_id=f"rule-{index}",
                dimension="technical",
                category="stale" if index == 0 else "fresh",
                severity="high",
                weight=1.0,
                outcome=RULE_OUTCOME_FAIL,
            )
            session.add(evaluation)
            await session.flush()
            session.add(
                SiteIssue(
                    workspace_id=seed.workspace_id,
                    project_id=seed.project_id,
                    crawl_id=seed.crawl_id,
                    site_url_id=site_url_id,
                    analysis_id=analysis.id,
                    evaluation_id=evaluation.id,
                    source_artifact_id=artifact.id,
                    rule_id=evaluation.rule_id,
                    dimension="technical",
                    category=evaluation.category,
                    severity="high",
                )
            )

        crawl = await session.get(SiteCrawl, seed.crawl_id)
        worker = _worker(session_factory, {}, owner="snapshot-latest")
        await worker._persist_snapshot(session, crawl=crawl)
        latest_artifact_id = artifacts[1].id
        await session.commit()

    async with session_factory() as session:
        snapshot = (
            await session.execute(
                select(SiteHealthSnapshot).where(
                    SiteHealthSnapshot.crawl_id == seed.crawl_id
                )
            )
        ).scalar_one()
        assert snapshot.analyzed_url_count == 1
        assert snapshot.overall_score == 90.0
        assert snapshot.source_analysis_ids == [high_analysis_id]
        assert snapshot.source_artifact_ids == [latest_artifact_id]
        assert snapshot.issue_count == 1
        assert snapshot.category_counts == {"fresh": 1}


@pytest.mark.asyncio
async def test_link_check_resolves_relative_targets_and_records_probe_provenance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.core.config.site_health import TASK_KIND_LINK_CHECK
    from app.models.site_health import SiteLinkReference

    source_url = "https://example.com/base/page"
    seed, site_url_id, _task_id = await _seed_analyze_ready(
        session_factory, root=source_url
    )
    source_html = (
        b"<html><head><title>Links</title></head><body>"
        b'<a href="../ok">head works</a>'
        b'<a href="/fallback">get fallback</a>'
        b'<a href="missing">missing</a>'
        b"</body></html>"
    )
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        method_path = (request.method, request.url.path)
        requests.append(method_path)
        if method_path == ("GET", "/base/page"):
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                stream=_ByteStream(source_html),
            )
        if method_path == ("HEAD", "/ok"):
            return httpx.Response(200, stream=_ByteStream(b""))
        if method_path == ("HEAD", "/fallback"):
            return httpx.Response(405, stream=_ByteStream(b""))
        if method_path == ("GET", "/fallback"):
            return httpx.Response(200, stream=_ByteStream(b"ok"))
        if method_path == ("HEAD", "/base/missing"):
            return httpx.Response(404, stream=_ByteStream(b""))
        return httpx.Response(404, stream=_ByteStream(b""))

    transport = httpx.MockTransport(handler)
    worker = SiteHealthWorker(
        session_factory=session_factory,
        owner="link-analyze",
        resolver=_FakeResolver(),
        transport=transport,
    )
    await worker.run_until_idle()

    # Now enqueue a link_check task for the same URL and run it.
    async with session_factory() as session:
        _canonical, url_hash = canonical_identity(source_url)
        link_task = SiteCrawlTask(
            crawl_id=seed.crawl_id,
            workspace_id=seed.workspace_id,
            site_url_id=site_url_id,
            task_kind=TASK_KIND_LINK_CHECK,
            requested_url=source_url,
            url_hash=url_hash,
            generation=0,
            idempotency_key=(
                f"{seed.crawl_id}:{TASK_KIND_LINK_CHECK}:{url_hash}:0"
            ),
            status=TASK_STATUS_QUEUED,
            randomized_position=0,
        )
        session.add(link_task)
        await session.flush()
        link_task_id = link_task.id
        # Re-open the crawl so the worker can run (it terminalized after
        # analyze); reset to running with analysis pending-safe status.
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        crawl.status = CRAWL_STATUS_RUNNING
        await session.commit()

    worker2 = SiteHealthWorker(
        session_factory=session_factory,
        owner="link-check",
        resolver=_FakeResolver(),
        transport=transport,
    )
    await worker2.run_until_idle()

    async with session_factory() as session:
        refs = (
            await session.execute(
                select(SiteLinkReference).where(
                    SiteLinkReference.workspace_id == seed.workspace_id
                )
            )
        ).scalars().all()
        by_url = {ref.target_url: ref for ref in refs}
        assert set(by_url) == {
            "https://example.com/ok",
            "https://example.com/fallback",
            "https://example.com/base/missing",
        }
        assert all(ref.target_task_id == link_task_id for ref in refs)
        # The existing schema has no reachability/status column. Its semantic
        # evidence fingerprint exposes the outcome prefix and hashes the
        # method/status evidence without overloading rel.
        assert by_url["https://example.com/ok"].evidence_fingerprint.startswith(
            "reachable:"
        )
        assert by_url[
            "https://example.com/fallback"
        ].evidence_fingerprint.startswith("reachable:")
        assert by_url[
            "https://example.com/base/missing"
        ].evidence_fingerprint.startswith("unreachable:")
        assert len({ref.evidence_fingerprint for ref in refs}) == 3

    assert ("HEAD", "/ok") in requests
    assert ("GET", "/ok") not in requests
    assert requests.index(("HEAD", "/fallback")) < requests.index(
        ("GET", "/fallback")
    )
    assert ("HEAD", "/base/missing") in requests
    assert ("GET", "/base/missing") not in requests
