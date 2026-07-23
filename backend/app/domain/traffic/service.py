# Traffic snapshot refresh executor (A7): the ``traffic_snapshot_refresh``
# analytics-task kind.
#
# A pure projection over persisted ``IntegrationMetricRow`` rows (invariant
# 7 — NO provider I/O, no network): read the project's consumed-dataset
# metric rows for the payload window in bounded batches (cooperative cancel
# at every metric-row batch boundary, invariant 9), run the PURE projection
# math (``projection.py``) per configured granularity, then persist all
# granularities in ONE transaction — the ``TrafficSnapshot`` upsert on its
# unique ``(project_id, window_start, window_end, granularity)`` tuple
# (precedent: ``domain/site_health/discovery.py``), the ``site_url_id``
# resolution per page row, and the replace-then-insert of the page/query
# stat rows (delete-then-insert in the same tx).
#
# Idempotent (traffic.md section 4): recomputing from the same
# latest-``resync_seq`` rows rewrites the SAME snapshot row in place and
# replaces its stat rows, so a re-run (or a concurrent duplicate attempt,
# serialized by the upsert's row lock) never duplicates. Every written row
# stamps the formula/normalization versions and the
# ``source_metric_row_ids`` / ``source_artifact_ids`` provenance
# (invariant 4).
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.task_queue import TASK_TERMINAL_STATUSES
from app.core.config.traffic import (
    TRAFFIC_CONSUMED_DATASETS,
    TRAFFIC_FORMULA_VERSION,
    TRAFFIC_NORMALIZATION_VERSION,
    TRAFFIC_SNAPSHOT_GRANULARITIES,
)
from app.domain.analytics.tasks import TaskCancelledError
from app.domain.traffic.projection import (
    SnapshotProjection,
    TrafficMetricRowInput,
    build_traffic_projection,
)
from app.models.analytics import AnalyticsTask
from app.models.integrations import IntegrationMetricRow
from app.models.site_health import SiteUrl
from app.models.traffic import TrafficPageStat, TrafficQueryStat, TrafficSnapshot

# Bounded work per read batch: each batch is one cooperative-cancel boundary
# (the WRITE phase is a single transaction). Module constant (not config) —
# the same precedent as A6's ``_CLASSIFY_BATCH_SIZE``; tests monkeypatch it
# down to 1 to exercise the boundary per row.
_METRIC_ROW_BATCH_SIZE = 1000


async def _raise_if_task_terminal(
    session_factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID | None
) -> None:
    """Cooperative-cancel boundary check (invariant 9).

    Mirrors the A6 idiom (``domain/analytics/tasks.py``): re-read the queue
    row in a FRESH session (never the work session's possibly-stale identity
    map) and stop if it reached a terminal status. The refresh writes
    nothing before its single write transaction, so stopping here leaves no
    partial projection behind. A row that does not resolve (unpersisted
    direct-invocation fixture) has nothing to cancel against.
    """
    if task_id is None:  # unpersisted fixture row: nothing to cancel against
        return
    async with session_factory() as session:
        row = await session.get(AnalyticsTask, task_id)
        status = row.status if row is not None else None
    if status is not None and status in TASK_TERMINAL_STATUSES:
        raise TaskCancelledError(
            f"analytics task {task_id} reached terminal status {status!r}; "
            "stopping at the metric-row batch boundary"
        )


def _payload_window(task: AnalyticsTask) -> tuple[date, date]:
    payload = task.payload or {}
    raw_start = payload.get("window_start")
    raw_end = payload.get("window_end")
    if not raw_start or not raw_end:
        raise ValueError(
            "traffic_snapshot_refresh payload missing window_start/window_end"
        )
    window_start = date.fromisoformat(str(raw_start))
    window_end = date.fromisoformat(str(raw_end))
    if window_end < window_start:
        raise ValueError("traffic_snapshot_refresh window_end before window_start")
    return window_start, window_end


