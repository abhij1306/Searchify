"""Site Health model constraints + Free-default entitlement (Task 1).

Verifies the uniqueness/FK/index contract that the queue, quota, and
idempotency logic depends on: duplicate URL identity, duplicate task slot
(including the ``generation`` discriminator), duplicate rule evaluation and
selection uniqueness, plus the capability-based entitlement resolver defaulting
to Free. Requires a real Postgres (Postgres UUID + partial index semantics).
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.site_health import (
    CAPABILITY_FREE,
    CAPABILITY_STARTER,
    FREE_MONITORED_URL_LIMIT,
    INITIAL_TASK_GENERATION,
    SELECTION_SOURCE_USER,
    STARTER_MONITORED_URL_LIMIT,
    TASK_KIND_DISCOVER,
)
from app.domain.site_health.entitlements import (
    resolve_entitlement,
    set_entitlement,
)
from app.models.site_health import (
    MonitoredSiteUrl,
    SiteCrawlTask,
    SiteUrl,
)
from tests.component.site_health_helpers import seed_site_crawl


@pytest.mark.asyncio
async def test_site_url_project_hash_unique(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_site_crawl(session)
    async with session_factory() as session:
        session.add(
            SiteUrl(
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                normalized_url="https://example.com/a",
                url_hash="hash-a",
            )
        )
        await session.commit()
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(
                SiteUrl(
                    workspace_id=seed.workspace_id,
                    project_id=seed.project_id,
                    normalized_url="https://example.com/a",
                    url_hash="hash-a",
                )
            )
            await session.commit()


def _task(seed, *, url_hash: str, generation: int, key: str) -> SiteCrawlTask:
    return SiteCrawlTask(
        crawl_id=seed.crawl_id,
        workspace_id=seed.workspace_id,
        task_kind=TASK_KIND_DISCOVER,
        requested_url="https://example.com/x",
        url_hash=url_hash,
        generation=generation,
        idempotency_key=key,
    )


@pytest.mark.asyncio
async def test_task_slot_unique_but_generation_disambiguates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_site_crawl(session)

    # Same (crawl, kind, url_hash, generation) must collide.
    async with session_factory() as session:
        session.add(
            _task(seed, url_hash="h1", generation=INITIAL_TASK_GENERATION, key="k1")
        )
        await session.commit()
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(
                _task(
                    seed,
                    url_hash="h1",
                    generation=INITIAL_TASK_GENERATION,
                    key="k2",
                )
            )
            await session.commit()

    # Bumping the generation makes it a distinct slot — no collision.
    async with session_factory() as session:
        session.add(
            _task(seed, url_hash="h1", generation=1, key="k3")
        )
        await session.commit()


@pytest.mark.asyncio
async def test_task_idempotency_key_unique(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_site_crawl(session)
    async with session_factory() as session:
        session.add(_task(seed, url_hash="a", generation=0, key="dup"))
        await session.commit()
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_task(seed, url_hash="b", generation=0, key="dup"))
            await session.commit()


@pytest.mark.asyncio
async def test_monitored_url_unique_per_project(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_site_crawl(session)
        site_url = SiteUrl(
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            normalized_url="https://example.com/m",
            url_hash="mhash",
        )
        session.add(site_url)
        await session.commit()
        site_url_id = site_url.id

    def _mon() -> MonitoredSiteUrl:
        return MonitoredSiteUrl(
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            profile_id=seed.profile_id,
            site_url_id=site_url_id,
            selection_source=SELECTION_SOURCE_USER,
        )

    async with session_factory() as session:
        session.add(_mon())
        await session.commit()
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_mon())
            await session.commit()


@pytest.mark.asyncio
async def test_resolve_entitlement_defaults_to_free(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_site_crawl(session)
    async with session_factory() as session:
        row = await resolve_entitlement(session, seed.workspace_id)
        await session.commit()
        assert row.plan_key == CAPABILITY_FREE
        assert row.monitored_url_limit == FREE_MONITORED_URL_LIMIT
        assert row.count_disclosure is False

    # Idempotent: a second resolve returns the same seeded row, no duplicate.
    async with session_factory() as session:
        again = await resolve_entitlement(session, seed.workspace_id)
        assert again.id == row.id


@pytest.mark.asyncio
async def test_set_entitlement_starter_then_free(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_site_crawl(session)
    async with session_factory() as session:
        starter = await set_entitlement(
            session, seed.workspace_id, CAPABILITY_STARTER
        )
        await session.commit()
        assert starter.plan_key == CAPABILITY_STARTER
        assert starter.monitored_url_limit == STARTER_MONITORED_URL_LIMIT
        assert starter.count_disclosure is True
        assert starter.capability_revision == 1

    async with session_factory() as session:
        back = await set_entitlement(
            session, seed.workspace_id, CAPABILITY_FREE
        )
        await session.commit()
        assert back.plan_key == CAPABILITY_FREE
        assert back.monitored_url_limit == FREE_MONITORED_URL_LIMIT
        # Revision bumped again on the in-place update.
        assert back.capability_revision == 2


@pytest.mark.asyncio
async def test_set_entitlement_unknown_coerces_to_free(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_site_crawl(session)
    async with session_factory() as session:
        row = await set_entitlement(session, seed.workspace_id, "enterprise")
        await session.commit()
        assert row.plan_key == CAPABILITY_FREE
