# Analytics task executors (A6): ``classify_referrals`` + the workspace-scoped
# ``referral_retention_sweep``.
#
# ``run_classify_referrals`` is the C5 chain's second link: for the payload's
# ``import_artifact_id`` (landed by ``ingest_referrals``) it reads the
# artifact's still-UNCLASSIFIED ``ReferralEvent`` rows, runs the A4 PURE
# deterministic classifier over each event's persisted signals, and writes
# exactly one provenance-stamped ``ReferralClassification`` per event via
# ``INSERT ... ON CONFLICT DO NOTHING`` on ``referral_event_id`` — a re-run
# (or a concurrent duplicate attempt) is a dedup no-op, never a mutation
# (invariant 3; single writer, invariant 4 version stamps). On completion it
# enqueues ``analytics_snapshot_refresh`` for the artifact's sync-run window
# at the run's ``resync_seq`` (the chain's third link; the window + revision
# are resolved artifact -> sync run, the same resolution the C5 hook uses).
#
# ``run_referral_retention_sweep`` hard-deletes referral data past
# ``REFERRAL_RETENTION_DAYS`` (llm-analytics.md section 3): classifications
# first (FK order), then their events, in bounded committed batches.
#
# Both executors are pure projections over persisted rows (NO network I/O,
# invariant 7) and check cooperative cancel at the batch boundary (invariant
# 9): between committed batches the claimed row's status is re-read exactly
# like the worker's dispatch-boundary check — a row that turned terminal
# (cancelled/failed) stops the run promptly with already-committed batches
# left intact (every write is idempotent, so a later attempt resumes safely).
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ``ANALYZER_VERSION`` is OWNED by config/analysis.py ("b6-analysis-1") and
# reused for the analytics provenance stamp (invariant 2) — NEVER the
# same-named constant in config/site_health.py ("sh-analyzer-1").
from app.core.config.analysis import ANALYZER_VERSION
from app.core.config.analytics import (
    AI_REFERRAL_RULE_VERSION,
    AI_SOURCE_OTHER,
    REFERRAL_RETENTION_DAYS,
)
from app.core.config.task_queue import TASK_TERMINAL_STATUSES
from app.domain.analytics.classification import classify_referral_signals
from app.domain.analytics.enqueue import enqueue_analytics_snapshot_refresh
from app.models.analytics import (
    AnalyticsTask,
    ReferralClassification,
    ReferralEvent,
)
from app.models.integrations import IntegrationImportArtifact, IntegrationSyncRun

# Bounded work per commit: each batch is one committed transaction and one
# cooperative-cancel boundary. Module constants (not config) — the sweep has
# no settings knob yet; tests monkeypatch these down to 1 to exercise the
# boundary per event/row.
_CLASSIFY_BATCH_SIZE = 500
_RETENTION_DELETE_BATCH_SIZE = 500


class TaskCancelledError(RuntimeError):
    """The claimed queue row turned terminal mid-run; stop cooperatively.

    The worker's ``_finalize`` re-checks owner + status under its lock and
    writes nothing for an already-terminal row (single-writer, invariant 3),
    so raising here never flips a cancelled row to failed.
    """


async def raise_if_task_terminal(
    session_factory: async_sessionmaker[AsyncSession],
    task_id: uuid.UUID | None,
    *,
    boundary: str = "batch",
) -> None:
    """Cooperative-cancel boundary check (invariant 9).

    Single owner of the batch-boundary idiom every analytics/traffic
    executor uses (the sibling executor modules keep a thin label adapter
    under their own module-private name so their patch point + message
    boundary stay local). Mirrors the worker's dispatch-boundary check
    (``_execute``): re-read the queue row in a FRESH session (never the
    work session's possibly-stale identity map) and stop if it reached a
    terminal status. A row that does not resolve (unpersisted
    direct-invocation fixture) has nothing to cancel against and the run
    continues.
    """
    if task_id is None:  # unpersisted fixture row: nothing to cancel against
        return
    async with session_factory() as session:
        row = await session.get(AnalyticsTask, task_id)
        status = row.status if row is not None else None
    if status is not None and status in TASK_TERMINAL_STATUSES:
        raise TaskCancelledError(
            f"analytics task {task_id} reached terminal status {status!r}; "
            f"stopping at the {boundary} boundary"
        )


