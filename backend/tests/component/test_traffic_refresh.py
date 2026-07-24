"""``traffic_snapshot_refresh`` executor (A7): transactional upsert
idempotency, the matched/unmatched ``site_url_id`` join, provenance
stamps, latest-``resync_seq`` selection, cooperative cancel at the
metric-row batch boundary, and worker routing for the kind.

Seeds the integrations-owned import graph (grant -> connection -> sync run
-> immutable artifact -> derived metric rows) via the shared analytics
helpers — the executor is a pure projection over those persisted rows (no
provider I/O, invariant 7). Requires a real Postgres.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.integrations import (
    DATASET_GA4_CHANNEL_DAILY,
    DATASET_GA4_LANDING_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    DATASET_GSC_PAGE_DAILY,
    DATASET_GSC_QUERY_DAILY,
    INTEGRATION_PROVIDER_GA4,
    INTEGRATION_PROVIDER_GSC,
)
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_SUCCEEDED,
)
from app.core.config.traffic import (
    TRAFFIC_FORMULA_VERSION,
    TRAFFIC_NORMALIZATION_VERSION,
)
from app.domain.analytics.enqueue import enqueue_traffic_snapshot_refresh
from app.domain.analytics.tasks import TaskCancelledError
from app.domain.site_health.normalization import canonical_identity
from app.domain.traffic import service as traffic_service
from app.domain.traffic.service import refresh_traffic_snapshot
from app.models.analytics import AnalyticsTask
from app.models.integrations import IntegrationConnection
from app.models.site_health import SiteUrl
from app.models.traffic import TrafficPageStat, TrafficQueryStat, TrafficSnapshot
from app.workers.analytics_worker import AnalyticsWorker
from tests.component.analytics_helpers import (
    seed_ga4_import,
    seed_metric_row,
    seed_workspace_project,
)

WINDOW = (date(2026, 7, 20), date(2026, 7, 22))
GSC_PROPERTY = "https://example.com/"
GA4_PROPERTY = "properties/123456789"
PAGE_A = "https://example.com/blog"
PAGE_B_RAW = "https://example.com/pricing?utm_medium=email"
PAGE_B = "https://example.com/pricing"


async def _seed_traffic_data(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict[str, object]:
    """Seed the full GSC + GA4 import graph the projection consumes.

    One shared Google grant carrying a GSC and a GA4 connection (the real
    consent shape); one import artifact per consumed dataset. Returns the
    named row/artifact ids for provenance assertions.
    """
    pages = await seed_ga4_import(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        dataset=DATASET_GSC_PAGE_DAILY,
        provider=INTEGRATION_PROVIDER_GSC,
        property_ref=GSC_PROPERTY,
        window=WINDOW,
    )
    r1 = await seed_metric_row(
        session,
        seed=pages,
        row_date=date(2026, 7, 20),
        dimension_values=[PAGE_A, "2026-07-20"],
        metrics={"clicks": 10, "impressions": 100, "ctr": 0.1, "position": 10.0},
    )
    r2 = await seed_metric_row(
        session,
        seed=pages,
        row_date=date(2026, 7, 21),
        dimension_values=[PAGE_A, "2026-07-21"],
        metrics={"clicks": 20, "impressions": 200, "ctr": 0.1, "position": 20.0},
    )
    r3 = await seed_metric_row(
        session,
        seed=pages,
        row_date=date(2026, 7, 20),
        dimension_values=[PAGE_B_RAW, "2026-07-20"],
        metrics={"clicks": 5, "impressions": 50, "ctr": 0.1, "position": 5.0},
    )
    gsc_connection = await session.get(IntegrationConnection, pages.connection_id)
    assert gsc_connection is not None
    # The second connection rides the SAME Google grant (one consent).
    ga4_connection = IntegrationConnection(
        workspace_id=workspace_id,
        grant_id=pages.grant_id,
        provider=INTEGRATION_PROVIDER_GA4,
        label="ga4 connection",
        account_ref="ga4-account-1",
    )
    session.add(ga4_connection)
    await session.flush()

    queries = await seed_ga4_import(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        dataset=DATASET_GSC_QUERY_DAILY,
        provider=INTEGRATION_PROVIDER_GSC,
        property_ref=GSC_PROPERTY,
        connection=gsc_connection,
        # Second run on the same connection/window needs a bumped seq
        # (uq_integration_sync_run_window_seq).
        resync_seq=1,
    )
    q1 = await seed_metric_row(
        session,
        seed=queries,
        row_date=date(2026, 7, 21),
        dimension_values=["Best  CRM", "2026-07-21"],
        metrics={"clicks": 3, "impressions": 30, "position": 8.0},
        resync_seq=1,
    )

    channels = await seed_ga4_import(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        dataset=DATASET_GA4_CHANNEL_DAILY,
        property_ref=GA4_PROPERTY,
        connection=ga4_connection,
    )
    c1 = await seed_metric_row(
        session,
        seed=channels,
        row_date=date(2026, 7, 20),
        dimension_values=["Organic Search", "20260720"],
        metrics={"sessions": 7, "conversions": 2},
    )
    c2 = await seed_metric_row(
        session,
        seed=channels,
        row_date=date(2026, 7, 21),
        dimension_values=["Paid Search", "20260721"],
        metrics={"sessions": 100, "conversions": 50},
    )
    source_medium = await seed_ga4_import(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        dataset=DATASET_GA4_SOURCE_MEDIUM_DAILY,
        property_ref=GA4_PROPERTY,
        connection=ga4_connection,
        resync_seq=1,
    )
    sm1 = await seed_metric_row(
        session,
        seed=source_medium,
        row_date=date(2026, 7, 21),
        dimension_values=["chatgpt.com", "referral", "20260721"],
        metrics={"sessions": 4, "conversions": 1},
        resync_seq=1,
    )
    sm2 = await seed_metric_row(
        session,
        seed=source_medium,
        row_date=date(2026, 7, 22),
        dimension_values=["google", "organic", "20260722"],
        metrics={"sessions": 999, "conversions": 9},
        resync_seq=1,
    )
    landing = await seed_ga4_import(
        session,
        workspace_id=workspace_id,
        project_id=project_id,
        dataset=DATASET_GA4_LANDING_DAILY,
        property_ref=GA4_PROPERTY,
        connection=ga4_connection,
        resync_seq=2,
    )
    l1 = await seed_metric_row(
        session,
        seed=landing,
        row_date=date(2026, 7, 21),
        dimension_values=[PAGE_A, "chatgpt.com", "referral", "20260721"],
        metrics={"sessions": 2, "conversions": 1},
        resync_seq=2,
    )
    l2 = await seed_metric_row(
        session,
        seed=landing,
        row_date=date(2026, 7, 20),
        dimension_values=[PAGE_B, "google", "organic", "20260720"],
        metrics={"sessions": 50, "conversions": 5},
        resync_seq=2,
    )
    await session.commit()
    return {
        "included_row_ids": [r1.id, r2.id, r3.id, q1.id, c1.id, sm1.id, l1.id],
        "excluded_row_ids": [c2.id, sm2.id, l2.id],
        "artifact_ids": [
            pages.artifact_id,
            queries.artifact_id,
            channels.artifact_id,
            source_medium.artifact_id,
            landing.artifact_id,
        ],
    }


async def _seed_site_url(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> uuid.UUID:
    """Seed the crawled SiteUrl identity that PAGE_A joins to."""
    canonical, url_hash = canonical_identity(PAGE_A)
    site_url = SiteUrl(
        workspace_id=workspace_id,
        project_id=project_id,
        normalized_url=canonical,
        url_hash=url_hash,
    )
    session.add(site_url)
    await session.commit()
    return site_url.id


async def _enqueue_and_fetch(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
) -> AnalyticsTask:
    async with session_factory() as session:
        task_id = await enqueue_traffic_snapshot_refresh(
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
) -> dict[str, TrafficSnapshot]:
    rows = list((await session.scalars(select(TrafficSnapshot))).all())
    return {row.granularity: row for row in rows}


@pytest.mark.asyncio
async def test_refresh_builds_snapshots_stats_join_and_provenance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
        ids = await _seed_traffic_data(
            session, workspace_id=workspace_id, project_id=project_id
        )
        site_url_id = await _seed_site_url(
            session, workspace_id=workspace_id, project_id=project_id
        )
    task = await _enqueue_and_fetch(
        session_factory, workspace_id=workspace_id, project_id=project_id
    )

    await refresh_traffic_snapshot(session_factory, task)

    async with session_factory() as session:
        # One current snapshot per configured granularity, version-stamped.
        snapshots = await _snapshots_by_granularity(session)
        assert set(snapshots) == {"day", "week", "month"}
        day = snapshots["day"]
        assert day.workspace_id == workspace_id
        assert day.project_id == project_id
        assert day.window_start == WINDOW[0]
        assert day.window_end == WINDOW[1]
        assert day.formula_version == TRAFFIC_FORMULA_VERSION
        assert day.normalization_version == TRAFFIC_NORMALIZATION_VERSION

        totals = day.metrics["totals"]
        assert totals["impressions"] == 350
        assert totals["clicks"] == 35
        assert totals["ctr"] == pytest.approx(0.1)
        # (10*100 + 20*200 + 5*50) / 350 — impression-weighted mean.
        assert totals["position"] == pytest.approx(15.0)
        # 7 organic-channel + 4 AI sessions; paid/google-organic excluded.
        assert totals["sessions"] == 11
        assert totals["conversions"] == 3

        # Provenance: exactly the included rows + their artifacts (inv. 4).
        assert set(day.source_metric_row_ids) == {
            str(row_id) for row_id in ids["included_row_ids"]
        }
        for excluded in ids["excluded_row_ids"]:
            assert str(excluded) not in day.source_metric_row_ids
        assert set(day.source_artifact_ids) == {
            str(artifact_id) for artifact_id in ids["artifact_ids"]
        }

        # Day series: 3 buckets, rows-free bucket is a gap.
        series = day.metrics["series"]
        assert [p["date"] for p in series["clicks"]] == [
            "2026-07-20",
            "2026-07-21",
            "2026-07-22",
        ]
        assert [p["value"] for p in series["impressions"]] == [150, 200, None]
        assert [p["value"] for p in series["clicks"]] == [15, 20, None]
        assert [p["value"] for p in series["sessions"]] == [7, 4, None]
        assert [p["value"] for p in series["conversions"]] == [2, 1, None]
        assert series["position"][0]["value"] == pytest.approx(1250 / 150)
        assert series["position"][2]["value"] is None

        # Week + month windows collapse to one bucket labelled at the
        # window start (7/20 is itself the ISO Monday / mid-month clamp).
        for granularity in ("week", "month"):
            other = snapshots[granularity]
            assert other.metrics["totals"] == totals
            assert [p["date"] for p in other.metrics["series"]["clicks"]] == [
                "2026-07-20"
            ]

        # Page stats: the canonical-keyed pages with the SiteUrl join.
        page_stats = list(
            (
                await session.scalars(
                    select(TrafficPageStat).where(
                        TrafficPageStat.snapshot_id == day.id
                    )
                )
            ).all()
        )
        by_url = {stat.canonical_url: stat for stat in page_stats}
        assert set(by_url) == {PAGE_A, PAGE_B}
        page_a = by_url[PAGE_A]
        # Matched join: PAGE_A resolves to the seeded SiteUrl identity.
        assert page_a.site_url_id == site_url_id
        assert page_a.metrics["impressions"] == 300
        assert page_a.metrics["clicks"] == 30
        assert page_a.metrics["ctr"] == pytest.approx(0.1)
        assert page_a.metrics["position"] == pytest.approx(5000 / 300)
        # AI-referred landing sessions fold into the page's GA4 metrics.
        assert page_a.metrics["sessions"] == 2
        assert page_a.metrics["conversions"] == 1
        page_b = by_url[PAGE_B]
        # Unmatched join: still a valid measured page with a NULL join.
        assert page_b.site_url_id is None
        assert page_b.metrics["clicks"] == 5
        # Its google/organic landing row is excluded (not an AI referral).
        assert page_b.metrics["sessions"] is None
        assert page_b.metrics["conversions"] is None
        assert page_b.source_metric_row_ids == [
            str(ids["included_row_ids"][2])
        ]

        # Query stats: normalized key, GSC-only measures.
        query_stats = list(
            (
                await session.scalars(
                    select(TrafficQueryStat).where(
                        TrafficQueryStat.snapshot_id == day.id
                    )
                )
            ).all()
        )
        assert len(query_stats) == 1
        query = query_stats[0]
        assert query.normalized_query == "best crm"
        assert query.metrics == {
            "impressions": 30,
            "clicks": 3,
            "ctr": pytest.approx(0.1),
            "position": pytest.approx(8.0),
        }
        assert query.source_metric_row_ids == [str(ids["included_row_ids"][3])]


@pytest.mark.asyncio
async def test_refresh_upsert_is_idempotent_across_reruns(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
        await _seed_traffic_data(
            session, workspace_id=workspace_id, project_id=project_id
        )
    task = await _enqueue_and_fetch(
        session_factory, workspace_id=workspace_id, project_id=project_id
    )

    await refresh_traffic_snapshot(session_factory, task)
    async with session_factory() as session:
        first = await _snapshots_by_granularity(session)
        first_metrics = {
            granularity: snapshot.metrics
            for granularity, snapshot in first.items()
        }

    # A second run of the same task recomputes from the same latest rows:
    # the SAME snapshot rows are updated in place and the stat rows are
    # replaced, never duplicated (traffic.md section 4).
    await refresh_traffic_snapshot(session_factory, task)

    async with session_factory() as session:
        second = await _snapshots_by_granularity(session)
        assert set(second) == {"day", "week", "month"}
        for granularity, snapshot in second.items():
            assert snapshot.id == first[granularity].id
            assert snapshot.metrics == first_metrics[granularity]
        day_id = second["day"].id
        assert (
            await session.scalar(
                select(func.count(TrafficPageStat.id)).where(
                    TrafficPageStat.snapshot_id == day_id
                )
            )
        ) == 2
        assert (
            await session.scalar(
                select(func.count(TrafficQueryStat.id)).where(
                    TrafficQueryStat.snapshot_id == day_id
                )
            )
        ) == 1


@pytest.mark.asyncio
async def test_refresh_reads_only_the_latest_resync_seq(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
        pages = await seed_ga4_import(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            dataset=DATASET_GSC_PAGE_DAILY,
            provider=INTEGRATION_PROVIDER_GSC,
            property_ref=GSC_PROPERTY,
            window=WINDOW,
        )
        stale = await seed_metric_row(
            session,
            seed=pages,
            row_date=date(2026, 7, 20),
            dimension_values=[PAGE_A, "2026-07-20"],
            metrics={"clicks": 5, "impressions": 50, "position": 2.0},
            resync_seq=0,
        )
        fresh = await seed_metric_row(
            session,
            seed=pages,
            row_date=date(2026, 7, 20),
            dimension_values=[PAGE_A, "2026-07-20"],
            metrics={"clicks": 9, "impressions": 90, "position": 4.0},
            resync_seq=1,
        )
        await session.commit()
    task = await _enqueue_and_fetch(
        session_factory, workspace_id=workspace_id, project_id=project_id
    )

    await refresh_traffic_snapshot(session_factory, task)

    async with session_factory() as session:
        snapshots = await _snapshots_by_granularity(session)
        day = snapshots["day"]
        # The superseded revision is stale evidence: not aggregated, not in
        # the provenance.
        assert day.metrics["totals"]["clicks"] == 9
        assert day.metrics["totals"]["position"] == pytest.approx(4.0)
        assert day.source_metric_row_ids == [str(fresh.id)]
        assert str(stale.id) not in day.source_metric_row_ids


@pytest.mark.asyncio
async def test_refresh_honors_cooperative_cancel_at_metric_row_boundary(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
        await _seed_traffic_data(
            session, workspace_id=workspace_id, project_id=project_id
        )
    task = await _enqueue_and_fetch(
        session_factory, workspace_id=workspace_id, project_id=project_id
    )

    # One row per batch so the boundary lands between metric rows.
    monkeypatch.setattr(traffic_service, "_METRIC_ROW_BATCH_SIZE", 1)
    real_check = traffic_service._raise_if_task_terminal
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
        traffic_service, "_raise_if_task_terminal", _cancel_on_second_check
    )

    with pytest.raises(TaskCancelledError):
        await refresh_traffic_snapshot(session_factory, task)

    async with session_factory() as session:
        # The cancel landed during the READ phase, before the single write
        # transaction: no partial projection is left behind.
        assert await session.scalar(select(func.count(TrafficSnapshot.id))) == 0
        assert await session.scalar(select(func.count(TrafficPageStat.id))) == 0
        assert (
            await session.scalar(select(func.count(TrafficQueryStat.id))) == 0
        )


@pytest.mark.asyncio
async def test_worker_routes_traffic_snapshot_refresh_kind(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        workspace_id, project_id = await seed_workspace_project(session)
        await _seed_traffic_data(
            session, workspace_id=workspace_id, project_id=project_id
        )
        task_id = await enqueue_traffic_snapshot_refresh(
            session,
            workspace_id=workspace_id,
            project_id=project_id,
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            resync_seq=0,
        )
        await session.commit()
    assert task_id is not None

    worker = AnalyticsWorker(session_factory=session_factory, owner="traffic-test")
    assert await worker.run_until_idle() == 1

    async with session_factory() as session:
        row = await session.get(AnalyticsTask, task_id)
        assert row is not None
        # Wired in A7: the dispatch table routes the kind to the real
        # executor — SUCCEEDED, never executor_not_wired.
        assert row.status == TASK_STATUS_SUCCEEDED
        assert row.error_code == ""
        assert await session.scalar(select(func.count(TrafficSnapshot.id))) == 3


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
            task_kind="traffic_snapshot_refresh",
            payload=payload,
            idempotency_key=uuid.uuid4().hex,
        )

    with pytest.raises(ValueError, match="project_id"):
        await refresh_traffic_snapshot(session_factory, _task({}, with_project=False))
    with pytest.raises(ValueError, match="window_start"):
        await refresh_traffic_snapshot(session_factory, _task({}))
    with pytest.raises(ValueError, match="window_end before window_start"):
        await refresh_traffic_snapshot(
            session_factory,
            _task({"window_start": "2026-07-22", "window_end": "2026-07-20"}),
        )
