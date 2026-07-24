"""Component tests for the GA4 sync path (I11).

Runs the real ``IntegrationWorker`` against a live Postgres schema with an
injected fake Google OAuth + GA4 Data API (``httpx.MockTransport``;
recorded ``runReport`` fixtures). Covers the full worker contract for a
GA4 connection riding the ONE shared Google grant (no new OAuth):

  - claim -> serialized refresh (shared grant) -> paged ``runReport``
    import (one immutable artifact per page) -> derivation metric rows
    with the full provenance triple + C1-packed ``dimension_key``.
  - The runReport -> GSC-shaped row mapping (``keys`` in declared
    template order incl. the compact GA4 date; metric strings coerced to
    numbers).
  - Empty result pages, and a 401 marking the grant ``needs_reauth``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.config.analytics import (
    ANALYTICS_TASK_KIND_INGEST_REFERRALS,
    ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH,
)
from app.core.config.integrations import (
    DATASET_GA4_CHANNEL_DAILY,
    DATASET_GA4_LANDING_DAILY,
    DATASET_GA4_REFERRER_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    ERROR_GRANT_AUTH_FAILED,
    EVENT_INTEGRATION_REAUTH_REQUIRED,
    EVENT_INTEGRATION_SYNC_FINISHED,
    EVENT_INTEGRATION_SYNC_STARTED,
    GRANT_STATUS_CONNECTED,
    GRANT_STATUS_NEEDS_REAUTH,
    INTEGRATION_IMPORTER_VERSION,
    INTEGRATION_PROVIDER_GA4,
    INTEGRATION_TRANSPORT_GOOGLE,
    integration_settings,
)
from app.core.config.task_queue import (
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCEEDED,
)
from app.core.security import decrypt_secret, encrypt_secret
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
)
from app.models.project import Project
from app.models.workspace import Workspace
from app.workers.integration_worker import IntegrationWorker

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "integrations"
_WINDOW = (date(2026, 7, 20), date(2026, 7, 22))
_PROPERTY_REF = "123456789"

_GA4_DATASETS = (
    DATASET_GA4_CHANNEL_DAILY,
    DATASET_GA4_SOURCE_MEDIUM_DAILY,
    DATASET_GA4_REFERRER_DAILY,
    DATASET_GA4_LANDING_DAILY,
)


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _fast_pacing_and_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep request pacing out of the test timing budget and give the OAuth
    # refresh path env-injected client credentials (never logged).
    monkeypatch.setattr(integration_settings, "ga4_requests_per_minute", 60000)
    monkeypatch.setattr(settings, "integration_google_client_id", "test-client-id")
    monkeypatch.setattr(
        settings, "integration_google_client_secret", "test-client-secret"
    )


class _ProviderFake:
    """The fake Google OAuth + GA4 Data API, routing by request host.

    ``drop_row`` swaps the channel dataset's first page for a variant whose
    second row is malformed (a non-numeric metric): the raw page is FULL
    (2 rows) but normalization keeps only 1 — the paging-termination
    regression fixture.
    """

    def __init__(
        self, *, ga4_status: int = 200, empty: bool = False, drop_row: bool = False
    ) -> None:
        self.token_calls: list[httpx.Request] = []
        self.ga4_auth: list[str] = []
        self.ga4_urls: list[str] = []
        self.ga4_requests: list[dict] = []
        self._ga4_status = ga4_status
        self._empty = empty
        self._drop_row = drop_row

    def _ga4_response(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.ga4_auth.append(request.headers.get("authorization", ""))
        self.ga4_urls.append(str(request.url))
        self.ga4_requests.append(body)
        if self._ga4_status != 200:
            return httpx.Response(
                self._ga4_status, json={"error": {"message": "ga4 boom"}}
            )
        if self._empty:
            return httpx.Response(200, json={"rowCount": 0})
        dimensions = tuple(
            entry.get("name") for entry in body.get("dimensions") or ()
        )
        offset = int(body.get("offset") or 0)
        if "sessionDefaultChannelGroup" in dimensions:
            if offset:
                payload = _fixture("ga4_run_report_page2.json")
            elif self._drop_row:
                payload = _fixture("ga4_run_report_page1_dropped_row.json")
            else:
                payload = _fixture("ga4_run_report_page1.json")
        elif "sessionSource" in dimensions and "landingPage" in dimensions:
            payload = _fixture("ga4_run_report_landing.json")
        elif "sessionSource" in dimensions:
            payload = _fixture("ga4_run_report_source_medium.json")
        else:
            payload = _fixture("ga4_run_report_referrer.json")
        return httpx.Response(200, json=payload)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            self.token_calls.append(request)
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-access-token",
                    "expires_in": 3600,
                    "scope": "scope-a scope-b",
                },
            )
        if request.url.host == "analyticsdata.googleapis.com":
            return self._ga4_response(request)
        raise AssertionError(f"unexpected request: {request.url}")

    def mock_transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


async def _seed_graph(
    db_session,
    *,
    grant_status: str = GRANT_STATUS_CONNECTED,
    token_expires_at: datetime | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
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
        token_expires_at=token_expires_at or (datetime.now(UTC) + timedelta(hours=1)),
        granted_scopes=["scope-a"],
        status=grant_status,
    )
    db_session.add(grant)
    await db_session.flush()
    connection = IntegrationConnection(
        workspace_id=workspace.id,
        grant_id=grant.id,
        provider=INTEGRATION_PROVIDER_GA4,
        label="ga4 connection",
        account_ref=_PROPERTY_REF,
    )
    db_session.add(connection)
    await db_session.flush()
    db_session.add(
        IntegrationPropertyMapping(
            workspace_id=workspace.id,
            connection_id=connection.id,
            provider=INTEGRATION_PROVIDER_GA4,
            property_ref=_PROPERTY_REF,
            project_id=project.id,
            status="active",
        )
    )
    await db_session.commit()
    return workspace.id, project.id, grant.id, connection.id


def _worker(
    session_factory: async_sessionmaker[AsyncSession],
    transport: httpx.AsyncBaseTransport,
) -> IntegrationWorker:
    return IntegrationWorker(
        session_factory=session_factory, owner="ga4-test", transport=transport
    )


async def _artifacts(db_session, run_id: uuid.UUID) -> list[IntegrationImportArtifact]:
    result = await db_session.scalars(
        select(IntegrationImportArtifact)
        .where(IntegrationImportArtifact.sync_run_id == run_id)
        .order_by(
            IntegrationImportArtifact.dataset.asc(),
            IntegrationImportArtifact.created_at.asc(),
            IntegrationImportArtifact.id.asc(),
        )
    )
    return list(result)


async def _metric_rows(db_session, run_id: uuid.UUID) -> list[IntegrationMetricRow]:
    artifact_ids = select(IntegrationImportArtifact.id).where(
        IntegrationImportArtifact.sync_run_id == run_id
    )
    result = await db_session.scalars(
        select(IntegrationMetricRow).where(
            IntegrationMetricRow.source_artifact_id.in_(artifact_ids)
        )
    )
    return list(result)


def _canonical_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_fixture_import_refresh_artifacts_derivation(
    session_factory, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """claim -> refresh (shared grant) -> artifacts -> derivation rows."""
    monkeypatch.setattr(integration_settings, "sync_page_size", 2)
    # Near-expiry token: the worker performs the serialized refresh first.
    near_expiry = datetime.now(UTC) + timedelta(seconds=5)
    workspace_id, project_id, grant_id, connection_id = await _seed_graph(
        db_session, token_expires_at=near_expiry
    )
    run = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection_id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )
    fake = _ProviderFake()

    ran = await _worker(session_factory, fake.mock_transport()).run_until_idle()
    assert ran == 1

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_SUCCEEDED
    assert run.error_code == ""

    # Exactly ONE serialized refresh on the shared grant, then every GA4
    # call carried the fresh Bearer token (never the expired one).
    (token_call,) = fake.token_calls
    form = parse_qs(token_call.content.decode("utf-8"))
    assert form["grant_type"] == ["refresh_token"]
    grant = await db_session.get(IntegrationOAuthGrant, grant_id)
    assert decrypt_secret(grant.access_token_encrypted) == "fresh-access-token"
    assert fake.ga4_auth == ["Bearer fresh-access-token"] * 5

    # The runReport requests carried the template dimensions/metrics and
    # the limit/offset paging (channel dataset paged 0 -> 2).
    channel_requests = [
        body
        for body in fake.ga4_requests
        if any(
            entry.get("name") == "sessionDefaultChannelGroup"
            for entry in body["dimensions"]
        )
    ]
    assert [body["offset"] for body in channel_requests] == [0, 2]
    assert [body["limit"] for body in channel_requests] == [2, 2]
    assert channel_requests[0]["dateRanges"] == [
        {"startDate": "2026-07-20", "endDate": "2026-07-22"}
    ]
    assert [m["name"] for m in channel_requests[0]["metrics"]] == [
        "sessions",
        "engagedSessions",
        "conversions",
    ]
    # Every call hit the pinned runReport path for the connection's
    # property ref (SSRF allow-listed host).
    assert fake.ga4_urls == [
        f"https://analyticsdata.googleapis.com/v1beta/properties/{_PROPERTY_REF}:runReport"
    ] * 5

    artifacts = await _artifacts(db_session, run.id)
    by_dataset: dict[str, list[IntegrationImportArtifact]] = {}
    for artifact in artifacts:
        by_dataset.setdefault(artifact.dataset, []).append(artifact)
    assert sorted(by_dataset) == sorted(_GA4_DATASETS)
    # Channel dataset paged (2 rows + 1 row); the others are one page each.
    channel = by_dataset[DATASET_GA4_CHANNEL_DAILY]
    assert [a.query_snapshot["startRow"] for a in channel] == [0, 2]
    assert [a.row_count for a in channel] == [2, 1]
    for dataset in (
        DATASET_GA4_SOURCE_MEDIUM_DAILY,
        DATASET_GA4_REFERRER_DAILY,
        DATASET_GA4_LANDING_DAILY,
    ):
        assert [a.row_count for a in by_dataset[dataset]] == [1]

    for artifact in artifacts:
        # Immutable evidence: sha256 of the normalized payload + the
        # credential-free query snapshot (invariant 6).
        assert artifact.payload_hash == _canonical_hash(artifact.payload)
        assert artifact.provider == INTEGRATION_PROVIDER_GA4
        assert artifact.query_snapshot["api_method"] == "runReport"
        snapshot_text = json.dumps(artifact.query_snapshot).lower()
        assert "token" not in snapshot_text
        assert "authorization" not in snapshot_text
        # Normalized row shape: keys in declared template order + metrics.
        for row in artifact.payload["rows"]:
            assert set(row) == {
                "keys",
                "sessions",
                "engagedSessions",
                "conversions",
            }
            assert all(isinstance(v, str) for v in row["keys"])
            assert isinstance(row["sessions"], int)

    # Derivation: one metric row per artifact row, full provenance (inv. 4).
    rows = await _metric_rows(db_session, run.id)
    assert len(rows) == 6  # 3 channel + 1 source/medium + 1 referrer + 1 landing
    artifact_ids = {artifact.id for artifact in artifacts}
    for row in rows:
        assert row.source_artifact_id in artifact_ids
        assert row.importer_version == INTEGRATION_IMPORTER_VERSION
        assert row.resync_seq == run.resync_seq == 0
        assert row.project_id == project_id
        assert row.provider == INTEGRATION_PROVIDER_GA4
        assert row.property_ref == _PROPERTY_REF

    by_key = {row.dimension_key: row for row in rows}
    organic = by_key["Organic Search | 20260720"]
    assert organic.dataset == DATASET_GA4_CHANNEL_DAILY
    assert organic.date == date(2026, 7, 20)
    assert organic.metrics == {"sessions": 41, "engagedSessions": 30, "conversions": 2}
    referral = by_key["https://chatgpt.com/ | 20260721"]
    assert referral.dataset == DATASET_GA4_REFERRER_DAILY
    assert referral.metrics["sessions"] == 6
    landing = by_key["/pricing | google | organic | 20260720"]
    assert landing.dataset == DATASET_GA4_LANDING_DAILY
    source_medium = by_key["google | organic | 20260720"]
    assert source_medium.dataset == DATASET_GA4_SOURCE_MEDIUM_DAILY

    # Sync lifecycle: started/finished events + last_synced_at + the C5
    # projection chain enqueued (one ingest per artifact + one refresh).
    connection = await db_session.get(IntegrationConnection, connection_id)
    assert connection.last_synced_at is not None
    events = list(
        (
            await db_session.scalars(
                select(IntegrationEvent).where(
                    IntegrationEvent.workspace_id == workspace_id
                )
            )
        ).all()
    )
    assert [event.event_type for event in events] == [
        EVENT_INTEGRATION_SYNC_STARTED,
        EVENT_INTEGRATION_SYNC_FINISHED,
    ]
    tasks = list((await db_session.scalars(select(AnalyticsTask))).all())
    ingest_tasks = [
        task
        for task in tasks
        if task.task_kind == ANALYTICS_TASK_KIND_INGEST_REFERRALS
    ]
    refresh_tasks = [
        task
        for task in tasks
        if task.task_kind == ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH
    ]
    assert len(ingest_tasks) == len(artifacts) == 5
    assert len(refresh_tasks) == 1
    assert refresh_tasks[0].payload == {
        "window_start": _WINDOW[0].isoformat(),
        "window_end": _WINDOW[1].isoformat(),
    }


@pytest.mark.asyncio
async def test_full_raw_page_with_dropped_row_still_pages_on(
    session_factory, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FULL raw page whose normalization dropped a row is NOT the last.

    Paging terminates on the provider's RAW row count, never the filtered
    count: the channel dataset's first page carries 2 raw rows, one with a
    non-numeric metric (dropped) — the worker must still request offset 2.
    """
    monkeypatch.setattr(integration_settings, "sync_page_size", 2)
    workspace_id, _project_id, _grant_id, connection_id = await _seed_graph(db_session)
    run = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection_id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )
    fake = _ProviderFake(drop_row=True)

    ran = await _worker(session_factory, fake.mock_transport()).run_until_idle()
    assert ran == 1
    await db_session.refresh(run)
    assert run.status == TASK_STATUS_SUCCEEDED

    channel_requests = [
        body
        for body in fake.ga4_requests
        if any(
            entry.get("name") == "sessionDefaultChannelGroup"
            for entry in body["dimensions"]
        )
    ]
    # Offset 2 WAS requested — the short normalized page (1 row) did not
    # terminate paging early.
    assert [body["offset"] for body in channel_requests] == [0, 2]

    artifacts = await _artifacts(db_session, run.id)
    channel = [
        artifact
        for artifact in artifacts
        if artifact.dataset == DATASET_GA4_CHANNEL_DAILY
    ]
    # row_count is the RAW provider count (the resume path's measure)...
    assert [a.row_count for a in channel] == [2, 1]
    # ...while the persisted payload keeps only the rows that normalized.
    assert [len(a.payload["rows"]) for a in channel] == [1, 1]