def payload_window(task: AnalyticsTask, *, kind: str) -> tuple[date, date]:
    """Parse + validate a refresh task's ``window_start``/``window_end``.

    ``kind`` is the task-kind token used in the error messages (the owning
    executor's name); every windowed executor parses the same payload
    shape, so the parse lives here exactly once.
    """
    payload = task.payload or {}
    raw_start = payload.get("window_start")
    raw_end = payload.get("window_end")
    if not raw_start or not raw_end:
        raise ValueError(f"{kind} payload missing window_start/window_end")
    window_start = date.fromisoformat(str(raw_start))
    window_end = date.fromisoformat(str(raw_end))
    if window_end < window_start:
        raise ValueError(f"{kind} window_end before window_start")
    return window_start, window_end


def payload_artifact_id(task: AnalyticsTask, *, kind: str) -> uuid.UUID:
    """Parse a task payload's ``import_artifact_id`` (fail loud when absent)."""
    raw = (task.payload or {}).get("import_artifact_id")
    if not raw:
        raise ValueError(f"{kind} payload missing import_artifact_id")
    return uuid.UUID(str(raw))


# --- classify_referrals -------------------------------------------------------


def _classification_values(event: ReferralEvent) -> dict[str, Any]:
    """Build the ``ReferralClassification`` column values for one event.

    The classifier is pure (A4): it reads only the event's persisted,
    already-sanitized signals. An unmatched event records
    ``is_ai_referral=false, ai_source=other`` with empty match fields — the
    classifier never guesses a source. The CALLER stamps the provenance
    versions (invariant 4): the config rule table + the analyzer.
    """
    match = classify_referral_signals(
        referrer_host=event.referrer_host,
        utm_source=event.utm_source,
        utm_medium=event.utm_medium,
        user_agent=event.user_agent,
    )
    return {
        # The event's own identity — the composite (workspace, event) FK
        # requires the classification's workspace to equal the event's.
        "workspace_id": event.workspace_id,
        "project_id": event.project_id,
        "referral_event_id": event.id,
        "is_ai_referral": match is not None,
        "ai_source": match.ai_source if match is not None else AI_SOURCE_OTHER,
        "logical_engine": match.logical_engine if match is not None else None,
        "matched_rule_id": match.matched_rule_id if match is not None else "",
        "match_signal": match.match_signal if match is not None else "",
        "confidence": match.confidence if match is not None else "",
        "rule_version": AI_REFERRAL_RULE_VERSION,
        "analyzer_version": ANALYZER_VERSION,
    }


