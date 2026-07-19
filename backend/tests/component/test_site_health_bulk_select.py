"""Server-resolved bulk selection (``bulk_select_monitored_set``).

Covers: ``first_n`` in the inventory's deterministic ``(normalized_url, id)``
order, ``all`` + ``none`` (clear preserves rows), over-quota rejection with the
same coded error as a manual over-selection, validation (missing count /
foreign crawl) and stale-version rejection, and admission scoping (``all``
never sweeps URLs outside the crawl's admitted set).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.site_health import TASK_KIND_ANALYZE
from app.core.config.task_queue import TASK_STATUS_QUEUED
from app.domain.site_health.selection import (
    QuotaExceededError,
    SelectionValidationError,
    StaleSelectionVersionError,
    bulk_select_monitored_set,
)
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawlTask,
    SiteUrl,
    SiteUrlObservation,
)
from tests.component.test_site_health_selection import (
    ProjectSeed,
    WorkspaceSeed,
    _seed_workspace,
)


async def _admit_urls(
    session: AsyncSession,
    *,
    seed: WorkspaceSeed,
    proj: ProjectSeed,
    only_first: int | None = None,
) -> None:
    """Write the crawl-admission observations bulk selection is scoped by."""
    assert proj.crawl_id is not None
    ids = proj.site_url_ids if only_first is None else proj.site_url_ids[:only_first]
    for site_url_id in ids:
        session.add(
            SiteUrlObservation(
                workspace_id=seed.workspace_id,
                project_id=proj.project_id,
                crawl_id=proj.crawl_id,
                site_url_id=site_url_id,
                source_kind="link",
            )
        )
    await session.flush()


async def _sorted_project_ids(
    session: AsyncSession, *, proj: ProjectSeed
) -> list[uuid.UUID]:
    """Project site-url ids in the inventory's ``(normalized_url, id)`` order."""
    return list(
        (
            await session.scalars(
                select(SiteUrl.id)
                .where(SiteUrl.project_id == proj.project_id)
                .order_by(SiteUrl.normalized_url.asc(), SiteUrl.id.asc())
            )
        ).all()
    )


@pytest.mark.asyncio
async def test_bulk_select_first_n_matches_inventory_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 12, "with_active_crawl": True}],
        )
        proj = seed.projects[0]
        assert proj.crawl_id is not None
        await _admit_urls(session, seed=seed, proj=proj)
        await session.commit()

    async with session_factory() as session:
        expected = (await _sorted_project_ids(session, proj=proj))[:5]
        result = await bulk_select_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            crawl_id=proj.crawl_id,
            mode="first_n",
            count=5,
            expected_selection_version=0,
        )
        await session.commit()

    assert result.selection_version == 1
    assert set(result.active_ids) == set(expected)
    # Analyze tasks were enqueued into the active crawl for every addition.
    async with session_factory() as session:
        tasks = (
            await session.execute(
                select(func.count())
                .select_from(SiteCrawlTask)
                .where(
                    SiteCrawlTask.crawl_id == proj.crawl_id,
                    SiteCrawlTask.task_kind == TASK_KIND_ANALYZE,
                    SiteCrawlTask.status == TASK_STATUS_QUEUED,
                )
            )
        ).scalar_one()
    assert tasks == 5


@pytest.mark.asyncio
async def test_bulk_select_all_then_clear(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 7, "with_active_crawl": True}],
        )
        proj = seed.projects[0]
        assert proj.crawl_id is not None
        await _admit_urls(session, seed=seed, proj=proj)
        await session.commit()

    async with session_factory() as session:
        result = await bulk_select_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            crawl_id=proj.crawl_id,
            mode="all",
            expected_selection_version=0,
        )
        await session.commit()
    assert len(result.active_ids) == 7

    # ``none`` clears the selection (rows deactivated, never deleted).
    async with session_factory() as session:
        cleared = await bulk_select_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            crawl_id=proj.crawl_id,
            mode="none",
            expected_selection_version=result.selection_version,
        )
        await session.commit()
    assert cleared.active_ids == ()
    assert len(cleared.removed_ids) == 7

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(func.count())
                .select_from(MonitoredSiteUrl)
                .where(MonitoredSiteUrl.project_id == proj.project_id)
            )
        ).scalar_one()
    assert rows == 7  # preserved, only deactivated


