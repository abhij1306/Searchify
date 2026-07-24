# Analytics enqueue service (invariant 8): the C5 post-sync hook plus the
# per-kind enqueue helpers with deterministic idempotency keys.
#
# ``enqueue_post_sync_projections`` is the hook the integrations worker calls
# after derivation (contract C5): per import artifact it enqueues
# ``ingest_referrals`` — the first task of the referral chain — plus one
# ``traffic_snapshot_refresh`` per distinct affected sync window. The later
# chain links (``classify_referrals`` on ingest completion, then
# ``analytics_snapshot_refresh`` on classify completion) are enqueued by the
# executors themselves via the per-kind helpers below (A5/A6/A8).
#
# Every helper builds a DETERMINISTIC idempotency key from the kind plus the
# project/artifact/window identity, and inserts ``ON CONFLICT DO NOTHING`` on
# the unique ``idempotency_key`` — a re-enqueue of the same logical task is a
# no-op (returns ``None``), never a duplicate queue row (invariant 8).
from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.analytics import (
    ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH,
    ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS,
    ANALYTICS_TASK_KIND_INGEST_REFERRALS,
    ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP,
    ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH,
    analytics_settings,
)
from app.core.config.task_queue import TASK_STATUS_QUEUED
from app.models.analytics import AnalyticsTask
from app.models.integrations import IntegrationImportArtifact, IntegrationSyncRun
from app.models.project import Project


def _idempotency_key(task_kind: str, *parts: object) -> str:
    """Deterministic queue-row identity: kind + project/artifact/window.

    Fits the 160-char column un-hashed (kind <= 26 chars + UUIDs / ISO
    dates), keeping the key debuggable (site_health ``_task_idempotency_key``
    precedent).
    """
    return ":".join(("analytics", task_kind, *(str(part) for part in parts)))


