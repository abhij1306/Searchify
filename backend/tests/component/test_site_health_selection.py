"""Task 4: monitored-set lifecycle domain logic against real Postgres.

Exercises ``app.domain.site_health.selection`` and the entitlement guard
helpers with ``SELECT ... FOR UPDATE`` on a real Postgres schema (the
``session_factory`` fixture from ``conftest``). Covers the two-project
workspace quota race, stale revision, foreign ids, add-during-discovery,
remove/re-add + generation increment, remove mid-fetch, Free->Starter sample
conversion + quota accounting, downgrade enforcement, persistent selection on a
second crawl, and unselected-URL analysis isolation.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.site_health import (
    CAPABILITY_FREE,
    CAPABILITY_STARTER,
    CRAWL_ACTIVE_STATUSES,
    CRAWL_STATUS_RUNNING,
    INITIAL_TASK_GENERATION,
    SELECTION_SOURCE_FREE_SAMPLE,
    SELECTION_SOURCE_USER,
    TASK_KIND_ANALYZE,
)
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
)
from app.domain.site_health.entitlements import (
    entitlement_allows_monitored_analysis,
    resolve_entitlement,
    set_entitlement,
)
from app.domain.site_health.planner import (
    CrawlAlreadyActiveError,
    create_crawl,
)
from app.domain.site_health.selection import (
    QuotaExceededError,
    SelectionValidationError,
    StaleSelectionVersionError,
    StarterRequiredError,
    crawl_is_active,
    evaluate_task_guard,
    lease_is_owned,
    monitored_is_active,
    replace_monitored_set,
    rerun_page,
    seed_monitored_targets,
)
from app.models.project import Project
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawl,
    SiteCrawlTask,
    SiteHealthProfile,
    SiteUrl,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:64]


@dataclass
class ProjectSeed:
    project_id: uuid.UUID
    profile_id: uuid.UUID
    crawl_id: uuid.UUID | None = None
    site_url_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class WorkspaceSeed:
    workspace_id: uuid.UUID
    projects: list[ProjectSeed] = field(default_factory=list)


async def _seed_project(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str,
    url_count: int,
    with_active_crawl: bool = False,
    root_url: str = "https://example.com/",
) -> ProjectSeed:
    project = Project(
        workspace_id=workspace_id,
        name=name,
        brand_name="Acme Corp",
        country_code="AU",
        language_code="en-AU",
        benchmark_mode="consumer_like",
        default_repetitions=1,
        website_url=root_url,
    )
    session.add(project)
    await session.flush()

    profile = SiteHealthProfile(
        workspace_id=workspace_id,
        project_id=project.id,
        root_url=root_url,
        root_host="example.com",
        registrable_domain="example.com",
    )
    session.add(profile)
    await session.flush()

    crawl_id: uuid.UUID | None = None
    if with_active_crawl:
        crawl = SiteCrawl(
            workspace_id=workspace_id,
            project_id=project.id,
            profile_id=profile.id,
            status=CRAWL_STATUS_RUNNING,
            root_url=root_url,
            random_seed="1",
        )
        session.add(crawl)
        await session.flush()
        crawl_id = crawl.id

    site_url_ids: list[uuid.UUID] = []
    for i in range(url_count):
        url = f"{root_url}{name}/page-{i}"
        site_url = SiteUrl(
            workspace_id=workspace_id,
            project_id=project.id,
            normalized_url=url,
            url_hash=_url_hash(url),
            display_url=url,
            host="example.com",
        )
        session.add(site_url)
        await session.flush()
        site_url_ids.append(site_url.id)

    return ProjectSeed(
        project_id=project.id,
        profile_id=profile.id,
        crawl_id=crawl_id,
        site_url_ids=site_url_ids,
    )


async def _seed_workspace(
    session: AsyncSession,
    *,
    capability: str = CAPABILITY_STARTER,
    projects: list[dict] | None = None,
) -> WorkspaceSeed:
    workspace = Workspace(name="Site WS")
    session.add(workspace)
    await session.flush()

    user = User(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner")
    )
    await session.flush()

    await set_entitlement(session, workspace.id, capability)

    seed = WorkspaceSeed(workspace_id=workspace.id)
    for spec in projects or []:
        seed.projects.append(
            await _seed_project(session, workspace_id=workspace.id, **spec)
        )
    await session.commit()
    return seed


# =========================================================================
# Full-set replacement: validation & versioning
# =========================================================================
@pytest.mark.asyncio
async def test_stale_selection_version_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(session, projects=[{"name": "a", "url_count": 3}])
    proj = seed.projects[0]

    async with session_factory() as session:
        with pytest.raises(StaleSelectionVersionError) as excinfo:
            await replace_monitored_set(
                session,
                workspace_id=seed.workspace_id,
                project_id=proj.project_id,
                site_url_ids=proj.site_url_ids[:1],
                expected_selection_version=99,
            )
        assert excinfo.value.current_version == 0
        await session.rollback()


@pytest.mark.asyncio
async def test_foreign_ids_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[
                {"name": "a", "url_count": 2},
                {"name": "b", "url_count": 2},
            ],
        )
    proj_a, proj_b = seed.projects

    # A URL that belongs to project B is not an authorized URL of project A.
    async with session_factory() as session:
        with pytest.raises(SelectionValidationError):
            await replace_monitored_set(
                session,
                workspace_id=seed.workspace_id,
                project_id=proj_a.project_id,
                site_url_ids=[proj_b.site_url_ids[0]],
                expected_selection_version=0,
            )
        await session.rollback()

    # A completely random id is likewise rejected.
    async with session_factory() as session:
        with pytest.raises(SelectionValidationError):
            await replace_monitored_set(
                session,
                workspace_id=seed.workspace_id,
                project_id=proj_a.project_id,
                site_url_ids=[uuid.uuid4()],
                expected_selection_version=0,
            )
        await session.rollback()


@pytest.mark.asyncio
async def test_free_workspace_selection_requires_starter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            capability=CAPABILITY_FREE,
            projects=[{"name": "a", "url_count": 2}],
        )
    proj = seed.projects[0]
    async with session_factory() as session:
        with pytest.raises(StarterRequiredError):
            await replace_monitored_set(
                session,
                workspace_id=seed.workspace_id,
                project_id=proj.project_id,
                site_url_ids=proj.site_url_ids[:1],
                expected_selection_version=0,
            )
        await session.rollback()


# =========================================================================
# Two-project workspace quota race (>50 blocked)
# =========================================================================
@pytest.mark.asyncio
async def test_two_project_quota_race_blocks_over_50(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # One Starter workspace, two projects, each with 30 discovered URLs.
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[
                {"name": "a", "url_count": 30},
                {"name": "b", "url_count": 30},
            ],
        )
    proj_a, proj_b = seed.projects

    async def _select(proj: ProjectSeed) -> str:
        async with session_factory() as session:
            try:
                await replace_monitored_set(
                    session,
                    workspace_id=seed.workspace_id,
                    project_id=proj.project_id,
                    site_url_ids=proj.site_url_ids,  # 30 each
                    expected_selection_version=0,
                )
                await session.commit()
                return "ok"
            except QuotaExceededError:
                await session.rollback()
                return "quota"

    # Both request 30 concurrently -> 60 > 50. The FOR UPDATE lock on the
    # entitlement row serializes them; exactly one wins.
    results = await asyncio.gather(_select(proj_a), _select(proj_b))
    assert sorted(results) == ["ok", "quota"]

    # Never more than 50 active workspace-wide.
    async with session_factory() as session:
        active = (
            await session.execute(
                select(func.count())
                .select_from(MonitoredSiteUrl)
                .where(
                    MonitoredSiteUrl.workspace_id == seed.workspace_id,
                    MonitoredSiteUrl.active.is_(True),
                )
            )
        ).scalar_one()
    assert active == 30


@pytest.mark.asyncio
async def test_quota_exceeded_reports_limit_and_used(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[
                {"name": "a", "url_count": 40},
                {"name": "b", "url_count": 40},
            ],
        )
    proj_a, proj_b = seed.projects
    # Commit 40 in project A first.
    async with session_factory() as session:
        await replace_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj_a.project_id,
            site_url_ids=proj_a.site_url_ids,
            expected_selection_version=0,
        )
        await session.commit()
    # Then 40 in project B -> 80 > 50.
    async with session_factory() as session:
        with pytest.raises(QuotaExceededError) as excinfo:
            await replace_monitored_set(
                session,
                workspace_id=seed.workspace_id,
                project_id=proj_b.project_id,
                site_url_ids=proj_b.site_url_ids,
                expected_selection_version=0,
            )
        assert excinfo.value.limit == 50
        assert excinfo.value.currently_used == 40
        await session.rollback()


# =========================================================================
# Add during discovery + remove/re-add generation increment
# =========================================================================
@pytest.mark.asyncio
async def test_add_during_discovery_enqueues_analyze(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 3, "with_active_crawl": True}],
        )
    proj = seed.projects[0]

    async with session_factory() as session:
        result = await replace_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            site_url_ids=proj.site_url_ids[:2],
            expected_selection_version=0,
        )
        await session.commit()
    assert len(result.enqueued_task_ids) == 2

    async with session_factory() as session:
        tasks = (
            (
                await session.execute(
                    select(SiteCrawlTask).where(
                        SiteCrawlTask.crawl_id == proj.crawl_id,
                        SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(tasks) == 2
    assert all(t.status == TASK_STATUS_QUEUED for t in tasks)
    assert all(t.generation == INITIAL_TASK_GENERATION for t in tasks)


@pytest.mark.asyncio
async def test_remove_readd_allocates_next_generation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 2, "with_active_crawl": True}],
        )
    proj = seed.projects[0]
    target = proj.site_url_ids[0]

    # v1: add the URL (generation 0).
    async with session_factory() as session:
        await replace_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            site_url_ids=[target],
            expected_selection_version=0,
        )
        await session.commit()

    # v2: remove it (its queued analyze task is cancelled).
    async with session_factory() as session:
        removed = await replace_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            site_url_ids=[],
            expected_selection_version=1,
        )
        await session.commit()
    assert target in removed.removed_ids
    assert len(removed.cancelled_task_ids) == 1

    # v3: re-add it -> a NEW task at generation 1 (never collides with the
    # cancelled generation-0 slot).
    async with session_factory() as session:
        readd = await replace_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            site_url_ids=[target],
            expected_selection_version=2,
        )
        await session.commit()
    assert len(readd.enqueued_task_ids) == 1

    async with session_factory() as session:
        tasks = (
            (
                await session.execute(
                    select(SiteCrawlTask)
                    .where(
                        SiteCrawlTask.crawl_id == proj.crawl_id,
                        SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
                    )
                    .order_by(SiteCrawlTask.generation.asc())
                )
            )
            .scalars()
            .all()
        )
    # Two task identities: the cancelled gen-0 and the fresh gen-1.
    assert [t.generation for t in tasks] == [0, 1]
    gen0, gen1 = tasks
    assert gen0.status == TASK_STATUS_CANCELLED
    assert gen1.status == TASK_STATUS_QUEUED
    assert gen0.idempotency_key != gen1.idempotency_key


# =========================================================================
# Remove mid-fetch: worker guard rejects a deactivated membership
# =========================================================================
@pytest.mark.asyncio
async def test_remove_mid_fetch_guard_blocks_persistence(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 1, "with_active_crawl": True}],
        )
    proj = seed.projects[0]
    target = proj.site_url_ids[0]

    async with session_factory() as session:
        await replace_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            site_url_ids=[target],
            expected_selection_version=0,
        )
        await session.commit()

    # Simulate a worker that claimed + is running the analyze task.
    async with session_factory() as session:
        task = (
            (
                await session.execute(
                    select(SiteCrawlTask).where(SiteCrawlTask.crawl_id == proj.crawl_id)
                )
            )
            .scalars()
            .first()
        )
        assert task is not None
        task.status = TASK_STATUS_RUNNING
        task.lease_owner = "worker-1"
        await session.commit()

    # User removes the URL mid-fetch.
    async with session_factory() as session:
        await replace_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            site_url_ids=[],
            expected_selection_version=1,
        )
        await session.commit()

    # Before persistence, the worker re-loads rows and evaluates the guard.
    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, proj.crawl_id)
        task = (
            (
                await session.execute(
                    select(SiteCrawlTask).where(
                        SiteCrawlTask.crawl_id == proj.crawl_id,
                        SiteCrawlTask.status == TASK_STATUS_RUNNING,
                    )
                )
            )
            .scalars()
            .first()
        )
        monitored = (
            (
                await session.execute(
                    select(MonitoredSiteUrl).where(
                        MonitoredSiteUrl.project_id == proj.project_id,
                        MonitoredSiteUrl.site_url_id == target,
                    )
                )
            )
            .scalars()
            .first()
        )
        entitlement = await resolve_entitlement(session, seed.workspace_id)

        decision = evaluate_task_guard(
            crawl=crawl,
            task=task,
            monitored=monitored,
            entitlement=entitlement,
            owner="worker-1",
        )
    assert not decision.ok
    assert decision.reason == "not_actively_monitored"
    assert not monitored_is_active(monitored)


# =========================================================================
# Free->Starter sample conversion + quota accounting
# =========================================================================
@pytest.mark.asyncio
async def test_free_to_starter_converts_and_deactivates_samples(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Free workspace with a pre-existing system-managed sample of 3 URLs.
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            capability=CAPABILITY_FREE,
            projects=[{"name": "a", "url_count": 4}],
        )
        proj = seed.projects[0]
        profile_id = proj.profile_id
        for sid in proj.site_url_ids[:3]:
            session.add(
                MonitoredSiteUrl(
                    workspace_id=seed.workspace_id,
                    project_id=proj.project_id,
                    profile_id=profile_id,
                    site_url_id=sid,
                    active=True,
                    selection_source=SELECTION_SOURCE_FREE_SAMPLE,
                )
            )
        await session.commit()

    # Upgrade to Starter.
    async with session_factory() as session:
        await set_entitlement(session, seed.workspace_id, CAPABILITY_STARTER)
        await session.commit()

    # First Starter selection: keep sample[0], drop sample[1] and sample[2],
    # add a brand-new url[3].
    keep = proj.site_url_ids[0]
    add = proj.site_url_ids[3]
    async with session_factory() as session:
        result = await replace_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            site_url_ids=[keep, add],
            expected_selection_version=0,
        )
        await session.commit()

    assert result.workspace_used == 2
    assert set(result.active_ids) == {keep, add}
    assert set(result.removed_ids) == set(proj.site_url_ids[1:3])

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(MonitoredSiteUrl).where(
                        MonitoredSiteUrl.project_id == proj.project_id
                    )
                )
            )
            .scalars()
            .all()
        )
    by_id = {r.site_url_id: r for r in rows}
    # Kept sample row converted to user-managed and stays active.
    assert by_id[keep].active
    assert by_id[keep].selection_source == SELECTION_SOURCE_USER
    # Omitted sample rows deactivated but PRESERVED (never deleted).
    assert not by_id[proj.site_url_ids[1]].active
    assert not by_id[proj.site_url_ids[2]].active
    assert proj.site_url_ids[1] in by_id
    # New row is user-managed active.
    assert by_id[add].active
    assert by_id[add].selection_source == SELECTION_SOURCE_USER


# =========================================================================
# Downgrade enforcement (pure guard)
# =========================================================================
@pytest.mark.asyncio
async def test_downgrade_blocks_user_row_but_allows_sample(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(session, capability=CAPABILITY_FREE, projects=[])
    async with session_factory() as session:
        free = await resolve_entitlement(session, seed.workspace_id)
        # Free entitlement blocks NEW analysis of a user-managed row...
        assert not entitlement_allows_monitored_analysis(
            free, selection_source=SELECTION_SOURCE_USER
        )
        # ...but still allows its own system-managed free_sample rows.
        assert entitlement_allows_monitored_analysis(
            free, selection_source=SELECTION_SOURCE_FREE_SAMPLE
        )
    # Missing entitlement fails closed.
    assert not entitlement_allows_monitored_analysis(
        None, selection_source=SELECTION_SOURCE_FREE_SAMPLE
    )


@pytest.mark.asyncio
async def test_guard_helpers_lease_and_crawl(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 1, "with_active_crawl": True}],
        )
    proj = seed.projects[0]
    async with session_factory() as session:
        crawl = await session.get(SiteCrawl, proj.crawl_id)
        assert crawl_is_active(crawl)
        crawl.status = TASK_STATUS_CANCELLED
        assert not crawl_is_active(crawl)
        assert not crawl_is_active(None)

    # Lease ownership.
    task = SiteCrawlTask(
        crawl_id=proj.crawl_id,
        workspace_id=seed.workspace_id,
        task_kind=TASK_KIND_ANALYZE,
        url_hash="h",
        idempotency_key="k",
        status=TASK_STATUS_RUNNING,
        lease_owner="worker-1",
    )
    assert lease_is_owned(task, owner="worker-1")
    assert not lease_is_owned(task, owner="worker-2")
    assert not lease_is_owned(None, owner="worker-1")


# =========================================================================
# Persistent selection on second crawl + unselected-URL isolation
# =========================================================================
@pytest.mark.asyncio
async def test_second_crawl_seeds_active_monitored_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 5, "with_active_crawl": True}],
        )
    proj = seed.projects[0]
    monitored_ids = proj.site_url_ids[:2]

    # Select 2 of the 5 discovered URLs in the first crawl.
    async with session_factory() as session:
        await replace_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            site_url_ids=monitored_ids,
            expected_selection_version=0,
        )
        await session.commit()

    # A later manual recrawl: a brand-new crawl seeds the persistent set.
    async with session_factory() as session:
        crawl2 = SiteCrawl(
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            profile_id=proj.profile_id,
            status=CRAWL_STATUS_RUNNING,
            root_url="https://example.com/",
            random_seed="2",
        )
        session.add(crawl2)
        await session.flush()
        seeded = await seed_monitored_targets(session, crawl=crawl2)
        await session.commit()
        crawl2_id = crawl2.id

    assert len(seeded) == 2

    async with session_factory() as session:
        tasks = (
            (
                await session.execute(
                    select(SiteCrawlTask).where(
                        SiteCrawlTask.crawl_id == crawl2_id,
                        SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
                    )
                )
            )
            .scalars()
            .all()
        )
    # Only the 2 active monitored URLs get analyze tasks — the other 3
    # discovered-but-unselected URLs never do (analysis isolation).
    assert len(tasks) == 2
    assert {t.site_url_id for t in tasks} == set(monitored_ids)
    assert all(t.generation == INITIAL_TASK_GENERATION for t in tasks)


@pytest.mark.asyncio
async def test_seed_monitored_targets_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 2, "with_active_crawl": True}],
        )
    proj = seed.projects[0]
    async with session_factory() as session:
        await replace_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            site_url_ids=proj.site_url_ids,
            expected_selection_version=0,
        )
        await session.commit()

    async with session_factory() as session:
        crawl2 = SiteCrawl(
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            profile_id=proj.profile_id,
            status=CRAWL_STATUS_RUNNING,
            root_url="https://example.com/",
            random_seed="2",
        )
        session.add(crawl2)
        await session.flush()
        first = await seed_monitored_targets(session, crawl=crawl2)
        # A second seeding pass (e.g. retry) creates no duplicate slot.
        second = await seed_monitored_targets(session, crawl=crawl2)
        await session.commit()
        crawl2_id = crawl2.id

    assert len(first) == 2
    assert second == []

    async with session_factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(SiteCrawlTask)
                .where(
                    SiteCrawlTask.crawl_id == crawl2_id,
                    SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
                )
            )
        ).scalar_one()
    assert count == 2


# =========================================================================
# Concurrency: full crawl vs terminal-page rerun cannot both go active
# =========================================================================
@pytest.mark.asyncio
async def test_full_crawl_and_page_rerun_cannot_both_create_active_crawl(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A concurrent full crawl and a terminal-page rerun serialize.

    ``create_crawl`` locks the project row before its active-crawl check, and
    ``rerun_page`` now takes the SAME project lock before deciding whether to
    mint a fresh single-page rerun crawl. So one of two outcomes must hold, but
    never "two active crawls":

    - full crawl wins the lock -> it creates the active crawl; the rerun then
      sees that active crawl and enqueues into it (mints nothing); OR
    - rerun wins the lock -> it mints the single-page rerun crawl; the full
      crawl then sees an active crawl and raises ``CrawlAlreadyActiveError``.

    Either way exactly ONE active crawl exists at the end.
    """
    # Starter project with NO active crawl and one active monitored URL, plus a
    # crawlable website_url so ``create_crawl`` can build a full crawl.
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 1}],
        )
        proj = seed.projects[0]
        session.add(
            MonitoredSiteUrl(
                workspace_id=seed.workspace_id,
                project_id=proj.project_id,
                profile_id=proj.profile_id,
                site_url_id=proj.site_url_ids[0],
                active=True,
                selection_source=SELECTION_SOURCE_USER,
            )
        )
        await session.commit()

    async def _full_crawl() -> str:
        async with session_factory() as session:
            try:
                await create_crawl(
                    session,
                    workspace_id=seed.workspace_id,
                    project_id=proj.project_id,
                )
                return "created"
            except CrawlAlreadyActiveError:
                await session.rollback()
                return "already_active"

    async def _rerun() -> str:
        async with session_factory() as session:
            try:
                result = await rerun_page(
                    session,
                    workspace_id=seed.workspace_id,
                    project_id=proj.project_id,
                    site_url_id=proj.site_url_ids[0],
                )
                await session.commit()
                return "minted" if result.created_new_crawl else "reused_active"
            except CrawlAlreadyActiveError:
                await session.rollback()
                return "already_active"

    results = await asyncio.gather(_full_crawl(), _rerun())

    # Exactly one active crawl regardless of who won the project lock.
    async with session_factory() as session:
        active = (
            await session.execute(
                select(func.count())
                .select_from(SiteCrawl)
                .where(
                    SiteCrawl.project_id == proj.project_id,
                    SiteCrawl.status.in_(list(CRAWL_ACTIVE_STATUSES)),
                )
            )
        ).scalar_one()
    assert active == 1, results

    # And the pair of outcomes is one of the two serialized possibilities —
    # never (created, minted), which would be two active crawls.
    full_result, rerun_result = results
    assert (full_result, rerun_result) in {
        ("created", "reused_active"),
        ("already_active", "minted"),
    }, results


