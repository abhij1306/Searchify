"""Referral ingest projection (A5): IntegrationMetricRow -> ReferralEvent.

Proves the ``ingest_referrals`` executor projects ONLY the artifact's
latest-``resync_seq`` GA4 referral-dimension rows, sanitizes BEFORE the
immutable write (no PII/fragments/credentials survive), stamps provenance
(import + source metric row + sanitize version), dedupes on re-run via
``ON CONFLICT DO NOTHING`` on ``(import_id, content_hash)``, and enqueues
``classify_referrals`` on completion (C5 chain) — plus that the worker's
default dispatch table routes the kind to the real executor. Requires a
real Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.analytics import (
    ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH,
    ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS,
    ANALYTICS_TASK_KIND_INGEST_REFERRALS,
    ERROR_EXECUTOR_NOT_WIRED,
    REFERRAL_RAW_ALLOWLIST,
    REFERRAL_SANITIZE_VERSION,
)
from app.core.config.integrations import (
    DATASET_GA4_CHANNEL_DAILY,
    DATASET_GA4_REFERRER_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    INTEGRATION_PROVIDER_GA4,
)
from app.core.config.task_queue import TASK_STATUS_FAILED, TASK_STATUS_SUCCEEDED
from app.domain.analytics.enqueue import enqueue_ingest_referrals
from app.domain.analytics.ingest import ingest_referrals
from app.models.analytics import (
    AnalyticsTask,
    ReferralClassification,
    ReferralEvent,
)
from app.models.integrations import (
    IntegrationConnection,
    IntegrationImportArtifact,
    IntegrationMetricRow,
)
from app.workers.analytics_worker import AnalyticsWorker
from tests.component.analytics_helpers import (
    seed_ga4_import,
    seed_metric_row,
    seed_workspace_project,
)

_GA4_DATE = "20260720"  # GA4 date dimension values arrive as YYYYMMDD.


def _ingest_task(
    workspace_id: uuid.UUID, project_id: uuid.UUID, artifact_id: uuid.UUID
) -> AnalyticsTask:
    """Fabricate the claimed queue row the executor receives (not persisted)."""
    return AnalyticsTask(
        workspace_id=workspace_id,
        project_id=project_id,
        task_kind=ANALYTICS_TASK_KIND_INGEST_REFERRALS,
        payload={"import_artifact_id": str(artifact_id)},
        idempotency_key=uuid.uuid4().hex,
    )


async def _events(session: AsyncSession) -> list[ReferralEvent]:
    return list(
        (
            await session.scalars(
                select(ReferralEvent).order_by(
                    ReferralEvent.occurred_at.asc(), ReferralEvent.id.asc()
                )
            )
        ).all()
    )


async def _classify_tasks(session: AsyncSession) -> list[AnalyticsTask]:
    return list(
        (
            await session.scalars(
                select(AnalyticsTask).where(
                    AnalyticsTask.task_kind == ANALYTICS_TASK_KIND_CLASSIFY_REFERRALS
                )
            )
        ).all()
    )


@pytest.mark.asyncio
async def test_ingest_sanitizes_referrer_rows_before_write(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
    async with session_factory() as session:
        seed = await seed_ga4_import(
            session, workspace_id=workspace_id, project_id=project_id
        )
        pii_row = await seed_metric_row(
            session,
            seed=seed,
            row_date=date(2026, 7, 20),
            dimension_values=[
                "https://user:pass@ChatGPT.com/c/abc"
                "?utm_source=chatgpt.com&email=bob@example.com#frag",
                _GA4_DATE,
            ],
            metrics={"sessions": 5, "engagedSessions": 3, "conversions": 1},
        )
        tracker_row = await seed_metric_row(
            session,
            seed=seed,
            row_date=date(2026, 7, 21),
            dimension_values=[
                "https://perplexity.ai/search?q=shoes&ref=nav&fbclid=track",
                "20260721",
            ],
            metrics={"sessions": 2},
        )
        # A " | " inside a free-form fullReferrer value must survive the
        # right-peeling unpack (only the trailing date dim is peeled).
        separator_row = await seed_metric_row(
            session,
            seed=seed,
            row_date=date(2026, 7, 21),
            dimension_values=[
                "https://example.com/weird | path?utm_campaign=launch",
                "20260721",
            ],
        )
        await session.commit()
        pii_row_id, tracker_row_id, separator_row_id = (
            pii_row.id,
            tracker_row.id,
            separator_row.id,
        )

    await ingest_referrals(
        session_factory, _ingest_task(workspace_id, project_id, seed.artifact_id)
    )

    async with session_factory() as session:
        events = await _events(session)
        assert len(events) == 3
        by_row = {event.source_metric_row_id: event for event in events}

        pii = by_row[pii_row_id]
        # Sanitize-before-write: credentials, the PII query param and the
        # fragment never persist; the allowlisted utm_* param survives.
        assert pii.referrer_url == "https://chatgpt.com/c/abc?utm_source=chatgpt.com"
        assert pii.referrer_host == "chatgpt.com"
        for leaked in ("user:pass", "bob@example.com", "email", "#"):
            assert leaked not in pii.referrer_url
        # The persisted raw payload is allowlisted + redacted.
        assert set(pii.raw or {}) <= REFERRAL_RAW_ALLOWLIST
        assert pii.raw["dataset"] == DATASET_GA4_REFERRER_DAILY
        assert pii.raw["referrer_host"] == "chatgpt.com"
        assert "email" not in pii.raw
        # Provenance on every derived row (invariant 4).
        assert pii.import_id == seed.artifact_id
        assert pii.source_metric_row_id == pii_row_id
        assert pii.sanitize_version == REFERRAL_SANITIZE_VERSION
        assert pii.source == INTEGRATION_PROVIDER_GA4
        assert pii.workspace_id == workspace_id
        assert pii.project_id == project_id
        assert pii.occurred_at == datetime(2026, 7, 20, tzinfo=UTC)
        assert len(pii.content_hash) == 64
        # GA4 aggregate rows carry no UA/session identity.
        assert pii.user_agent == ""
        assert pii.session_id_hash == ""

        tracker = by_row[tracker_row_id]
        # Only the allowlisted ref param survives; q/fbclid are dropped.
        assert tracker.referrer_url == "https://perplexity.ai/search?ref=nav"
        assert tracker.referrer_host == "perplexity.ai"

        separator = by_row[separator_row_id]
        assert (
            separator.referrer_url
            == "https://example.com/weird | path?utm_campaign=launch"
        )
        assert separator.referrer_host == "example.com"

        # The chain's next link is enqueued for the artifact (C5).
        classify = await _classify_tasks(session)
        assert len(classify) == 1
        assert classify[0].payload == {"import_artifact_id": str(seed.artifact_id)}
        assert classify[0].workspace_id == workspace_id
        assert classify[0].project_id == project_id


@pytest.mark.asyncio
async def test_ingest_maps_source_medium_rows_to_utm_signals(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
    async with session_factory() as session:
        seed = await seed_ga4_import(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            dataset=DATASET_GA4_SOURCE_MEDIUM_DAILY,
        )
        row = await seed_metric_row(
            session,
            seed=seed,
            row_date=date(2026, 7, 20),
            dimension_values=["ChatGPT.com", "referral", _GA4_DATE],
            metrics={"sessions": 7},
        )
        await session.commit()
        row_id = row.id

    await ingest_referrals(
        session_factory, _ingest_task(workspace_id, project_id, seed.artifact_id)
    )

    async with session_factory() as session:
        events = await _events(session)
        assert len(events) == 1
        event = events[0]
        # sessionSource/sessionMedium map to the UTM signal columns
        # (provider case preserved for traceability; the classifier
        # casefolds at match time). No referrer URL exists for this dataset.
        assert event.utm_source == "ChatGPT.com"
        assert event.utm_medium == "referral"
        assert event.referrer_url == ""
        assert event.referrer_host == ""
        assert event.source_metric_row_id == row_id
        assert event.raw["utm_source"] == "ChatGPT.com"
        assert event.raw["utm_medium"] == "referral"
        assert event.raw["dimension_key"] == "ChatGPT.com | referral | 20260720"


@pytest.mark.asyncio
async def test_ingest_dedupes_on_rerun(
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
        )
        await session.commit()

    task = _ingest_task(workspace_id, project_id, seed.artifact_id)
    await ingest_referrals(session_factory, task)
    async with session_factory() as session:
        first_ids = [event.id for event in await _events(session)]
        assert len(first_ids) == 1

    # A re-run (retry / duplicate chain fire) inserts nothing and never
    # mutates the immutable event; the chained enqueue dedupes too.
    await ingest_referrals(session_factory, task)
    async with session_factory() as session:
        assert [event.id for event in await _events(session)] == first_ids
        assert len(await _classify_tasks(session)) == 1


@pytest.mark.asyncio
async def test_ingest_ignores_non_referral_datasets(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
    async with session_factory() as session:
        seed = await seed_ga4_import(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            dataset=DATASET_GA4_CHANNEL_DAILY,
        )
        await seed_metric_row(
            session,
            seed=seed,
            row_date=date(2026, 7, 20),
            dimension_values=["Organic Search", _GA4_DATE],
        )
        await session.commit()

    await ingest_referrals(
        session_factory, _ingest_task(workspace_id, project_id, seed.artifact_id)
    )

    async with session_factory() as session:
        assert await _events(session) == []
        # The chain still continues: classification runs (and finds no
        # events) rather than the chain silently dying here.
        assert len(await _classify_tasks(session)) == 1


@pytest.mark.asyncio
async def test_ingest_reads_only_latest_resync_seq(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
    async with session_factory() as session:
        seed_v0 = await seed_ga4_import(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            resync_seq=0,
        )
        superseded = await seed_metric_row(
            session,
            seed=seed_v0,
            row_date=date(2026, 7, 20),
            dimension_values=["https://chatgpt.com/a", _GA4_DATE],
            resync_seq=0,
        )
        still_current = await seed_metric_row(
            session,
            seed=seed_v0,
            row_date=date(2026, 7, 20),
            dimension_values=["https://perplexity.ai/y", _GA4_DATE],
            resync_seq=0,
        )
        await session.commit()
        connection = await session.get(IntegrationConnection, seed_v0.connection_id)
        # A late-data re-sync lands a NEW artifact at a higher resync_seq
        # revising one of the two identities.
        seed_v1 = await seed_ga4_import(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            resync_seq=1,
            connection=connection,
        )
        revised = await seed_metric_row(
            session,
            seed=seed_v1,
            row_date=date(2026, 7, 20),
            dimension_values=["https://chatgpt.com/a", _GA4_DATE],
            resync_seq=1,
        )
        await session.commit()
        superseded_id, still_current_id, revised_id = (
            superseded.id,
            still_current.id,
            revised.id,
        )

    # The STALE artifact's late-running task ingests only its still-current
    # identity; the superseded row is skipped (consumers read latest only).
    await ingest_referrals(
        session_factory, _ingest_task(workspace_id, project_id, seed_v0.artifact_id)
    )
    async with session_factory() as session:
        events = await _events(session)
        assert [event.source_metric_row_id for event in events] == [still_current_id]
        assert superseded_id not in {event.source_metric_row_id for event in events}

    # The fresh artifact's task ingests the revised row normally.
    await ingest_referrals(
        session_factory, _ingest_task(workspace_id, project_id, seed_v1.artifact_id)
    )
    async with session_factory() as session:
        events = await _events(session)
        assert {event.source_metric_row_id for event in events} == {
            still_current_id,
            revised_id,
        }
        assert {event.import_id for event in events} == {
            seed_v0.artifact_id,
            seed_v1.artifact_id,
        }


@pytest.mark.asyncio
async def test_ingest_unknown_or_cross_workspace_artifact_fails(
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
        await ingest_referrals(
            session_factory,
            _ingest_task(workspace_id, project_id, uuid.uuid4()),
        )
    # An artifact from ANOTHER workspace is never projected (invariant 5).
    with pytest.raises(ValueError, match="unknown import artifact"):
        await ingest_referrals(
            session_factory,
            _ingest_task(uuid.uuid4(), project_id, seed.artifact_id),
        )


@pytest.mark.asyncio
async def test_referral_event_fk_actions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Metric-row delete -> SET NULL link; import delete -> events cascade."""
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
    async with session_factory() as session:
        seed = await seed_ga4_import(
            session, workspace_id=workspace_id, project_id=project_id
        )
        row = await seed_metric_row(
            session,
            seed=seed,
            row_date=date(2026, 7, 20),
            dimension_values=["https://chatgpt.com/c/abc", _GA4_DATE],
        )
        await session.commit()
        row_id = row.id

    await ingest_referrals(
        session_factory, _ingest_task(workspace_id, project_id, seed.artifact_id)
    )

    # The source metric row's deletion must not delete the event: the
    # optional provenance link goes NULL.
    async with session_factory() as session:
        await session.delete(await session.get(IntegrationMetricRow, row_id))
        await session.commit()
    async with session_factory() as session:
        events = await _events(session)
        assert len(events) == 1
        assert events[0].source_metric_row_id is None
        event_id = events[0].id

    # Deleting the source ingest batch deletes its events (retention
    # contract, llm-analytics.md section 3).
    async with session_factory() as session:
        await session.delete(
            await session.get(IntegrationImportArtifact, seed.artifact_id)
        )
        await session.commit()
    async with session_factory() as session:
        assert await session.get(ReferralEvent, event_id) is None


