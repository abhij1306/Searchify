"""Component tests for derivation: artifact -> IntegrationMetricRow (I9).

Drives the real ``IntegrationWorker`` over recorded GSC fixtures (and the
pure ``build_metric_row_values`` transform directly) against a live
Postgres schema. Covers:

  - Provenance on EVERY derived row (invariant 4): ``source_artifact_id`` +
    ``INTEGRATION_IMPORTER_VERSION`` + the run's ``resync_seq``, with the
    ``dimension_key`` packed in the template's declared order (date
    included) via the config-owned ``pack_dimension_key`` (C1).
  - Unmapped property: the run fails with ``unmapped_property`` and no
    project is ever guessed; zero metric rows + zero projections.
  - Re-sync of a completed window writes NEW rows at the higher
    ``resync_seq``; old revisions are retained, never overwritten (inv. 3).
  - Derivation is idempotent under replay (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.core.config.integrations import (
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    DATASET_GSC_PAGE_DAILY,
    DATASET_GSC_QUERY_DAILY,
    ERROR_UNMAPPED_PROPERTY,
    EVENT_INTEGRATION_SYNC_FINISHED,
    GRANT_STATUS_CONNECTED,
    INTEGRATION_DATASET_TEMPLATES,
    INTEGRATION_IMPORTER_VERSION,
    INTEGRATION_PROVIDER_GA4,
    INTEGRATION_PROVIDER_GSC,
    INTEGRATION_TRANSPORT_GOOGLE,
    integration_settings,
)
from app.core.config.task_queue import (
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCEEDED,
)
from app.core.security import encrypt_secret
from app.domain.integrations.derive import (
    UnmappedPropertyError,
    build_metric_row_values,
    derive_run,
    resolve_active_mapping,
)
from app.domain.integrations.sync import enqueue_sync_run
from app.models.analytics import AnalyticsTask
from app.models.brand import OwnedDomain
from app.models.integrations import (
    IntegrationConnection,
    IntegrationEvent,
    IntegrationImportArtifact,
    IntegrationMetricRow,
    IntegrationOAuthGrant,
    IntegrationPropertyMapping,
    IntegrationSyncRun,
)
from app.models.project import Project
from app.models.workspace import Workspace
from app.workers.integration_worker import IntegrationWorker

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "integrations"
_WINDOW = (date(2026, 7, 20), date(2026, 7, 22))
_PROPERTY_REF = "https://example.com"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _fast_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(integration_settings, "gsc_requests_per_minute", 60000)


def _gsc_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        dimensions = tuple(body.get("dimensions") or ())
        if "page" in dimensions:
            return httpx.Response(200, json=_fixture("gsc_search_analytics_page1.json"))
        return httpx.Response(
            200, json=_fixture("gsc_search_analytics_query_page.json")
        )

    return httpx.MockTransport(handler)


async def _seed_graph(db_session, *, with_mapping: bool = True) -> tuple:
    workspace = Workspace(name="Acme")
    db_session.add(workspace)
    await db_session.flush()
    project = Project(workspace_id=workspace.id, name="Acme Site")
    db_session.add(project)
    await db_session.flush()
    db_session.add(OwnedDomain(project_id=project.id, domain="example.com"))
    grant = IntegrationOAuthGrant(
        workspace_id=workspace.id,
        transport=INTEGRATION_TRANSPORT_GOOGLE,
        access_token_encrypted=encrypt_secret("access-token-1"),
        refresh_token_encrypted=encrypt_secret("refresh-token-1"),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        granted_scopes=["scope-a"],
        status=GRANT_STATUS_CONNECTED,
    )
    db_session.add(grant)
    await db_session.flush()
    connection = IntegrationConnection(
        workspace_id=workspace.id,
        grant_id=grant.id,
        provider=INTEGRATION_PROVIDER_GSC,
        label="gsc connection",
        account_ref=_PROPERTY_REF,
    )
    db_session.add(connection)
    await db_session.flush()
    if with_mapping:
        db_session.add(
            IntegrationPropertyMapping(
                workspace_id=workspace.id,
                connection_id=connection.id,
                provider=INTEGRATION_PROVIDER_GSC,
                property_ref=_PROPERTY_REF,
                project_id=project.id,
                status="active",
            )
        )
    await db_session.commit()
    return workspace.id, project.id, connection.id


async def _enqueue_run(db_session, workspace_id, connection_id) -> IntegrationSyncRun:
    return await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection_id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )


async def _run_worker(session_factory) -> None:
    worker = IntegrationWorker(
        session_factory=session_factory,
        owner="derivation-test",
        transport=_gsc_transport(),
    )
    await worker.run_until_idle()


async def _run_artifacts(
    db_session, run_id: uuid.UUID
) -> list[IntegrationImportArtifact]:
    result = await db_session.scalars(
        select(IntegrationImportArtifact).where(
            IntegrationImportArtifact.sync_run_id == run_id
        )
    )
    return list(result)


async def _run_metric_rows(db_session, run_id: uuid.UUID) -> list[IntegrationMetricRow]:
    artifact_ids = select(IntegrationImportArtifact.id).where(
        IntegrationImportArtifact.sync_run_id == run_id
    )
    result = await db_session.scalars(
        select(IntegrationMetricRow).where(
            IntegrationMetricRow.source_artifact_id.in_(artifact_ids)
        )
    )
    return list(result)


@pytest.mark.asyncio
async def test_derivation_provenance_on_every_row(session_factory, db_session) -> None:
    workspace_id, project_id, connection_id = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, workspace_id, connection_id)

    await _run_worker(session_factory)

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_SUCCEEDED
    artifacts = await _run_artifacts(db_session, run.id)
    assert len(artifacts) == 2  # one page-dataset page + one query-dataset page
    rows = await _run_metric_rows(db_session, run.id)
    assert len(rows) == 3

    artifact_ids = {artifact.id for artifact in artifacts}
    for row in rows:
        # Provenance triple on every row (invariant 4).
        assert row.source_artifact_id in artifact_ids
        assert row.importer_version == INTEGRATION_IMPORTER_VERSION
        assert row.resync_seq == run.resync_seq == 0
        # Identity resolved through the mapping, never from client input.
        assert row.project_id == project_id
        assert row.workspace_id == workspace_id
        assert row.property_ref == _PROPERTY_REF
        assert row.provider == INTEGRATION_PROVIDER_GSC

    by_dataset: dict[str, list[IntegrationMetricRow]] = {}
    for row in rows:
        by_dataset.setdefault(row.dataset, []).append(row)

    page_rows = sorted(
        by_dataset[DATASET_GSC_PAGE_DAILY], key=lambda row: row.dimension_key
    )
    assert [row.dimension_key for row in page_rows] == [
        "https://example.com/ | 2026-07-20",
        "https://example.com/pricing | 2026-07-20",
    ]
    assert all(row.date == date(2026, 7, 20) for row in page_rows)
    first = page_rows[0]
    assert first.metrics == {
        "clicks": 12,
        "impressions": 340,
        "ctr": 0.0353,
        "position": 4.2,
    }

    (query_row,) = by_dataset[DATASET_GSC_QUERY_DAILY]
    assert query_row.dimension_key == "searchify | 2026-07-20"
    assert query_row.date == date(2026, 7, 20)
    # Each row points at the artifact of ITS dataset (not just any artifact).
    artifact_dataset = {artifact.id: artifact.dataset for artifact in artifacts}
    for row in rows:
        assert artifact_dataset[row.source_artifact_id] == row.dataset


@pytest.mark.asyncio
async def test_unmapped_property_fails_run(session_factory, db_session) -> None:
    workspace_id, _project_id, connection_id = await _seed_graph(
        db_session, with_mapping=False
    )
    run = await _enqueue_run(db_session, workspace_id, connection_id)

    await _run_worker(session_factory)

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_FAILED
    assert run.error_code == ERROR_UNMAPPED_PROPERTY
    assert run.completed_at is not None
    # The raw import landed (immutable evidence retained) but NOTHING was
    # derived or projected: the property was never guessed.
    assert len(await _run_artifacts(db_session, run.id)) == 2
    assert await _run_metric_rows(db_session, run.id) == []
    assert list((await db_session.scalars(select(AnalyticsTask))).all()) == []
    events = list(
        (
            await db_session.scalars(
                select(IntegrationEvent).where(
                    IntegrationEvent.workspace_id == workspace_id
                )
            )
        ).all()
    )
    assert EVENT_INTEGRATION_SYNC_FINISHED not in [event.event_type for event in events]
    connection = await db_session.get(IntegrationConnection, connection_id)
    assert connection.last_synced_at is None


@pytest.mark.asyncio
async def test_resync_writes_new_rows_old_retained(session_factory, db_session) -> None:
    workspace_id, project_id, connection_id = await _seed_graph(db_session)
    run0 = await _enqueue_run(db_session, workspace_id, connection_id)
    await _run_worker(session_factory)
    await db_session.refresh(run0)
    assert run0.status == TASK_STATUS_SUCCEEDED

    rows0 = await _run_metric_rows(db_session, run0.id)
    artifacts0 = await _run_artifacts(db_session, run0.id)
    hashes0 = {artifact.id: artifact.payload_hash for artifact in artifacts0}
    assert len(rows0) == 3
    assert {row.resync_seq for row in rows0} == {0}

    # The completed window re-syncs at resync_seq 1 (I5 allocation).
    run1 = await _enqueue_run(db_session, workspace_id, connection_id)
    assert run1.resync_seq == 1
    await _run_worker(session_factory)
    await db_session.refresh(run1)
    assert run1.status == TASK_STATUS_SUCCEEDED

    rows1 = await _run_metric_rows(db_session, run1.id)
    assert len(rows1) == 3
    assert {row.resync_seq for row in rows1} == {1}

    # Old rows + old artifacts are retained, never mutated (invariant 3):
    # same identity tuple at both revisions, distinct row ids.
    identity = (
        select(
            IntegrationMetricRow.project_id,
            IntegrationMetricRow.property_ref,
            IntegrationMetricRow.provider,
            IntegrationMetricRow.dataset,
            IntegrationMetricRow.date,
            IntegrationMetricRow.dimension_key,
            IntegrationMetricRow.resync_seq,
        )
        .where(IntegrationMetricRow.project_id == project_id)
        .group_by(
            IntegrationMetricRow.project_id,
            IntegrationMetricRow.property_ref,
            IntegrationMetricRow.provider,
            IntegrationMetricRow.dataset,
            IntegrationMetricRow.date,
            IntegrationMetricRow.dimension_key,
            IntegrationMetricRow.resync_seq,
        )
    )
    grouped = (await db_session.execute(identity)).all()
    assert len(grouped) == 6  # 3 identities x 2 revisions
    seqs = {row.resync_seq for row in grouped}
    assert seqs == {0, 1}
    for artifact in artifacts0:
        await db_session.refresh(artifact)
        assert artifact.payload_hash == hashes0[artifact.id]


@pytest.mark.asyncio
async def test_derivation_replay_is_a_dedup_noop(session_factory, db_session) -> None:
    workspace_id, _project_id, connection_id = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, workspace_id, connection_id)
    await _run_worker(session_factory)
    await db_session.refresh(run)
    assert run.status == TASK_STATUS_SUCCEEDED

    async with session_factory() as session:
        persisted_run = await session.get(IntegrationSyncRun, run.id)
        connection = await session.get(IntegrationConnection, connection_id)
        artifacts = list(
            (
                await session.scalars(
                    select(IntegrationImportArtifact).where(
                        IntegrationImportArtifact.sync_run_id == run.id
                    )
                )
            ).all()
        )
        derived = await derive_run(
            session, run=persisted_run, connection=connection, artifacts=artifacts
        )
        await session.commit()
    # The transform still maps every payload row, but the insert conflicts
    # on the identity tuple and lands NOTHING twice.
    assert derived.metric_row_count == 3
    assert len(await _run_metric_rows(db_session, run.id)) == 3


@pytest.mark.asyncio
async def test_resolve_active_mapping_never_guesses(db_session) -> None:
    workspace_id, project_id, connection_id = await _seed_graph(db_session)
    mapping = await resolve_active_mapping(
        db_session,
        workspace_id=workspace_id,
        provider=INTEGRATION_PROVIDER_GSC,
        property_ref=_PROPERTY_REF,
    )
    assert mapping.project_id == project_id
    assert mapping.connection_id == connection_id
    with pytest.raises(UnmappedPropertyError):
        await resolve_active_mapping(
            db_session,
            workspace_id=workspace_id,
            provider=INTEGRATION_PROVIDER_GA4,
            property_ref=_PROPERTY_REF,
        )


def test_build_metric_row_values_pure_packing() -> None:
    """Pure transform: declared-order packing incl. date; GA4 compact dates;
    malformed rows are skipped, never guessed."""
    template = INTEGRATION_DATASET_TEMPLATES[DATASET_GA4_SOURCE_MEDIUM_DAILY]
    workspace_id = uuid.uuid4()
    run = IntegrationSyncRun(
        workspace_id=workspace_id,
        connection_id=uuid.uuid4(),
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
        resync_seq=2,
        idempotency_key=uuid.uuid4().hex,
    )
    mapping = IntegrationPropertyMapping(
        workspace_id=workspace_id,
        connection_id=run.connection_id,
        provider=INTEGRATION_PROVIDER_GA4,
        property_ref="properties/123",
        project_id=uuid.uuid4(),
    )
    artifact = IntegrationImportArtifact(
        id=uuid.uuid4(),
        sync_run_id=run.id,
        connection_id=run.connection_id,
        workspace_id=workspace_id,
        provider=INTEGRATION_PROVIDER_GA4,
        dataset=DATASET_GA4_SOURCE_MEDIUM_DAILY,
        payload_hash=hashlib.sha256(b"fixture").hexdigest(),
        row_count=3,
        payload={
            "rows": [
                {
                    "keys": ["google", "organic", "20260720"],
                    "sessions": 41,
                    "engagedSessions": 30,
                    "conversions": 2,
                },
                {"keys": ["only-two", "20260720"]},  # wrong arity: skipped
                {
                    "keys": ["bing", "referral", "not-a-date"]  # bad date: skipped
                },
            ]
        },
    )

    values = build_metric_row_values(
        template=template, run=run, mapping=mapping, artifact=artifact
    )
    assert len(values) == 1
    (row,) = values
    assert row["dimension_key"] == "google | organic | 20260720"
    assert row["date"] == date(2026, 7, 20)
    assert row["metrics"] == {
        "sessions": 41,
        "engagedSessions": 30,
        "conversions": 2,
    }
    assert row["resync_seq"] == 2
    assert row["importer_version"] == INTEGRATION_IMPORTER_VERSION
    assert row["source_artifact_id"] == artifact.id
    assert row["project_id"] == mapping.project_id