@pytest.mark.asyncio
async def test_page_rerun_before_full_crawl_blocks_second_active_crawl(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Deterministic "rerun wins the lock" ordering.

    When a terminal-page rerun commits its fresh single-page crawl first, a
    subsequent full ``create_crawl`` sees the active crawl (under the project
    lock the rerun released on commit) and refuses to create a second one.
    """
    async with session_factory() as session:
        seed = await _seed_workspace(session, projects=[{"name": "a", "url_count": 1}])
        proj = seed.projects[0]
        session.add(
            MonitoredSiteUrl(
                workspace_id=seed.workspace_id,
                project_id=proj.project_id,
                profile_id=proj.profile_id,
                site_url_id=proj.site_url_ids[0],
                active=True,
                selection_source=SELECTION_SOURCE_USER,
            )
        )
        await session.commit()

    # Rerun first: no active crawl yet -> mints a fresh single-page crawl.
    async with session_factory() as session:
        result = await rerun_page(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            site_url_id=proj.site_url_ids[0],
        )
        await session.commit()
    assert result.created_new_crawl is True

    # Full crawl now sees the active crawl and refuses.
    async with session_factory() as session:
        with pytest.raises(CrawlAlreadyActiveError):
            await create_crawl(
                session,
                workspace_id=seed.workspace_id,
                project_id=proj.project_id,
            )
        await session.rollback()

    async with session_factory() as session:
        active = (
            await session.execute(
                select(func.count())
                .select_from(SiteCrawl)
                .where(
                    SiteCrawl.project_id == proj.project_id,
                    SiteCrawl.status.in_(list(CRAWL_ACTIVE_STATUSES)),
                )
            )
        ).scalar_one()
    assert active == 1


@pytest.mark.asyncio
async def test_full_crawl_before_page_rerun_reuses_active_crawl(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Deterministic "full crawl wins the lock" ordering.

    When a full crawl commits first, a subsequent terminal-page rerun sees the
    active crawl and enqueues into it instead of minting a second crawl.
    """
    async with session_factory() as session:
        seed = await _seed_workspace(session, projects=[{"name": "a", "url_count": 1}])
        proj = seed.projects[0]
        session.add(
            MonitoredSiteUrl(
                workspace_id=seed.workspace_id,
                project_id=proj.project_id,
                profile_id=proj.profile_id,
                site_url_id=proj.site_url_ids[0],
                active=True,
                selection_source=SELECTION_SOURCE_USER,
            )
        )
        await session.commit()

    # Full crawl first (commits an active crawl).
    async with session_factory() as session:
        await create_crawl(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
        )

    # Rerun now sees the active crawl and reuses it (mints nothing).
    async with session_factory() as session:
        result = await rerun_page(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            site_url_id=proj.site_url_ids[0],
        )
        await session.commit()
    assert result.created_new_crawl is False

    async with session_factory() as session:
        active = (
            await session.execute(
                select(func.count())
                .select_from(SiteCrawl)
                .where(
                    SiteCrawl.project_id == proj.project_id,
                    SiteCrawl.status.in_(list(CRAWL_ACTIVE_STATUSES)),
                )
            )
        ).scalar_one()
    assert active == 1