@pytest.mark.asyncio
async def test_bulk_select_all_over_quota_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Starter monitored limit is 50 (pinned by the conftest fixture); 60
    # admitted URLs makes ``all`` an over-quota selection.
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 60, "with_active_crawl": True}],
        )
        proj = seed.projects[0]
        assert proj.crawl_id is not None
        await _admit_urls(session, seed=seed, proj=proj)
        await session.commit()

    async with session_factory() as session:
        with pytest.raises(QuotaExceededError) as excinfo:
            await bulk_select_monitored_set(
                session,
                workspace_id=seed.workspace_id,
                project_id=proj.project_id,
                crawl_id=proj.crawl_id,
                mode="all",
                expected_selection_version=0,
            )
        await session.rollback()
    assert excinfo.value.limit == 50

    # ``first_n`` at exactly the limit succeeds.
    async with session_factory() as session:
        result = await bulk_select_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            crawl_id=proj.crawl_id,
            mode="first_n",
            count=50,
            expected_selection_version=0,
        )
        await session.commit()
    assert len(result.active_ids) == 50


@pytest.mark.asyncio
async def test_bulk_select_validation_and_stale_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 3, "with_active_crawl": True}],
        )
        proj = seed.projects[0]
        assert proj.crawl_id is not None
        await _admit_urls(session, seed=seed, proj=proj)
        await session.commit()

    # first_n without a positive count is invalid.
    async with session_factory() as session:
        with pytest.raises(SelectionValidationError):
            await bulk_select_monitored_set(
                session,
                workspace_id=seed.workspace_id,
                project_id=proj.project_id,
                crawl_id=proj.crawl_id,
                mode="first_n",
                count=0,
                expected_selection_version=0,
            )
        await session.rollback()

    # A foreign/unknown crawl id is invalid.
    async with session_factory() as session:
        with pytest.raises(SelectionValidationError):
            await bulk_select_monitored_set(
                session,
                workspace_id=seed.workspace_id,
                project_id=proj.project_id,
                crawl_id=uuid.uuid4(),
                mode="all",
                expected_selection_version=0,
            )
        await session.rollback()

    # A stale version is rejected with the current version attached.
    async with session_factory() as session:
        with pytest.raises(StaleSelectionVersionError) as excinfo:
            await bulk_select_monitored_set(
                session,
                workspace_id=seed.workspace_id,
                project_id=proj.project_id,
                crawl_id=proj.crawl_id,
                mode="all",
                expected_selection_version=99,
            )
        assert excinfo.value.current_version == 0
        await session.rollback()


@pytest.mark.asyncio
async def test_bulk_select_scoped_to_crawl_admission(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``all`` only sweeps URLs ADMITTED to the given crawl, never the
    project's whole catalog (a later crawl cannot resurrect an earlier
    crawl's fuller inventory)."""
    async with session_factory() as session:
        seed = await _seed_workspace(
            session,
            projects=[{"name": "a", "url_count": 6, "with_active_crawl": True}],
        )
        proj = seed.projects[0]
        assert proj.crawl_id is not None
        # Admit only the first 4 URLs to the crawl; 2 remain unadmitted.
        await _admit_urls(session, seed=seed, proj=proj, only_first=4)
        await session.commit()

    async with session_factory() as session:
        result = await bulk_select_monitored_set(
            session,
            workspace_id=seed.workspace_id,
            project_id=proj.project_id,
            crawl_id=proj.crawl_id,
            mode="all",
            expected_selection_version=0,
        )
        await session.commit()
    assert set(result.active_ids) == set(proj.site_url_ids[:4])
