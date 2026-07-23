"""classify_referrals executor (A6): ReferralEvent -> ReferralClassification.

Proves the executor classifies ONLY the artifact's still-unclassified events
via the A4 pure classifier (fixed referrer -> utm -> user-agent priority),
writes exactly one provenance-stamped classification per event (rule_version
+ the config/analysis.py ANALYZER_VERSION — never the site_health one),
stays idempotent + immutable on re-run (ON CONFLICT DO NOTHING), enqueues
``analytics_snapshot_refresh`` for the artifact's sync-run window (C5 chain),
honors cooperative cancel at the batch boundary, and that the worker's
default dispatch table routes the kind to the real executor. Requires a real
Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.analysis import ANALYZER_VERSION
from app.core.config.analytics import (
    AI_REFERRAL_RULE_VERSION,
    AI_SOURCE_CHATGPT,
    AI_SOURCE_GEMINI,
    AI_SOURCE_OTHER,
    ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH,
    ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS,
    ERROR_EXECUTOR_NOT_WIRED,
)
from app.core.config.provider_catalog import ENGINE_CHATGPT, ENGINE_GEMINI
from app.core.config.site_health import ANALYZER_VERSION as SH_ANALYZER_VERSION
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCEEDED,
)
from app.domain.analytics import tasks as analytics_tasks
from app.domain.analytics.enqueue import enqueue_classify_referrals
from app.domain.analytics.tasks import TaskCancelledError, run_classify_referrals
from app.models.analytics import (
    AnalyticsTask,
    ReferralClassification,
    ReferralEvent,
)
from app.workers.analytics_worker import AnalyticsWorker
from tests.component.analytics_helpers import (
    seed_ga4_import,
    seed_referral_event,
    seed_workspace_project,
)

_DAY = datetime(2026, 7, 20, tzinfo=UTC)


def _classify_task(
    workspace_id: uuid.UUID, project_id: uuid.UUID, artifact_id: uuid.UUID
) -> AnalyticsTask:
    """Fabricate the claimed queue row the executor receives (not persisted)."""
    return AnalyticsTask(
        workspace_id=workspace_id,
        project_id=project_id,
        task_kind=ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS,
        payload={"import_artifact_id": str(artifact_id)},
        idempotency_key=uuid.uuid4().hex,
    )


async def _classifications(session: AsyncSession) -> list[ReferralClassification]:
    return list(
        (
            await session.scalars(
                select(ReferralClassification).order_by(ReferralClassification.id.asc())
            )
        ).all()
    )


async def _snapshot_refresh_tasks(session: AsyncSession) -> list[AnalyticsTask]:
    return list(
        (
            await session.scalars(
                select(AnalyticsTask).where(
                    AnalyticsTask.task_kind
                    == ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH
                )
            )
        ).all()
    )


async def _seed_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, dict[str, uuid.UUID]]:
    """Seed one artifact plus four events covering every signal tier."""
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
    async with session_factory() as session:
        seed = await seed_ga4_import(
            session, workspace_id=workspace_id, project_id=project_id
        )
        host_event = await seed_referral_event(
            session, seed=seed, occurred_at=_DAY, referrer_host="chatgpt.com"
        )
        utm_event = await seed_referral_event(
            session,
            seed=seed,
            occurred_at=_DAY,
            utm_source="Gemini",
            utm_medium="referral",
        )
        ua_event = await seed_referral_event(
            session, seed=seed, occurred_at=_DAY, user_agent="Mozilla/5.0 ChatGPT-User"
        )
        other_event = await seed_referral_event(
            session, seed=seed, occurred_at=_DAY, referrer_host="example.com"
        )
        await session.commit()
        ids = {
            "host": host_event.id,
            "utm": utm_event.id,
            "ua": ua_event.id,
            "other": other_event.id,
        }
    return workspace_id, project_id, seed.artifact_id, ids


@pytest.mark.asyncio
async def test_classify_writes_one_classification_per_unclassified_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    workspace_id, project_id, artifact_id, ids = await _seed_events(session_factory)

    await run_classify_referrals(
        session_factory, _classify_task(workspace_id, project_id, artifact_id)
    )

    async with session_factory() as session:
        rows = await _classifications(session)
        assert len(rows) == 4
        by_event = {row.referral_event_id: row for row in rows}

        host = by_event[ids["host"]]
        assert host.is_ai_referral is True
        assert host.ai_source == AI_SOURCE_CHATGPT
        # The audited logical-engine join key (invariant 10).
        assert host.logical_engine == ENGINE_CHATGPT
        assert host.matched_rule_id == "host-chatgpt-com"
        assert host.match_signal == "referrer"
        assert host.confidence == "exact"

        utm = by_event[ids["utm"]]
        assert utm.is_ai_referral is True
        assert utm.ai_source == AI_SOURCE_GEMINI
        assert utm.logical_engine == ENGINE_GEMINI
        assert utm.matched_rule_id == "utm-source-gemini"
        assert utm.match_signal == "utm"
        assert utm.confidence == "exact"

        ua = by_event[ids["ua"]]
        assert ua.is_ai_referral is True
        assert ua.ai_source == AI_SOURCE_CHATGPT
        assert ua.matched_rule_id == "ua-chatgpt-user"
        assert ua.match_signal == "user_agent"
        assert ua.confidence == "heuristic"

        # Unmatched: never a guess — other + empty match fields.
        other = by_event[ids["other"]]
        assert other.is_ai_referral is False
        assert other.ai_source == AI_SOURCE_OTHER
        assert other.logical_engine is None
        assert other.matched_rule_id == ""
        assert other.match_signal == ""
        assert other.confidence == ""

        # Provenance on every row (invariant 4).
        for row in rows:
            assert row.workspace_id == workspace_id
            assert row.project_id == project_id


@pytest.mark.asyncio
async def test_classify_stamps_rule_and_analyzer_versions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    workspace_id, project_id, artifact_id, _ids = await _seed_events(session_factory)

    await run_classify_referrals(
        session_factory, _classify_task(workspace_id, project_id, artifact_id)
    )

    async with session_factory() as session:
        rows = await _classifications(session)
        assert rows
        for row in rows:
            assert row.rule_version == AI_REFERRAL_RULE_VERSION
            # config/analysis.py's ANALYZER_VERSION — never the site_health
            # same-named constant (invariant 2).
            assert row.analyzer_version == ANALYZER_VERSION
            assert row.analyzer_version != SH_ANALYZER_VERSION


@pytest.mark.asyncio
async def test_classify_rerun_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    workspace_id, project_id, artifact_id, _ids = await _seed_events(session_factory)
    task = _classify_task(workspace_id, project_id, artifact_id)

    await run_classify_referrals(session_factory, task)
    async with session_factory() as session:
        first_ids = [row.id for row in await _classifications(session)]
        assert len(first_ids) == 4

    # A re-run (retry / duplicate chain fire) inserts nothing and never
    # mutates the written rows; the chained enqueue dedupes too.
    await run_classify_referrals(session_factory, task)
    async with session_factory() as session:
        assert [row.id for row in await _classifications(session)] == first_ids
        assert len(await _snapshot_refresh_tasks(session)) == 1


@pytest.mark.asyncio
async def test_classify_enqueues_snapshot_refresh_for_sync_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    workspace_id, project_id, artifact_id, _ids = await _seed_events(session_factory)

    await run_classify_referrals(
        session_factory, _classify_task(workspace_id, project_id, artifact_id)
    )

    async with session_factory() as session:
        refreshes = await _snapshot_refresh_tasks(session)
        assert len(refreshes) == 1
        refresh = refreshes[0]
        # The window resolves from the artifact's sync run (C5).
        assert refresh.payload == {
            "window_start": "2026-07-20",
            "window_end": "2026-07-22",
        }
        assert refresh.workspace_id == workspace_id
        assert refresh.project_id == project_id


@pytest.mark.asyncio
async def test_classify_honors_cooperative_cancel_at_batch_boundary(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, project_id, artifact_id, _ids = await _seed_events(session_factory)
    async with session_factory() as session:
        task_id = await enqueue_classify_referrals(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            import_artifact_id=artifact_id,
        )
        await session.commit()
    assert task_id is not None
    async with session_factory() as session:
        task = await session.get(AnalyticsTask, task_id)
    assert task is not None

    # One event per batch so the boundary lands between events.
    monkeypatch.setattr(analytics_tasks, "_CLASSIFY_BATCH_SIZE", 1)
    real_check = analytics_tasks._raise_if_task_terminal
    checks = 0

    async def _cancel_on_second_check(
        factory: async_sessionmaker[AsyncSession], row_id: uuid.UUID
    ) -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            # The row is cancelled mid-run (e.g. by an operator): the next
            # boundary check must stop the executor cooperatively.
            async with factory() as session:
                row = await session.get(AnalyticsTask, row_id)
                assert row is not None
                row.status = TASK_STATUS_CANCELLED
                await session.commit()
        await real_check(factory, row_id)

    monkeypatch.setattr(
        analytics_tasks, "_raise_if_task_terminal", _cancel_on_second_check
    )

    with pytest.raises(TaskCancelledError):
        await run_classify_referrals(session_factory, task)

    async with session_factory() as session:
        # The first batch committed; the remaining events stay unclassified
        # and the chain's next link was NOT enqueued.
        assert (
            await session.scalar(select(func.count(ReferralClassification.id)))
        ) == 1
        unclassified = await session.scalar(
            select(func.count(ReferralEvent.id)).where(
                ~select(ReferralClassification.id)
                .where(ReferralClassification.referral_event_id == ReferralEvent.id)
                .exists()
            )
        )
        assert unclassified == 3
        assert await _snapshot_refresh_tasks(session) == []


@pytest.mark.asyncio
async def test_classify_unknown_or_cross_workspace_artifact_fails(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
    async with session_factory() as session:
        seed = await seed_ga4_import(
            session, workspace_id=workspace_id, project_id=project_id
        )
        await session.commit()

    with pytest.raises(ValueError, match="unknown import artifact"):
        await run_classify_referrals(
            session_factory,
            _classify_task(workspace_id, project_id, uuid.uuid4()),
        )
    # An artifact from ANOTHER workspace is never classified (invariant 5).
    with pytest.raises(ValueError, match="unknown import artifact"):
        await run_classify_referrals(
            session_factory,
            _classify_task(uuid.uuid4(), project_id, seed.artifact_id),
        )


@pytest.mark.asyncio
async def test_worker_dispatch_runs_registered_classify_executor(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The worker's DEFAULT dispatch table routes classify_referrals (A6)."""
    workspace_id, project_id, artifact_id, _ids = await _seed_events(session_factory)
    async with session_factory() as session:
        task_id = await enqueue_classify_referrals(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            import_artifact_id=artifact_id,
        )
        await session.commit()
    assert task_id is not None

    worker = AnalyticsWorker(session_factory=session_factory, owner="analytics-test")
    # classify + the chained analytics_snapshot_refresh (stub until A8).
    assert await worker.run_until_idle() == 2

    async with session_factory() as session:
        row = await session.get(AnalyticsTask, task_id)
        assert row is not None
        assert row.status == TASK_STATUS_SUCCEEDED
        assert row.attempt_count == 1
        assert (
            await session.scalar(select(func.count(ReferralClassification.id)))
        ) == 4
        # The chain's third link exists; it fails loud as not-yet-wired
        # until A8 registers its executor.
        refreshes = await _snapshot_refresh_tasks(session)
        assert len(refreshes) == 1
        assert refreshes[0].status == TASK_STATUS_FAILED
        assert refreshes[0].error_code == ERROR_EXECUTOR_NOT_WIRED