async def _metric_row_batch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    window_start: date,
    window_end: date,
    after_id: uuid.UUID | None,
    limit: int,
) -> list[IntegrationMetricRow]:
    """One keyset batch of the window's consumed-dataset metric rows.

    Workspace + project scoped (invariant 5); the id-keyset order keeps the
    scan stable across batches. Latest-``resync_seq`` selection is applied
    by the pure projection (one owner of the rule), not here.
    """
    stmt = (
        select(IntegrationMetricRow)
        .where(IntegrationMetricRow.workspace_id == workspace_id)
        .where(IntegrationMetricRow.project_id == project_id)
        .where(
            IntegrationMetricRow.dataset.in_(sorted(TRAFFIC_CONSUMED_DATASETS))
        )
        .where(IntegrationMetricRow.date >= window_start)
        .where(IntegrationMetricRow.date <= window_end)
        .order_by(IntegrationMetricRow.id.asc())
        .limit(limit)
    )
    if after_id is not None:
        stmt = stmt.where(IntegrationMetricRow.id > after_id)
    return list((await session.scalars(stmt)).all())


def _to_input(row: IntegrationMetricRow) -> TrafficMetricRowInput:
    return TrafficMetricRowInput(
        id=row.id,
        property_ref=row.property_ref,
        provider=row.provider,
        dataset=row.dataset,
        date=row.date,
        dimension_key=row.dimension_key,
        metrics=row.metrics,
        source_artifact_id=row.source_artifact_id,
        resync_seq=row.resync_seq,
    )


async def _upsert_snapshot(
    session: AsyncSession,
    *,
    task: AnalyticsTask,
    window_start: date,
    window_end: date,
    granularity: str,
    projection: SnapshotProjection,
) -> uuid.UUID:
    """The transactional upsert of the one current snapshot row.

    ``INSERT ... ON CONFLICT (project_id, window_start, window_end,
    granularity) DO UPDATE`` — concurrent refreshes serialize on the unique
    row and can never create a duplicate "current" snapshot (traffic.md
    section 3). The conflict target's workspace cannot drift (one project
    lives in one workspace), so only the projection payload + provenance +
    version stamps are updated.
    """
    stmt = (
        pg_insert(TrafficSnapshot)
        .values(
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            window_start=window_start,
            window_end=window_end,
            granularity=granularity,
            metrics=projection.metrics,
            source_metric_row_ids=projection.source_metric_row_ids,
            source_artifact_ids=projection.source_artifact_ids,
            formula_version=TRAFFIC_FORMULA_VERSION,
            normalization_version=TRAFFIC_NORMALIZATION_VERSION,
        )
        .on_conflict_do_update(
            index_elements=[
                "project_id",
                "window_start",
                "window_end",
                "granularity",
            ],
            set_={
                "metrics": projection.metrics,
                "source_metric_row_ids": projection.source_metric_row_ids,
                "source_artifact_ids": projection.source_artifact_ids,
                "formula_version": TRAFFIC_FORMULA_VERSION,
                "normalization_version": TRAFFIC_NORMALIZATION_VERSION,
            },
        )
        .returning(TrafficSnapshot.id)
    )
    snapshot_id = await session.scalar(stmt)
    if snapshot_id is None:  # RETURNING always yields the upserted row's id
        raise RuntimeError("traffic snapshot upsert returned no id")
    return snapshot_id


async def _resolve_site_url_ids(
    session: AsyncSession,
    *,
    task: AnalyticsTask,
    url_hashes: list[str],
) -> dict[str, uuid.UUID]:
    """Map page url_hashes to crawled ``SiteUrl`` ids (unmatched -> absent).

    The page join resolves by ``(project_id, url_hash)`` —
    ``uq_site_url_project_hash`` (traffic.md section 5); an unmatched page
    keeps ``site_url_id NULL`` and stays a valid measured page.
    """
    if not url_hashes:
        return {}
    stmt = (
        select(SiteUrl.url_hash, SiteUrl.id)
        .where(SiteUrl.workspace_id == task.workspace_id)
        .where(SiteUrl.project_id == task.project_id)
        .where(SiteUrl.url_hash.in_(url_hashes))
    )
    return {
        url_hash: site_url_id
        for url_hash, site_url_id in (await session.execute(stmt)).all()
    }


