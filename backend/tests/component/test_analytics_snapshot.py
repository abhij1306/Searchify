"""``analytics_snapshot_refresh`` executor (A8): transactional upsert
idempotency, the persisted projection metrics + provenance ids, latest-
``resync_seq`` selection, cooperative cancel at the classification batch
boundary, and worker routing for the kind.

Seeds the integrations-owned import graph + referral chain rows
(metric row -> event -> classification) and the audit-side rows
(``MetricSnapshot`` / ``ResponseAnalysis``) via the shared analytics
helpers — the executor is a pure projection over those persisted rows (no
provider I/O, invariant 7). Requires a real Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.analysis import ANALYZER_VERSION, SCORING_RULE_VERSION
from app.core.config.analytics import (
    AI_SOURCE_CHATGPT,
    AI_SOURCE_GEMINI,
    AI_SOURCE_OTHER,
    AI_SOURCE_PERPLEXITY,
    CONFIDENCE_EXACT,
    CORRELATION_STATE_INSUFFICIENT_DATA,
    MATCH_SIGNAL_REFERRER,
)
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_SUCCEEDED,
)
from app.domain.analytics import snapshot as snapshot_module
from app.domain.analytics.enqueue import enqueue_analytics_snapshot_refresh
from app.domain.analytics.snapshot import refresh_analytics_snapshot
from app.domain.analytics.tasks import TaskCancelledError
from app.models.analytics import AnalyticsSnapshot, AnalyticsTask
from app.workers.analytics_worker import AnalyticsWorker
from tests.component.analytics_helpers import (
    DEFAULT_WINDOW,
    seed_ga4_import,
    seed_metric_row,
    seed_referral_classification,
    seed_referral_event,
    seed_theme_analysis,
    seed_visibility_snapshot,
    seed_workspace_project,
)

WINDOW = DEFAULT_WINDOW  # 2026-07-20 -> 2026-07-22
_GA4_DATE = "20260720"  # GA4 date dimension values arrive as YYYYMMDD.


def _occurred(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


async def _seed_referral_chain(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict[str, object]:
    """Seed metric rows -> referral events -> classifications for the window.

    Three AI referrals (chatgpt/gemini/perplexity) + one non-AI referral,
    each event linked to its source metric row exactly like the ingest
    projection writes it.
    """
    seed = await seed_ga4_import(
        session, workspace_id=workspace_id, project_id=project_id
    )
    rows = {}
    for key, row_date, referrer, sessions in (
        ("chatgpt", date(2026, 7, 20), "https://chatgpt.com/c/abc", 4),
        ("gemini", date(2026, 7, 21), "https://gemini.google.com/app", 1),
        ("other", date(2026, 7, 21), "https://example.com/blog", 6),
        ("perplexity", date(2026, 7, 22), "https://perplexity.ai/s/1", 2),
    ):
        rows[key] = await seed_metric_row(
            session,
            seed=seed,
            row_date=row_date,
            dimension_values=[referrer, row_date.strftime("%Y%m%d")],
            metrics={"sessions": sessions},
        )
    classifications = {}
    for key, is_ai, ai_source in (
        ("chatgpt", True, AI_SOURCE_CHATGPT),
        ("gemini", True, AI_SOURCE_GEMINI),
        ("other", False, AI_SOURCE_OTHER),
        ("perplexity", True, AI_SOURCE_PERPLEXITY),
    ):
        row = rows[key]
        event = await seed_referral_event(
            session,
            seed=seed,
            occurred_at=_occurred(row.date),
            referrer_url=f"https://{ai_source}.example/",
            source_metric_row_id=row.id,
        )
        classifications[key] = await seed_referral_classification(
            session,
            event=event,
            is_ai_referral=is_ai,
            ai_source=ai_source,
            logical_engine=(
                ai_source
                if ai_source in {AI_SOURCE_CHATGPT, AI_SOURCE_GEMINI}
                else None
            ),
            matched_rule_id="host-rule" if is_ai else "",
            match_signal=MATCH_SIGNAL_REFERRER if is_ai else "",
            confidence=CONFIDENCE_EXACT if is_ai else "",
        )
    await session.commit()
    return {
        "artifact_id": seed.artifact_id,
        "classification_ids": [c.id for c in classifications.values()],
    }


async def _seed_audit_side(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict[str, object]:
    """Seed two dashboard-status audits: snapshots + theme analyses."""
    snapshot_a = await seed_visibility_snapshot(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        completed_at=datetime(2026, 7, 20, 12, tzinfo=UTC),
        visibility_score=50.0,
        total_completed=2,
        per_engine={"chatgpt": 0.5},
    )
    snapshot_b = await seed_visibility_snapshot(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        completed_at=datetime(2026, 7, 21, 12, tzinfo=UTC),
        visibility_score=25.0,
        total_completed=4,
        per_engine={"chatgpt": 0.25, "gemini": 0.75},
    )
    await seed_theme_analysis(
        session,
        workspace_id=workspace_id,
        audit_id=snapshot_a.audit_id,
        prompt_index=0,
        theme="pricing",
        intent="comparison",
        brand_mentioned=True,
        competitors_mentioned=["Globex"],
    )
    await seed_theme_analysis(
        session,
        workspace_id=workspace_id,
        audit_id=snapshot_a.audit_id,
        prompt_index=1,
        theme="pricing",
        intent="comparison",
        brand_mentioned=False,
    )
    await seed_theme_analysis(
        session,
        workspace_id=workspace_id,
        audit_id=snapshot_b.audit_id,
        prompt_index=0,
        theme="onboarding",
        intent="",
        brand_mentioned=True,
    )
    await session.commit()
    return {"snapshot_ids": [snapshot_a.id, snapshot_b.id]}


async def _enqueue_and_fetch(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
) -> AnalyticsTask:
    async with session_factory() as session:
        task_id = await enqueue_analytics_snapshot_refresh(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            resync_seq=0,
        )
        await session.commit()
    assert task_id is not None
    async with session_factory() as session:
        task = await session.get(AnalyticsTask, task_id)
    assert task is not None
    return task


async def _snapshots_by_granularity(
    session: AsyncSession,
) -> dict[str, AnalyticsSnapshot]:
    rows = list((await session.scalars(select(AnalyticsSnapshot))).all())
    return {row.granularity: row for row in rows}


@pytest.mark.asyncio
async def test_refresh_builds_snapshots_metrics_and_provenance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
        ids = await _seed_referral_chain(
            session, workspace_id=workspace_id, project_id=project_id
        )
        audit_side = await _seed_audit_side(
            session, workspace_id=workspace_id, project_id=project_id
        )
    task = await _enqueue_and_fetch(
        session_factory, workspace_id=workspace_id, project_id=project_id
    )

    await refresh_analytics_snapshot(session_factory, task)

    async with session_factory() as session:
        # One current snapshot per configured granularity, version-stamped.
        snapshots = await _snapshots_by_granularity(session)
        assert set(snapshots) == {"day", "week", "month"}
        day = snapshots["day"]
        assert day.workspace_id == workspace_id
        assert day.project_id == project_id
        assert day.window_start == WINDOW[0]
        assert day.window_end == WINDOW[1]
        assert day.analyzer_version == ANALYZER_VERSION
        assert day.formula_version == SCORING_RULE_VERSION

        metrics = day.metrics
        # Referral volume: measured AI sessions per day bucket.
        volume = metrics["referral_volume"]
        assert [p["date"] for p in volume] == [
            "2026-07-20",
            "2026-07-21",
            "2026-07-22",
        ]
        assert [p["value"] for p in volume] == [4, 1, 2]
        # Share over the SAME row set's total (AI + non-AI) sessions.
        share = metrics["referral_share"]
        assert share[0]["value"] == pytest.approx(1.0)  # 4 / 4
        assert share[1]["value"] == pytest.approx(1 / 7)  # 1 / (1 + 6)
        assert share[2]["value"] == pytest.approx(1.0)  # 2 / 2

        # Per-source breakdown: AI sources only, sessions desc then name.
        assert metrics["sources"] == [
            {"ai_source": AI_SOURCE_CHATGPT, "sessions": 4, "share": 4 / 13},
            {"ai_source": AI_SOURCE_PERPLEXITY, "sessions": 2, "share": 2 / 13},
            {"ai_source": AI_SOURCE_GEMINI, "sessions": 1, "share": 1 / 13},
        ]

        # Per-engine visibility from the persisted MetricSnapshot rows.
        engines = {
            row["logical_engine"]: row["series"] for row in metrics["engine_visibility"]
        }
        assert set(engines) == {"chatgpt", "gemini"}
        assert [p["value"] for p in engines["chatgpt"]] == [50.0, 25.0, None]
        assert [p["value"] for p in engines["gemini"]] == [None, 75.0, None]

        # Day-aligned correlation: only 2 days have BOTH series (< the 8
        # minimum) -> insufficient_data, never a fabricated coefficient.
        assert metrics["correlation"] == {
            "state": CORRELATION_STATE_INSUFFICIENT_DATA,
            "coefficient": None,
            "sample_size": 2,
        }

        # Theme rollup over the frozen (theme, intent) axes.
        themes = {(row["theme"], row["intent"]): row for row in metrics["themes"]}
        pricing = themes[("pricing", "comparison")]
        assert pricing["total_completed"] == 2
        assert pricing["brand_mention_rate"] == pytest.approx(0.5)
        assert pricing["visibility_score"] == pytest.approx(50.0)
        assert pricing["share_of_voice"] == pytest.approx(0.5)
        onboarding = themes[("onboarding", "")]
        assert onboarding["total_completed"] == 1
        assert onboarding["share_of_voice"] == pytest.approx(1.0)

        # Provenance (invariant 4): every folded classification + snapshot.
        assert set(day.source_classification_ids) == {
            str(classification_id) for classification_id in ids["classification_ids"]
        }
        assert set(day.source_snapshot_ids) == {
            str(snapshot_id) for snapshot_id in audit_side["snapshot_ids"]
        }

        # Week + month collapse to one bucket labelled at the window start.
        for granularity in ("week", "month"):
            other = snapshots[granularity]
            assert [p["date"] for p in other.metrics["referral_volume"]] == [
                "2026-07-20"
            ]
            assert other.metrics["referral_volume"][0]["value"] == 7
            # The day-aligned correlation is granularity-independent.
            assert other.metrics["correlation"] == metrics["correlation"]


@pytest.mark.asyncio
async def test_refresh_upsert_is_idempotent_across_reruns(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
        await _seed_referral_chain(
            session, workspace_id=workspace_id, project_id=project_id
        )
        await _seed_audit_side(
            session, workspace_id=workspace_id, project_id=project_id
        )
    task = await _enqueue_and_fetch(
        session_factory, workspace_id=workspace_id, project_id=project_id
    )

    await refresh_analytics_snapshot(session_factory, task)
    async with session_factory() as session:
        first = await _snapshots_by_granularity(session)
        first_payloads = {
            granularity: (snapshot.metrics, snapshot.source_classification_ids)
            for granularity, snapshot in first.items()
        }

    # A second run of the same task recomputes from the same persisted
    # rows: the SAME snapshot rows are updated in place, never duplicated.
    await refresh_analytics_snapshot(session_factory, task)

    async with session_factory() as session:
        second = await _snapshots_by_granularity(session)
        assert set(second) == {"day", "week", "month"}
        assert (await session.scalar(select(func.count(AnalyticsSnapshot.id)))) == 3
        for granularity, snapshot in second.items():
            assert snapshot.id == first[granularity].id
            assert (snapshot.metrics, snapshot.source_classification_ids) == (
                first_payloads[granularity]
            )


@pytest.mark.asyncio
async def test_refresh_reads_only_the_latest_resync_seq(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
        seed = await seed_ga4_import(
            session, workspace_id=workspace_id, project_id=project_id
        )
        stale = await seed_metric_row(
            session,
            seed=seed,
            row_date=date(2026, 7, 20),
            dimension_values=["https://chatgpt.com/c/abc", _GA4_DATE],
            metrics={"sessions": 5},
            resync_seq=0,
        )
        fresh = await seed_metric_row(
            session,
            seed=seed,
            row_date=date(2026, 7, 20),
            dimension_values=["https://chatgpt.com/c/abc", _GA4_DATE],
            metrics={"sessions": 9},
            resync_seq=1,
        )
        stale_event = await seed_referral_event(
            session,
            seed=seed,
            occurred_at=_occurred(date(2026, 7, 20)),
            source_metric_row_id=stale.id,
        )
        stale_classification = await seed_referral_classification(
            session,
            event=stale_event,
            is_ai_referral=True,
            ai_source=AI_SOURCE_CHATGPT,
        )
        fresh_event = await seed_referral_event(
            session,
            seed=seed,
            occurred_at=_occurred(date(2026, 7, 20)),
            source_metric_row_id=fresh.id,
        )
        fresh_classification = await seed_referral_classification(
            session,
            event=fresh_event,
            is_ai_referral=True,
            ai_source=AI_SOURCE_CHATGPT,
        )
        await session.commit()
    task = await _enqueue_and_fetch(
        session_factory, workspace_id=workspace_id, project_id=project_id
    )

    await refresh_analytics_snapshot(session_factory, task)

    async with session_factory() as session:
        day = (await _snapshots_by_granularity(session))["day"]
        # The superseded revision is stale evidence: not aggregated, not in
        # the provenance.
        assert day.metrics["referral_volume"][0]["value"] == 9
        assert day.source_classification_ids == [str(fresh_classification.id)]
        assert str(stale_classification.id) not in day.source_classification_ids


@pytest.mark.asyncio
async def test_refresh_honors_cooperative_cancel_at_classification_boundary(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
        await _seed_referral_chain(
            session, workspace_id=workspace_id, project_id=project_id
        )
    task = await _enqueue_and_fetch(
        session_factory, workspace_id=workspace_id, project_id=project_id
    )

    # One classification per batch so the boundary lands between rows.
    monkeypatch.setattr(snapshot_module, "_CLASSIFICATION_BATCH_SIZE", 1)
    real_check = snapshot_module._raise_if_task_terminal
    checks = 0

    async def _cancel_on_second_check(
        factory: async_sessionmaker[AsyncSession], row_id: uuid.UUID | None
    ) -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            # The row is cancelled mid-read (e.g. by an operator): the next
            # boundary check must stop the executor cooperatively.
            async with factory() as session:
                row = await session.get(AnalyticsTask, row_id)
                assert row is not None
                row.status = TASK_STATUS_CANCELLED
                await session.commit()
        await real_check(factory, row_id)

    monkeypatch.setattr(
        snapshot_module, "_raise_if_task_terminal", _cancel_on_second_check
    )

    with pytest.raises(TaskCancelledError):
        await refresh_analytics_snapshot(session_factory, task)

    async with session_factory() as session:
        # The cancel landed during the READ phase, before the single write
        # transaction: no partial projection is left behind.
        assert await session.scalar(select(func.count(AnalyticsSnapshot.id))) == 0


@pytest.mark.asyncio
async def test_worker_routes_analytics_snapshot_refresh_kind(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
        await _seed_referral_chain(
            session, workspace_id=workspace_id, project_id=project_id
        )
        task_id = await enqueue_analytics_snapshot_refresh(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            resync_seq=0,
        )
        await session.commit()
    assert task_id is not None

    worker = AnalyticsWorker(session_factory=session_factory, owner="snapshot-test")
    assert await worker.run_until_idle() == 1

    async with session_factory() as session:
        row = await session.get(AnalyticsTask, task_id)
        assert row is not None
        # Wired in A8: the dispatch table routes the kind to the real
        # executor — SUCCEEDED, never executor_not_wired.
        assert row.status == TASK_STATUS_SUCCEEDED
        assert row.error_code == ""
        assert await session.scalar(select(func.count(AnalyticsSnapshot.id))) == 3


@pytest.mark.asyncio
async def test_refresh_rejects_invalid_payload_and_project(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    workspace_id = uuid.uuid4()
    project_id = uuid.uuid4()

    def _task(payload: dict, *, with_project: bool = True) -> AnalyticsTask:
        # Unpersisted fixture row (id None): nothing to cancel against.
        return AnalyticsTask(
            workspace_id=workspace_id,
            project_id=project_id if with_project else None,
            task_kind="analytics_snapshot_refresh",
            payload=payload,
            idempotency_key=uuid.uuid4().hex,
        )

    with pytest.raises(ValueError, match="project_id"):
        await refresh_analytics_snapshot(session_factory, _task({}, with_project=False))
    with pytest.raises(ValueError, match="window_start"):
        await refresh_analytics_snapshot(session_factory, _task({}))
    with pytest.raises(ValueError, match="window_end before window_start"):
        await refresh_analytics_snapshot(
            session_factory,
            _task({"window_start": "2026-07-22", "window_end": "2026-07-20"}),
        )
