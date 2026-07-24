"""Traffic projection model constraints (A2).

Verifies the persistence contract the snapshot-refresh projection (A7)
depends on: exactly one ``TrafficSnapshot`` per ``(project, window,
granularity)`` (the upsert target), per-page/per-query uniqueness on
``snapshot_id``, the metrics + JSONB provenance columns (invariant 4), the
formula/normalization version stamps, same-workspace composite FK
enforcement, the ``site_url_id`` SET NULL join, and workspace/snapshot
cascades. Requires a real Postgres (FK + cascade semantics).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.traffic import (
    TRAFFIC_FORMULA_VERSION,
    TRAFFIC_NORMALIZATION_VERSION,
)
from app.models.project import Project
from app.models.site_health import SiteUrl
from app.models.traffic import TrafficPageStat, TrafficQueryStat, TrafficSnapshot
from app.models.workspace import Workspace

_WINDOW = (date(2026, 7, 1), date(2026, 7, 28))


async def _seed_project(
    session: AsyncSession, name: str = "Traffic WS"
) -> tuple[Workspace, Project]:
    workspace = Workspace(name=name)
    session.add(workspace)
    await session.flush()
    project = Project(workspace_id=workspace.id, name=f"{name} project")
    session.add(project)
    await session.flush()
    return workspace, project


def _snapshot(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    window: tuple[date, date] = _WINDOW,
    granularity: str = "day",
) -> TrafficSnapshot:
    return TrafficSnapshot(
        workspace_id=workspace_id,
        project_id=project_id,
        window_start=window[0],
        window_end=window[1],
        granularity=granularity,
        metrics={"totals": {"clicks": 12, "impressions": 340}},
        source_metric_row_ids=[str(uuid.uuid4())],
        source_artifact_ids=[str(uuid.uuid4())],
    )


def _page_stat(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    *,
    canonical_url: str = "https://example.com/pricing",
    site_url_id: uuid.UUID | None = None,
) -> TrafficPageStat:
    return TrafficPageStat(
        workspace_id=workspace_id,
        project_id=project_id,
        snapshot_id=snapshot_id,
        site_url_id=site_url_id,
        canonical_url=canonical_url,
        metrics={"clicks": 5, "impressions": 120, "sessions": 40},
        source_metric_row_ids=[str(uuid.uuid4())],
        source_artifact_ids=[str(uuid.uuid4())],
    )


def _query_stat(
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    *,
    normalized_query: str = "best running shoes",
) -> TrafficQueryStat:
    return TrafficQueryStat(
        workspace_id=workspace_id,
        project_id=project_id,
        snapshot_id=snapshot_id,
        normalized_query=normalized_query,
        metrics={"clicks": 7, "impressions": 220},
        source_metric_row_ids=[str(uuid.uuid4())],
        source_artifact_ids=[str(uuid.uuid4())],
    )


@pytest.mark.asyncio
async def test_snapshot_unique_per_project_window_granularity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace, project = await _seed_project(session)
        session.add(_snapshot(workspace.id, project.id))
        await session.commit()
        ws_id, project_id = workspace.id, project.id

    # Same (project, window, granularity): rejected — the refresh upsert
    # targets this tuple, so a plain insert must collide.
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_snapshot(ws_id, project_id))
            await session.commit()

    # A different granularity or a different window is a different snapshot.
    async with session_factory() as session:
        session.add(_snapshot(ws_id, project_id, granularity="week"))
        session.add(
            _snapshot(ws_id, project_id, window=(date(2026, 6, 1), date(2026, 6, 28)))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_snapshot_stamps_versions_and_provenance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace, project = await _seed_project(session)
        snapshot = _snapshot(workspace.id, project.id)
        session.add(snapshot)
        await session.commit()
        snapshot_id = snapshot.id

    async with session_factory() as session:
        persisted = await session.get(TrafficSnapshot, snapshot_id)
    assert persisted is not None
    # Version stamps default from config (invariant 4) and stay distinct.
    assert persisted.formula_version == TRAFFIC_FORMULA_VERSION
    assert persisted.normalization_version == TRAFFIC_NORMALIZATION_VERSION
    assert persisted.formula_version != persisted.normalization_version
    # Metrics + JSONB provenance ids round-trip.
    assert persisted.metrics == {"totals": {"clicks": 12, "impressions": 340}}
    assert len(persisted.source_metric_row_ids) == 1
    assert len(persisted.source_artifact_ids) == 1


@pytest.mark.asyncio
async def test_page_stat_unique_per_snapshot_url_with_provenance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace, project = await _seed_project(session)
        snapshot = _snapshot(workspace.id, project.id)
        session.add(snapshot)
        await session.flush()
        session.add(_page_stat(workspace.id, project.id, snapshot.id))
        await session.commit()
        ws_id, project_id, snapshot_id = workspace.id, project.id, snapshot.id

    # Same canonical_url under the same snapshot: rejected.
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_page_stat(ws_id, project_id, snapshot_id))
            await session.commit()

    # A different page key (or a different snapshot) is allowed.
    async with session_factory() as session:
        other_snapshot = _snapshot(ws_id, project_id, granularity="week")
        session.add(other_snapshot)
        await session.flush()
        session.add(
            _page_stat(
                ws_id,
                project_id,
                snapshot_id,
                canonical_url="https://example.com/about",
            )
        )
        session.add(_page_stat(ws_id, project_id, other_snapshot.id))
        await session.commit()

    async with session_factory() as session:
        stats = (
            await session.scalars(
                select(TrafficPageStat).where(
                    TrafficPageStat.snapshot_id == snapshot_id
                )
            )
        ).all()
    assert len(stats) == 2
    for stat in stats:
        assert stat.metrics
        assert len(stat.source_metric_row_ids) == 1
        assert len(stat.source_artifact_ids) == 1


@pytest.mark.asyncio
async def test_query_stat_unique_per_snapshot_query(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace, project = await _seed_project(session)
        snapshot = _snapshot(workspace.id, project.id)
        session.add(snapshot)
        await session.flush()
        session.add(_query_stat(workspace.id, project.id, snapshot.id))
        await session.commit()
        ws_id, project_id, snapshot_id = workspace.id, project.id, snapshot.id

    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_query_stat(ws_id, project_id, snapshot_id))
            await session.commit()

    async with session_factory() as session:
        session.add(
            _query_stat(ws_id, project_id, snapshot_id, normalized_query="trail shoes")
        )
        await session.commit()


@pytest.mark.asyncio
async def test_stat_composite_fk_rejects_cross_workspace_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace, project = await _seed_project(session)
        snapshot = _snapshot(workspace.id, project.id)
        session.add(snapshot)
        other_workspace, other_project = await _seed_project(session, "Other WS")
        await session.commit()
        snapshot_id = snapshot.id
        other_ws_id, other_project_id = other_workspace.id, other_project.id

    # A stat row cannot point at a snapshot owned by ANOTHER workspace
    # (tenant-consistency composite FK, invariant 5).
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_page_stat(other_ws_id, other_project_id, snapshot_id))
            await session.commit()
    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            session.add(_query_stat(other_ws_id, other_project_id, snapshot_id))
            await session.commit()


@pytest.mark.asyncio
async def test_site_url_join_is_set_null(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace, project = await _seed_project(session)
        site_url = SiteUrl(
            workspace_id=workspace.id,
            project_id=project.id,
            normalized_url="https://example.com/pricing",
            url_hash="a" * 64,
        )
        session.add(site_url)
        await session.flush()
        snapshot = _snapshot(workspace.id, project.id)
        session.add(snapshot)
        await session.flush()
        stat = _page_stat(
            workspace.id, project.id, snapshot.id, site_url_id=site_url.id
        )
        session.add(stat)
        await session.commit()
        site_url_id, stat_id = site_url.id, stat.id

    # Deleting the crawled SiteUrl does NOT delete the measured page stat —
    # unmatched pages stay valid (traffic.md section 5); the join nulls out.
    async with session_factory() as session:
        persisted_url = await session.get(SiteUrl, site_url_id)
        assert persisted_url is not None
        await session.delete(persisted_url)
        await session.commit()

    async with session_factory() as session:
        stat = await session.get(TrafficPageStat, stat_id)
    assert stat is not None
    assert stat.site_url_id is None
    assert stat.canonical_url == "https://example.com/pricing"


@pytest.mark.asyncio
async def test_snapshot_delete_cascades_stats(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace, project = await _seed_project(session)
        snapshot = _snapshot(workspace.id, project.id)
        session.add(snapshot)
        await session.flush()
        session.add(_page_stat(workspace.id, project.id, snapshot.id))
        session.add(_query_stat(workspace.id, project.id, snapshot.id))
        await session.commit()
        snapshot_id = snapshot.id

    async with session_factory() as session:
        persisted = await session.get(TrafficSnapshot, snapshot_id)
        assert persisted is not None
        await session.delete(persisted)
        await session.commit()

    async with session_factory() as session:
        page_stats = (
            await session.scalars(
                select(TrafficPageStat).where(
                    TrafficPageStat.snapshot_id == snapshot_id
                )
            )
        ).all()
        query_stats = (
            await session.scalars(
                select(TrafficQueryStat).where(
                    TrafficQueryStat.snapshot_id == snapshot_id
                )
            )
        ).all()
    assert page_stats == []
    assert query_stats == []


@pytest.mark.asyncio
async def test_workspace_delete_cascades_graph(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace, project = await _seed_project(session)
        snapshot = _snapshot(workspace.id, project.id)
        session.add(snapshot)
        await session.flush()
        session.add(_page_stat(workspace.id, project.id, snapshot.id))
        await session.commit()
        ws_id, snapshot_id = workspace.id, snapshot.id

    async with session_factory() as session:
        persisted = await session.get(Workspace, ws_id)
        assert persisted is not None
        await session.delete(persisted)
        await session.commit()

    async with session_factory() as session:
        assert await session.get(TrafficSnapshot, snapshot_id) is None
        remaining = (await session.scalars(select(TrafficPageStat))).all()
    assert remaining == []