@pytest.mark.asyncio
async def test_worker_dispatch_runs_registered_ingest_executor(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The worker's DEFAULT dispatch table routes ingest_referrals (A5)."""
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
        )
        await session.commit()
    async with session_factory() as session:
        task_id = await enqueue_ingest_referrals(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            import_artifact_id=seed.artifact_id,
        )
        await session.commit()
    assert task_id is not None

    worker = AnalyticsWorker(session_factory=session_factory, owner="analytics-test")
    # ingest + the chained classify + the chained snapshot-refresh stub.
    assert await worker.run_until_idle() == 3

    async with session_factory() as session:
        ingest_row = await session.get(AnalyticsTask, task_id)
        assert ingest_row is not None
        assert ingest_row.status == TASK_STATUS_SUCCEEDED
        assert ingest_row.attempt_count == 1
        assert await session.scalar(select(func.count(ReferralEvent.id))) == 1
        # The chained classify task ran the real executor (A6) and wrote the
        # event's classification.
        classify = await _classify_tasks(session)
        assert len(classify) == 1
        assert classify[0].status == TASK_STATUS_SUCCEEDED
        assert (
            await session.scalar(select(func.count(ReferralClassification.id)))
        ) == 1
        # The chain continues: analytics_snapshot_refresh is enqueued; it
        # fails loud as not-yet-wired until A8 registers its executor.
        refresh = list(
            (
                await session.scalars(
                    select(AnalyticsTask).where(
                        AnalyticsTask.task_kind
                        == ANALYTICS_TASK_KIND_ANALYTICS_SNAPSHOT_REFRESH
                    )
                )
            ).all()
        )
        assert len(refresh) == 1
        assert refresh[0].status == TASK_STATUS_FAILED
        assert refresh[0].error_code == ERROR_EXECUTOR_NOT_WIRED
