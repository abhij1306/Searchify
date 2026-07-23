"""C5 post-sync chain lifecycle (A6): hook -> ingest -> classify -> refresh.

THE one lifecycle test for the referral chain: drive
``enqueue_post_sync_projections`` (the hook the integrations worker WILL
call after derivation in I9) over a freshly derived artifact, drain the
analytics worker, and assert the full chain ran end to end — ingest
projected the referral events, classify classified them, and both
window-level refreshes were enqueued (``analytics_snapshot_refresh`` by the
classify executor, ``traffic_snapshot_refresh`` by the hook itself). The
``analytics_snapshot_refresh`` kind lands in A8, so its row terminal-fails
``executor_not_wired`` here; ``traffic_snapshot_refresh`` is wired (A7) and
succeeds. The chain LINKS (enqueue + routing) are what this test pins.
Requires a real Postgres.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.analysis import ANALYZER_VERSION
from app.core.config.analytics import (
    AI_REFERRAL_RULE_VERSION,
    AI_SOURCE_CHATGPT,
    AI_SOURCE_OTHER,
    ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH,
    ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS,
    ANALYTICS_TASK_KIND_INGEST_REFERRALS,
    ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH,
    ERROR_EXECUTOR_NOT_WIRED,
)
from app.core.config.task_queue import TASK_STATUS_FAILED, TASK_STATUS_SUCCEEDED
from app.domain.analytics.enqueue import enqueue_post_sync_projections
from app.models.analytics import (
    AnalyticsTask,
    ReferralClassification,
    ReferralEvent,
)
from app.workers.analytics_worker import AnalyticsWorker
from tests.component.analytics_helpers import (
    DEFAULT_WINDOW,
    seed_ga4_import,
    seed_metric_row,
    seed_workspace_project,
)

_GA4_DATE = "20260720"  # GA4 date dimension values arrive as YYYYMMDD.


async def _tasks_by_kind(
    session: AsyncSession, task_kind: str
) -> list[AnalyticsTask]:
    return list(
        (
            await session.scalars(
                select(AnalyticsTask).where(AnalyticsTask.task_kind == task_kind)
            )
        ).all()
    )


@pytest.mark.asyncio
async def test_post_sync_chain_runs_ingest_classify_and_enqueues_refreshes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
    async with session_factory() as session:
        seed = await seed_ga4_import(
            session, workspace_id=workspace_id, project_id=project_id
        )
        await seed_metric_row(
            session,
            seed=seed,
            row_date=date(2026, 7, 20),
            dimension_values=["https://chatgpt.com/c/abc", _GA4_DATE],
            metrics={"sessions": 5},
        )
        await seed_metric_row(
            session,
            seed=seed,
            row_date=date(2026, 7, 21),
            dimension_values=["https://example.com/blog", "20260721"],
            metrics={"sessions": 3},
        )
        await session.commit()

    # The C5 hook (what the integrations worker calls after derivation, I9).
    async with session_factory() as session:
        enqueued = await enqueue_post_sync_projections(
            session,
            project_id=project_id,
            import_artifact_ids=[seed.artifact_id],
        )
        await session.commit()
    # ingest_referrals (chain link 1) + traffic_snapshot_refresh (hook).
    assert len(enqueued) == 2

    worker = AnalyticsWorker(session_factory=session_factory, owner="chain-test")
    # ingest -> classify -> analytics_snapshot_refresh + the hook's
    # traffic_snapshot_refresh (analytics_snapshot_refresh fails not-wired
    # until A8 — terminal, no retry burn; traffic_snapshot_refresh is wired).
    assert await worker.run_until_idle() == 4

    async with session_factory() as session:
        # Link 1: ingest ran and projected both referral events.
        ingest = await _tasks_by_kind(session, ANALYTICS_TASK_KIND_INGEST_REFERRALS)
        assert len(ingest) == 1
        assert ingest[0].status == TASK_STATUS_SUCCEEDED
        assert ingest[0].payload == {"import_artifact_id": str(seed.artifact_id)}
        events = list((await session.scalars(select(ReferralEvent))).all())
        assert len(events) == 2

        # Link 2: classify ran and wrote one classification per event.
        classify = await _tasks_by_kind(
            session, ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS
        )
        assert len(classify) == 1
        assert classify[0].status == TASK_STATUS_SUCCEEDED
        rows = list((await session.scalars(select(ReferralClassification))).all())
        assert len(rows) == 2
        by_event = {row.referral_event_id: row for row in rows}
        chatgpt_event = next(
            event for event in events if event.referrer_host == "chatgpt.com"
        )
        other_event = next(
            event for event in events if event.referrer_host == "example.com"
        )
        chatgpt = by_event[chatgpt_event.id]
        assert chatgpt.is_ai_referral is True
        assert chatgpt.ai_source == AI_SOURCE_CHATGPT
        assert chatgpt.rule_version == AI_REFERRAL_RULE_VERSION
        assert chatgpt.analyzer_version == ANALYZER_VERSION
        other = by_event[other_event.id]
        assert other.is_ai_referral is False
        assert other.ai_source == AI_SOURCE_OTHER

        # Link 3: the classify executor enqueued analytics_snapshot_refresh
        # for the artifact's sync-run window.
        analytics_refresh = await _tasks_by_kind(
            session, ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH
        )
        assert len(analytics_refresh) == 1
        assert analytics_refresh[0].payload == {
            "window_start": DEFAULT_WINDOW[0].isoformat(),
            "window_end": DEFAULT_WINDOW[1].isoformat(),
        }
        assert analytics_refresh[0].workspace_id == workspace_id
        assert analytics_refresh[0].project_id == project_id
        # Not wired until A8: claimed, terminal-failed loud, never retried.
        assert analytics_refresh[0].status == TASK_STATUS_FAILED
        assert analytics_refresh[0].error_code == ERROR_EXECUTOR_NOT_WIRED

        # The hook enqueued traffic_snapshot_refresh for the same window.
        traffic_refresh = await _tasks_by_kind(
            session, ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH
        )
        assert len(traffic_refresh) == 1
        assert traffic_refresh[0].payload == {
            "window_start": DEFAULT_WINDOW[0].isoformat(),
            "window_end": DEFAULT_WINDOW[1].isoformat(),
        }
        # Wired in A7: the executor ran (the seeded rows are all
        # ga4_referrer_daily, which Traffic does not consume — an empty
        # projection is still a successful refresh).
        assert traffic_refresh[0].status == TASK_STATUS_SUCCEEDED

    # The hook is dedup-safe: re-firing it for the same artifact enqueues
    # nothing (deterministic idempotency keys, invariant 8).
    async with session_factory() as session:
        assert (
            await enqueue_post_sync_projections(
                session,
                project_id=project_id,
                import_artifact_ids=[seed.artifact_id],
            )
        ) == []
        await session.commit()