async def _unclassified_events(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    artifact_id: uuid.UUID,
    limit: int,
) -> list[ReferralEvent]:
    """One batch of the artifact's events that have NO classification yet.

    Re-queried after every committed batch, so events classified by an
    earlier batch (or a concurrent duplicate attempt) drop out naturally;
    the deterministic order keeps re-runs stable (invariant 9).
    """
    classified = (
        select(ReferralClassification.id)
        .where(ReferralClassification.referral_event_id == ReferralEvent.id)
        .exists()
    )
    stmt = (
        select(ReferralEvent)
        .where(ReferralEvent.workspace_id == workspace_id)
        .where(ReferralEvent.import_id == artifact_id)
        .where(~classified)
        .order_by(ReferralEvent.occurred_at.asc(), ReferralEvent.id.asc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def run_classify_referrals(
    session_factory: async_sessionmaker[AsyncSession], task: AnalyticsTask
) -> None:
    """``classify_referrals`` executor: classify one artifact's events.

    Committed batches of ``_CLASSIFY_BATCH_SIZE``: read the artifact's
    unclassified events, classify each via the A4 pure classifier, insert
    conflict-safe (idempotent, never mutates — invariant 3). Then enqueue
    ``analytics_snapshot_refresh`` for the artifact's sync-run window (C5
    chain). Cooperative cancel is honored at every batch boundary.
    """
    if task.project_id is None:
        raise ValueError("classify_referrals task missing project_id")
    artifact_id = payload_artifact_id(task, kind="classify_referrals")
    async with session_factory() as session:
        artifact = await session.get(IntegrationImportArtifact, artifact_id)
        # Never classify events for an artifact outside the claimed task's
        # workspace (invariant 5); an unknown id fails the attempt loud.
        if artifact is None or artifact.workspace_id != task.workspace_id:
            raise ValueError(f"unknown import artifact: {artifact_id}")
        # Resolve the refresh window from the artifact's sync run — the same
        # artifact -> sync-run resolution the C5 hook performs.
        sync_run = await session.get(IntegrationSyncRun, artifact.sync_run_id)
        if sync_run is None:  # broken provenance — fail loud, never guess
            raise ValueError(f"unknown sync run: {artifact.sync_run_id}")
        # Bind to locals BEFORE any commit (post-commit attribute access on
        # an expired ORM object would re-load).
        window_start, window_end = sync_run.window_start, sync_run.window_end
        resync_seq = sync_run.resync_seq

        while True:
            await raise_if_task_terminal(session_factory, task.id)
            events = await _unclassified_events(
                session,
                workspace_id=task.workspace_id,
                artifact_id=artifact.id,
                limit=_CLASSIFY_BATCH_SIZE,
            )
            if not events:
                break
            stmt = (
                pg_insert(ReferralClassification)
                .values([_classification_values(event) for event in events])
                .on_conflict_do_nothing(index_elements=["referral_event_id"])
            )
            await session.execute(stmt)
            await session.commit()

        await enqueue_analytics_snapshot_refresh(
            session,
            workspace_id=task.workspace_id,
            project_id=task.project_id,
            window_start=window_start,
            window_end=window_end,
            resync_seq=resync_seq,
        )
        await session.commit()


# --- referral_retention_sweep ---------------------------------------------------


async def _delete_expired_batch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    cutoff: datetime,
    limit: int,
) -> int:
    """Delete one bounded batch of expired events + their classifications.

    Classifications are deleted FIRST (FK order), then the events; the batch
    commits as one transaction. Returns the number of events deleted (0 when
    nothing is past the horizon). The deterministic order keeps the batch
    composition stable across re-runs (invariant 9).
    """
    event_ids = list(
        (
            await session.scalars(
                select(ReferralEvent.id)
                .where(ReferralEvent.workspace_id == workspace_id)
                .where(ReferralEvent.occurred_at < cutoff)
                .order_by(ReferralEvent.occurred_at.asc(), ReferralEvent.id.asc())
                .limit(limit)
            )
        ).all()
    )
    if not event_ids:
        return 0
    await session.execute(
        delete(ReferralClassification).where(
            ReferralClassification.referral_event_id.in_(event_ids)
        )
    )
    await session.execute(delete(ReferralEvent).where(ReferralEvent.id.in_(event_ids)))
    await session.commit()
    return len(event_ids)


async def run_referral_retention_sweep(
    session_factory: async_sessionmaker[AsyncSession], task: AnalyticsTask
) -> None:
    """``referral_retention_sweep`` executor: hard-delete expired referrals.

    Workspace-scoped (the task carries no project): every ``ReferralEvent``
    in the task's workspace whose ``occurred_at`` is past
    ``REFERRAL_RETENTION_DAYS`` is deleted together with its
    ``ReferralClassification`` (llm-analytics.md section 3), in bounded
    committed batches with cooperative cancel at each batch boundary
    (invariant 9). Idempotent: a re-run simply finds less (then nothing) to
    delete.
    """
    if not (task.payload or {}).get("sweep_key"):
        raise ValueError("referral_retention_sweep payload missing sweep_key")
    # One fixed horizon per run: the cutoff never drifts mid-sweep.
    cutoff = datetime.now(UTC) - timedelta(days=REFERRAL_RETENTION_DAYS)
    async with session_factory() as session:
        while True:
            await raise_if_task_terminal(session_factory, task.id)
            deleted = await _delete_expired_batch(
                session,
                workspace_id=task.workspace_id,
                cutoff=cutoff,
                limit=_RETENTION_DELETE_BATCH_SIZE,
            )
            if deleted == 0:
                break