async def _replace_page_stats(
    session: AsyncSession,
    *,
    task: AnalyticsTask,
    snapshot_id: uuid.UUID,
    projection: SnapshotProjection,
) -> None:
    """Delete-then-insert the snapshot's page stat rows (same tx)."""
    await session.execute(
        delete(TrafficPageStat).where(TrafficPageStat.snapshot_id == snapshot_id)
    )
    if not projection.pages:
        return
    site_url_ids = await _resolve_site_url_ids(
        session, task=task, url_hashes=[page.url_hash for page in projection.pages]
    )
    await session.execute(
        pg_insert(TrafficPageStat).values(
            [
                {
                    "workspace_id": task.workspace_id,
                    "project_id": task.project_id,
                    "snapshot_id": snapshot_id,
                    "site_url_id": site_url_ids.get(page.url_hash),
                    "canonical_url": page.canonical_url,
                    "metrics": page.metrics,
                    "source_metric_row_ids": page.source_metric_row_ids,
                    "source_artifact_ids": page.source_artifact_ids,
                }
                for page in projection.pages
            ]
        )
    )


async def _replace_query_stats(
    session: AsyncSession,
    *,
    task: AnalyticsTask,
    snapshot_id: uuid.UUID,
    projection: SnapshotProjection,
) -> None:
    """Delete-then-insert the snapshot's query stat rows (same tx)."""
    await session.execute(
        delete(TrafficQueryStat).where(
            TrafficQueryStat.snapshot_id == snapshot_id
        )
    )
    if not projection.queries:
        return
    await session.execute(
        pg_insert(TrafficQueryStat).values(
            [
                {
                    "workspace_id": task.workspace_id,
                    "project_id": task.project_id,
                    "snapshot_id": snapshot_id,
                    "normalized_query": query.normalized_query,
                    "metrics": query.metrics,
                    "source_metric_row_ids": query.source_metric_row_ids,
                    "source_artifact_ids": query.source_artifact_ids,
                }
                for query in projection.queries
            ]
        )
    )


async def refresh_traffic_snapshot(
    session_factory: async_sessionmaker[AsyncSession], task: AnalyticsTask
) -> None:
    """``traffic_snapshot_refresh`` executor: rebuild one window's snapshots.

    Read phase: the window's consumed-dataset metric rows in bounded
    keyset batches, checking cooperative cancel at every batch boundary.
    Write phase: for each configured granularity
    (``TRAFFIC_SNAPSHOT_GRANULARITIES``) the pure projection is upserted and
    its page/query stat rows replaced — ALL of it in ONE transaction (one
    commit), so a refresh never leaves a half-written snapshot family.
    """
    if task.project_id is None:
        raise ValueError("traffic_snapshot_refresh task missing project_id")
    window_start, window_end = _payload_window(task)
    async with session_factory() as session:
        inputs: list[TrafficMetricRowInput] = []
        after_id: uuid.UUID | None = None
        while True:
            await _raise_if_task_terminal(session_factory, task.id)
            batch = await _metric_row_batch(
                session,
                workspace_id=task.workspace_id,
                project_id=task.project_id,
                window_start=window_start,
                window_end=window_end,
                after_id=after_id,
                limit=_METRIC_ROW_BATCH_SIZE,
            )
            if not batch:
                break
            inputs.extend(_to_input(row) for row in batch)
            after_id = batch[-1].id
            if len(batch) < _METRIC_ROW_BATCH_SIZE:
                break

        for granularity in sorted(TRAFFIC_SNAPSHOT_GRANULARITIES):
            projection = build_traffic_projection(
                rows=inputs,
                window_start=window_start,
                window_end=window_end,
                granularity=granularity,
            )
            snapshot_id = await _upsert_snapshot(
                session,
                task=task,
                window_start=window_start,
                window_end=window_end,
                granularity=granularity,
                projection=projection,
            )
            await _replace_page_stats(
                session, task=task, snapshot_id=snapshot_id, projection=projection
            )
            await _replace_query_stats(
                session, task=task, snapshot_id=snapshot_id, projection=projection
            )
        await session.commit()
