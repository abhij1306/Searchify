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
    TASK_STATUS_LEASED,
    TASK_STATUS_QUEUED,
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

        # Reserved analyze tasks (auto-enqueued at priority=1 by the Free
        # sample path) are NEVER claimed by the discover worker — they stay
        # QUEUED for Task 5.
        analyze_statuses = (
            await session.execute(
                select(SiteCrawlTask.status).where(
                    SiteCrawlTask.workspace_id == seed_a.workspace_id,
                    SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
                )
            )
        ).scalars().all()
        assert analyze_statuses
        assert all(s == TASK_STATUS_QUEUED for s in analyze_statuses)
        assert len(analyze_statuses) == 10

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

    # This worker directly finalizes: the non-terminal task must keep the
    # crawl active (remaining discover work > 0).
    worker = _worker(session_factory, {"/": _html([])})
    await worker._finalize_discovery(seed.crawl_id)

    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, seed.crawl_id)
        assert crawl.status == CRAWL_STATUS_RUNNING
        assert crawl.inventory_complete is False