@pytest.mark.asyncio
async def test_retry_resumes_past_durable_page_with_dropped_row(
    session_factory, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resume path reads the same RAW count the live loop uses.

    A durable first page with raw ``row_count == page_size`` but fewer
    normalized payload rows (a dropped malformed row) resumes at the next
    offset instead of being mistaken for a complete short page.
    """
    monkeypatch.setattr(integration_settings, "sync_page_size", 2)
    workspace_id, _project_id, _grant_id, connection_id = await _seed_graph(db_session)
    run = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection_id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )
    # Simulate a crashed first attempt: channel page 0 is already durable
    # with the raw count (2) vs one normalized payload row.
    normalized_page1 = {
        "rows": [
            {
                "keys": ["Organic Search", "20260720"],
                "sessions": 41,
                "engagedSessions": 30,
                "conversions": 2,
            }
        ],
        "rowCount": 3,
    }
    db_session.add(
        IntegrationImportArtifact(
            sync_run_id=run.id,
            connection_id=connection_id,
            workspace_id=workspace_id,
            provider=INTEGRATION_PROVIDER_GA4,
            dataset=DATASET_GA4_CHANNEL_DAILY,
            query_snapshot={
                "api_method": "runReport",
                "dataset": DATASET_GA4_CHANNEL_DAILY,
                "property_ref": _PROPERTY_REF,
                "startDate": _WINDOW[0].isoformat(),
                "endDate": _WINDOW[1].isoformat(),
                "dimensions": ["sessionDefaultChannelGroup", "date"],
                "metrics": ["sessions", "engagedSessions", "conversions"],
                "rowLimit": 2,
                "startRow": 0,
            },
            payload_hash=_canonical_hash(normalized_page1),
            row_count=2,
            payload=normalized_page1,
        )
    )
    await db_session.commit()
    fake = _ProviderFake(drop_row=True)

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_SUCCEEDED
    channel_requests = [
        body
        for body in fake.ga4_requests
        if any(
            entry.get("name") == "sessionDefaultChannelGroup"
            for entry in body["dimensions"]
        )
    ]
    # Page 0 was NOT refetched and the dataset was NOT declared complete:
    # exactly one channel request, at the resumed offset.
    assert [body["offset"] for body in channel_requests] == [2]


@pytest.mark.asyncio
async def test_empty_report_pages_write_empty_artifacts(
    session_factory, db_session
) -> None:
    workspace_id, _project_id, _grant_id, connection_id = await _seed_graph(db_session)
    run = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection_id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )
    fake = _ProviderFake(empty=True)

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_SUCCEEDED
    artifacts = await _artifacts(db_session, run.id)
    assert len(artifacts) == len(_GA4_DATASETS)
    assert all(artifact.row_count == 0 for artifact in artifacts)
    assert await _metric_rows(db_session, run.id) == []
    # A fresh (non-expired) grant token was used; no refresh happened.
    assert fake.token_calls == []
    assert fake.ga4_auth == ["Bearer access-token-1"] * 4


@pytest.mark.asyncio
async def test_ga4_auth_failure_marks_grant_needs_reauth(
    session_factory, db_session
) -> None:
    workspace_id, _project_id, grant_id, connection_id = await _seed_graph(db_session)
    run = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection_id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )
    fake = _ProviderFake(ga4_status=401)

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_FAILED
    assert run.error_code == ERROR_GRANT_AUTH_FAILED
    grant = await db_session.get(IntegrationOAuthGrant, grant_id)
    assert grant.status == GRANT_STATUS_NEEDS_REAUTH
    events = list(
        (
            await db_session.scalars(
                select(IntegrationEvent).where(
                    IntegrationEvent.workspace_id == workspace_id
                )
            )
        ).all()
    )
    assert EVENT_INTEGRATION_REAUTH_REQUIRED in [e.event_type for e in events]
    # Nothing derived, nothing projected downstream.
    assert await _metric_rows(db_session, run.id) == []
    assert list((await db_session.scalars(select(AnalyticsTask))).all()) == []