async def _enqueue_task(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID | None,
    task_kind: str,
    payload: dict,
    idempotency_key: str,
    priority: int = 0,
) -> uuid.UUID | None:
    """Enqueue one queue row conflict-safely (returns id, or None if it existed).

    The unique ``idempotency_key`` plus ``ON CONFLICT DO NOTHING`` mean a
    re-enqueue of the same logical task never double-enqueues.
    """
    stmt = (
        pg_insert(AnalyticsTask)
        .values(
            workspace_id=workspace_id,
            project_id=project_id,
            task_kind=task_kind,
            payload=payload,
            idempotency_key=idempotency_key,
            status=TASK_STATUS_QUEUED,
            priority=priority,
            max_attempts=analytics_settings.task_max_attempts,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(AnalyticsTask.id)
    )
    return await session.scalar(stmt)


# --- Per-kind helpers (the worker executors chain through these) -------------


async def enqueue_ingest_referrals(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    import_artifact_id: uuid.UUID,
    priority: int = 0,
) -> uuid.UUID | None:
    """Enqueue the referral chain's first task for one import artifact (A5)."""
    return await _enqueue_task(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        task_kind=ANALYTICS_TASK_KIND_INGEST_REFERRALS,
        payload={"import_artifact_id": str(import_artifact_id)},
        idempotency_key=_idempotency_key(
            ANALYTICS_TASK_KIND_INGEST_REFERRALS, project_id, import_artifact_id
        ),
        priority=priority,
    )


async def enqueue_classify_referrals(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    import_artifact_id: uuid.UUID,
    priority: int = 0,
) -> uuid.UUID | None:
    """Enqueue classification of the events one artifact ingested (A6)."""
    return await _enqueue_task(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        task_kind=ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS,
        payload={"import_artifact_id": str(import_artifact_id)},
        idempotency_key=_idempotency_key(
            ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS, project_id, import_artifact_id
        ),
        priority=priority,
    )


async def _enqueue_window_snapshot_refresh(
    session: AsyncSession,
    *,
    task_kind: str,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    window_start: date,
    window_end: date,
    resync_seq: int,
    priority: int = 0,
) -> uuid.UUID | None:
    """The shared body of the two window snapshot-refresh enqueues (A7/A8).

    The payload is window-level; the executor expands the configured
    snapshot granularities. The idempotency key carries the triggering
    data revision (``resync_seq``) so a re-sync of an already-projected
    window re-fires the refresh while a same-revision duplicate still
    dedupes.
    """
    return await _enqueue_task(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        task_kind=task_kind,
        payload={
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        },
        idempotency_key=_idempotency_key(
            task_kind, project_id, window_start, window_end, resync_seq
        ),
        priority=priority,
    )


async def enqueue_traffic_snapshot_refresh(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    window_start: date,
    window_end: date,
    resync_seq: int,
    priority: int = 0,
) -> uuid.UUID | None:
    """Enqueue a rebuild of the Traffic snapshot rows for one window (A7).

    The executor expands ``TRAFFIC_SNAPSHOT_GRANULARITIES``; the
    revision-keyed dedupe rule is documented on
    ``_enqueue_window_snapshot_refresh``.
    """
    return await _enqueue_window_snapshot_refresh(
        session,
        task_kind=ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH,
        workspace_id=workspace_id,
        project_id=project_id,
        window_start=window_start,
        window_end=window_end,
        resync_seq=resync_seq,
        priority=priority,
    )


async def enqueue_analytics_snapshot_refresh(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    window_start: date,
    window_end: date,
    resync_seq: int,
    priority: int = 0,
) -> uuid.UUID | None:
    """Enqueue a rebuild of the LLM-Analytics snapshot for one window (A8).

    The executor expands ``ANALYTICS_SNAPSHOT_GRANULARITIES``; the
    revision-keyed dedupe rule is documented on
    ``_enqueue_window_snapshot_refresh``.
    """
    return await _enqueue_window_snapshot_refresh(
        session,
        task_kind=ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH,
        workspace_id=workspace_id,
        project_id=project_id,
        window_start=window_start,
        window_end=window_end,
        resync_seq=resync_seq,
        priority=priority,
    )


async def enqueue_referral_retention_sweep(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    sweep_key: str,
    priority: int = 0,
) -> uuid.UUID | None:
    """Enqueue one workspace-scoped retention sweep (A6).

    ``sweep_key`` is the caller-chosen period token (e.g. an ISO date) that
    makes the sweep deterministic per period — at most one sweep row per
    ``(workspace_id, sweep_key)`` is ever queued.
    """
    return await _enqueue_task(
        session,
        workspace_id=workspace_id,
        project_id=None,
        task_kind=ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP,
        payload={"sweep_key": sweep_key},
        idempotency_key=_idempotency_key(
            ANALYTICS_TASK_KIND_REFERRAL_RETENTION_SWEEP, workspace_id, sweep_key
        ),
        priority=priority,
    )


# --- C5 post-sync hook (called by the integrations worker after derivation) --


async def enqueue_post_sync_projections(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    import_artifact_ids: Iterable[uuid.UUID],
) -> list[uuid.UUID]:
    """Enqueue the analytics projection chain for freshly derived artifacts.

    Per import artifact: one ``ingest_referrals`` task (the chain's first
    link; the executors chain ``classify_referrals`` and
    ``analytics_snapshot_refresh`` on completion). Plus one
    ``traffic_snapshot_refresh`` per distinct affected (sync window,
    ``resync_seq``) revision (C5) — the refresh idempotency keys carry the
    triggering run's data revision so a re-sync of an already-projected
    window re-fires the refresh instead of deduping away.

    Artifact ids are resolved scoped to the project's workspace — an id that
    does not resolve there (unknown or cross-workspace) is skipped, never
    enqueued (invariant 5). Returns the ids of the newly inserted queue rows
    (deduplicated re-calls return fewer or no ids).
    """
    artifact_ids = list(dict.fromkeys(import_artifact_ids))
    if not artifact_ids:
        return []

    project = await session.get(Project, project_id)
    if project is None:
        raise ValueError(f"unknown project: {project_id}")
    workspace_id = project.workspace_id

    rows = (
        await session.execute(
            select(
                IntegrationImportArtifact.id,
                IntegrationSyncRun.window_start,
                IntegrationSyncRun.window_end,
                IntegrationSyncRun.resync_seq,
            )
            .join(
                IntegrationSyncRun,
                IntegrationImportArtifact.sync_run_id == IntegrationSyncRun.id,
            )
            .where(IntegrationImportArtifact.workspace_id == workspace_id)
            .where(IntegrationImportArtifact.id.in_(artifact_ids))
        )
    ).all()
    resolved_ids = {row.id for row in rows}
    # One refresh per DISTINCT affected (window, data revision), deduped
    # in first-seen order of the returned rows (the SELECT has no ORDER
    # BY; a hook call normally carries one run's artifacts — one window
    # at one resync_seq — so ordering is moot in practice).
    revisions = list(
        dict.fromkeys(
            (row.window_start, row.window_end, row.resync_seq) for row in rows
        )
    )

    enqueued: list[uuid.UUID] = []
    for artifact_id in artifact_ids:
        if artifact_id not in resolved_ids:
            continue
        task_id = await enqueue_ingest_referrals(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            import_artifact_id=artifact_id,
        )
        if task_id is not None:
            enqueued.append(task_id)
    for window_start, window_end, resync_seq in revisions:
        task_id = await enqueue_traffic_snapshot_refresh(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            window_start=window_start,
            window_end=window_end,
            resync_seq=resync_seq,
        )
        if task_id is not None:
            enqueued.append(task_id)
    return enqueued
