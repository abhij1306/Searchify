"""Component tests for the integration sync worker (I6).

Runs the real ``IntegrationWorker`` against a live Postgres schema with an
injected fake Google OAuth + GSC API (``httpx.MockTransport`` or an async
scripted transport; recorded fixtures). Covers:

  - Fixture-driven import: one immutable ``IntegrationImportArtifact`` per
    fetched page with a sha256 ``payload_hash`` + credential-free
    ``query_snapshot``; sync started/finished events; ``last_synced_at``.
  - Serialized-per-grant refresh: two workers on ONE grant perform exactly
    one remote token refresh (spec section 2).
  - Lost lease (before the work starts AND mid-run) writes NOTHING.
  - Cooperative cancel at the page boundary: earlier pages stay immutable,
    nothing downstream is written.
  - Provider/refresh error taxonomy: retryable backoff, budget-exhausted
    terminal failure, auth failure marking the grant ``needs_reauth``.
  - Retry RESUME: durable artifacts are never refetched.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.config.analytics import (
    ANALYTICS_TASK_KIND_INGEST_REFERRALS,
    ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH,
)
from app.core.config.integrations import (
    DATASET_GSC_PAGE_DAILY,
    DATASET_GSC_QUERY_DAILY,
    ERROR_GRANT_AUTH_FAILED,
    ERROR_PAYLOAD_TOO_LARGE,
    ERROR_PROVIDER_API,
    ERROR_RATE_LIMITED,
    ERROR_TOKEN_REFRESH_FAILED,
    EVENT_INTEGRATION_REAUTH_REQUIRED,
    EVENT_INTEGRATION_SYNC_FINISHED,
    EVENT_INTEGRATION_SYNC_STARTED,
    GRANT_STATUS_CONNECTED,
    GRANT_STATUS_NEEDS_REAUTH,
    INTEGRATION_PROVIDER_GA4,
    INTEGRATION_PROVIDER_GSC,
    INTEGRATION_QUEUE_SPEC,
    INTEGRATION_TRANSPORT_GOOGLE,
    integration_settings,
)
from app.core.config.task_queue import (
    ERROR_MAX_ATTEMPTS,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_LEASED,
    TASK_STATUS_RETRY_WAIT,
    TASK_STATUS_RUNNING,
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
    IntegrationSyncRun,
)
from app.models.project import Project
from app.models.workspace import Workspace
from app.orchestration.postgres_task_queue import PostgresTaskQueue
from app.workers.integration_worker import IntegrationWorker

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "integrations"
_WINDOW = (date(2026, 7, 20), date(2026, 7, 22))
_WINDOW_ALT = (date(2026, 7, 17), date(2026, 7, 19))
_PROPERTY_REF = "https://example.com"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _fast_pacing_and_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep request pacing out of the test timing budget and give the OAuth
    # refresh path env-injected client credentials (never logged).
    monkeypatch.setattr(integration_settings, "gsc_requests_per_minute", 60000)
    monkeypatch.setattr(settings, "integration_google_client_id", "test-client-id")
    monkeypatch.setattr(
        settings, "integration_google_client_secret", "test-client-secret"
    )


class _AsyncScriptedTransport(httpx.AsyncBaseTransport):
    """An async transport seam: the handler may perform DB side effects
    between pages (lease theft / cancellation mid-run)."""

    def __init__(self, handler) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._handler(request)


class _ProviderFake:
    """The fake Google OAuth + GSC API, routing by request host."""

    def __init__(
        self,
        *,
        token_status: int = 200,
        gsc_status: dict[str, int] | None = None,
        query_empty: bool = False,
    ) -> None:
        self.token_calls: list[httpx.Request] = []
        self.gsc_auth: list[str] = []
        self.gsc_pages: list[tuple[tuple[str, ...], int]] = []
        self._token_status = token_status
        self._gsc_status = gsc_status or {}
        self._query_empty = query_empty

    def _token_response(self) -> httpx.Response:
        if self._token_status != 200:
            return httpx.Response(
                self._token_status, json={"error": "temporarily_unavailable"}
            )
        return httpx.Response(
            200,
            json={
                "access_token": "fresh-access-token",
                "expires_in": 3600,
                "scope": "scope-a scope-b",
            },
        )

    def _gsc_response(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        dimensions = tuple(body.get("dimensions") or ())
        start_row = int(body.get("startRow") or 0)
        self.gsc_auth.append(request.headers.get("authorization", ""))
        self.gsc_pages.append((dimensions, start_row))
        dataset_key = "page" if "page" in dimensions else "query"
        status = self._gsc_status.get(dataset_key, 200)
        if status != 200:
            headers = {"Retry-After": "7"} if status == 429 else None
            return httpx.Response(
                status, json={"error": {"message": "provider boom"}}, headers=headers
            )
        if dataset_key == "query":
            payload = (
                {}
                if self._query_empty
                else _fixture("gsc_search_analytics_query_page.json")
            )
            return httpx.Response(200, json=payload)
        page_payloads = {
            0: _fixture("gsc_search_analytics_page1.json"),
            2: _fixture("gsc_search_analytics_page2.json"),
        }
        return httpx.Response(200, json=page_payloads.get(start_row, {}))

    def sync_handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            self.token_calls.append(request)
            return self._token_response()
        if request.url.host == "www.googleapis.com":
            return self._gsc_response(request)
        raise AssertionError(f"unexpected request: {request.url}")

    def mock_transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.sync_handler)


class _Seed:
    def __init__(
        self,
        *,
        workspace_id: uuid.UUID,
        project_id: uuid.UUID,
        grant_id: uuid.UUID,
        connection_id: uuid.UUID,
    ) -> None:
        self.workspace_id = workspace_id
        self.project_id = project_id
        self.grant_id = grant_id
        self.connection_id = connection_id


async def _seed_graph(
    db_session,
    *,
    provider: str = INTEGRATION_PROVIDER_GSC,
    grant_status: str = GRANT_STATUS_CONNECTED,
    with_mapping: bool = True,
    token_expires_at: datetime | None = None,
) -> _Seed:
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
        provider=provider,
        label=f"{provider} connection",
        account_ref=_PROPERTY_REF,
    )
    db_session.add(connection)
    await db_session.flush()
    if with_mapping:
        db_session.add(
            IntegrationPropertyMapping(
                workspace_id=workspace.id,
                connection_id=connection.id,
                provider=provider,
                property_ref=_PROPERTY_REF,
                project_id=project.id,
                status="active",
            )
        )
    await db_session.commit()
    return _Seed(
        workspace_id=workspace.id,
        project_id=project.id,
        grant_id=grant.id,
        connection_id=connection.id,
    )


async def _enqueue_run(
    db_session, seed: _Seed, window: tuple[date, date] = _WINDOW
) -> IntegrationSyncRun:
    return await enqueue_sync_run(
        db_session,
        workspace_id=seed.workspace_id,
        connection_id=seed.connection_id,
        window_start=window[0],
        window_end=window[1],
    )


def _worker(
    session_factory: async_sessionmaker[AsyncSession],
    transport: httpx.AsyncBaseTransport,
    *,
    owner: str = "test-worker",
) -> IntegrationWorker:
    return IntegrationWorker(
        session_factory=session_factory, owner=owner, transport=transport
    )


async def _artifacts(
    db_session, run_id: uuid.UUID
) -> list[IntegrationImportArtifact]:
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


async def _events(db_session, workspace_id: uuid.UUID) -> list[IntegrationEvent]:
    result = await db_session.scalars(
        select(IntegrationEvent)
        .where(IntegrationEvent.workspace_id == workspace_id)
        .order_by(IntegrationEvent.created_at.asc(), IntegrationEvent.id.asc())
    )
    return list(result)


def _canonical_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- Happy path -------------------------------------------------------------


@pytest.mark.asyncio
async def test_fixture_import_writes_immutable_artifacts(
    session_factory, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(integration_settings, "sync_page_size", 2)
    seed = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake()

    ran = await _worker(session_factory, fake.mock_transport()).run_until_idle()
    assert ran == 1

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_SUCCEEDED
    assert run.attempt_count == 1
    assert run.error_code == ""
    assert run.lease_owner is None
    assert run.completed_at is not None

    artifacts = await _artifacts(db_session, run.id)
    assert len(artifacts) == 3  # page dataset pages 0/2 + one query page
    by_dataset: dict[str, list[IntegrationImportArtifact]] = {}
    for artifact in artifacts:
        by_dataset.setdefault(artifact.dataset, []).append(artifact)
    assert sorted(by_dataset) == [DATASET_GSC_PAGE_DAILY, DATASET_GSC_QUERY_DAILY]
    page_artifacts = by_dataset[DATASET_GSC_PAGE_DAILY]
    assert [a.query_snapshot["startRow"] for a in page_artifacts] == [0, 2]
    assert [a.row_count for a in page_artifacts] == [2, 1]
    assert by_dataset[DATASET_GSC_QUERY_DAILY][0].row_count == 1

    for artifact in artifacts:
        # Immutable evidence: sha256 of the raw payload + fetch metadata.
        assert artifact.payload_hash == _canonical_hash(artifact.payload)
        assert len(artifact.payload_hash) == 64
        assert artifact.fetched_at is not None
        assert artifact.provider == INTEGRATION_PROVIDER_GSC
        # Credential-free query snapshot (invariant 6): exact key set, and
        # no credential-ish value anywhere in the serialized snapshot.
        assert set(artifact.query_snapshot) == {
            "api_method",
            "dataset",
            "property_ref",
            "startDate",
            "endDate",
            "dimensions",
            "metrics",
            "rowLimit",
            "startRow",
        }
        snapshot_text = json.dumps(artifact.query_snapshot).lower()
        assert "token" not in snapshot_text
        assert "authorization" not in snapshot_text
        assert artifact.query_snapshot["property_ref"] == _PROPERTY_REF
        assert artifact.query_snapshot["startDate"] == _WINDOW[0].isoformat()
        assert artifact.query_snapshot["endDate"] == _WINDOW[1].isoformat()

    # The sync finished: last_synced_at + started/finished events + the C5
    # projection chain enqueued (one ingest per artifact + one traffic
    # refresh for the window).
    connection = await db_session.get(IntegrationConnection, seed.connection_id)
    assert connection.last_synced_at is not None
    events = await _events(db_session, seed.workspace_id)
    event_types = [event.event_type for event in events]
    assert event_types == [
        EVENT_INTEGRATION_SYNC_STARTED,
        EVENT_INTEGRATION_SYNC_FINISHED,
    ]
    finished = events[-1]
    assert finished.payload["sync_run_id"] == str(run.id)
    assert finished.payload["row_count"] == 4
    assert finished.payload["metric_row_count"] == 4

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
    assert len(ingest_tasks) == 3
    assert {task.payload["import_artifact_id"] for task in ingest_tasks} == {
        str(artifact.id) for artifact in artifacts
    }
    assert len(refresh_tasks) == 1
    assert refresh_tasks[0].payload == {
        "window_start": _WINDOW[0].isoformat(),
        "window_end": _WINDOW[1].isoformat(),
    }
    assert all(task.project_id == seed.project_id for task in tasks)

    # The fresh (non-expired) access token was used; no refresh happened.
    assert fake.token_calls == []
    assert fake.gsc_auth == ["Bearer access-token-1"] * 3


@pytest.mark.asyncio
async def test_empty_result_page_writes_empty_artifact(
    session_factory, db_session
) -> None:
    seed = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake(query_empty=True)

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_SUCCEEDED
    artifacts = await _artifacts(db_session, run.id)
    by_dataset = {artifact.dataset: artifact for artifact in artifacts}
    empty = by_dataset[DATASET_GSC_QUERY_DAILY]
    assert empty.row_count == 0
    assert empty.payload == {}
    assert empty.payload_hash == _canonical_hash({})


# --- Serialized-per-grant refresh -------------------------------------------


@pytest.mark.asyncio
async def test_two_workers_one_grant_exactly_one_remote_refresh(
    session_factory, db_session
) -> None:
    near_expiry = datetime.now(UTC) + timedelta(seconds=5)
    seed = await _seed_graph(db_session, token_expires_at=near_expiry)
    run_a = await _enqueue_run(db_session, seed, _WINDOW)
    run_b = await _enqueue_run(db_session, seed, _WINDOW_ALT)
    fake = _ProviderFake()

    queue = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)
    claimed_a = await queue.claim(owner="worker-a", limit=1)
    claimed_b = await queue.claim(owner="worker-b", limit=1)
    assert {claimed_a[0].id, claimed_b[0].id} == {run_a.id, run_b.id}

    worker_a = _worker(session_factory, fake.mock_transport(), owner="worker-a")
    worker_b = _worker(session_factory, fake.mock_transport(), owner="worker-b")
    await asyncio.gather(
        worker_a._execute(claimed_a[0]), worker_b._execute(claimed_b[0])
    )

    # The grant row lock serialized the refresh: exactly ONE remote call.
    assert len(fake.token_calls) == 1
    # Both workers then used the fresh token for every provider call.
    assert fake.gsc_auth == ["Bearer fresh-access-token"] * 4

    for run_id in (run_a.id, run_b.id):
        run = await db_session.get(IntegrationSyncRun, run_id)
        await db_session.refresh(run)
        assert run.status == TASK_STATUS_SUCCEEDED

    grant = await db_session.get(IntegrationOAuthGrant, seed.grant_id)
    await db_session.refresh(grant)
    assert decrypt_secret(grant.access_token_encrypted) == "fresh-access-token"
    assert grant.token_expires_at > datetime.now(UTC) + timedelta(minutes=50)


# --- Lost lease / cancellation ------------------------------------------------


@pytest.mark.asyncio
async def test_lost_lease_before_start_writes_nothing(
    session_factory, db_session
) -> None:
    seed = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake()

    queue = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)
    claimed = await queue.claim(owner="worker-a", limit=1)
    # The sweeper steals the lease before the worker starts.
    await db_session.execute(
        update(IntegrationSyncRun)
        .where(IntegrationSyncRun.id == run.id)
        .values(lease_owner="worker-b")
    )
    await db_session.commit()

    worker_a = _worker(session_factory, fake.mock_transport(), owner="worker-a")
    await worker_a._execute(claimed[0])

    await db_session.refresh(run)
    assert run.lease_owner == "worker-b"
    assert run.status == TASK_STATUS_LEASED
    assert run.attempt_count == 0
    assert await _artifacts(db_session, run.id) == []
    assert await _metric_rows(db_session, run.id) == []
    assert await _events(db_session, seed.workspace_id) == []
    assert list((await db_session.scalars(select(AnalyticsTask))).all()) == []
    connection = await db_session.get(IntegrationConnection, seed.connection_id)
    assert connection.last_synced_at is None
    assert fake.token_calls == [] and fake.gsc_auth == []


@pytest.mark.asyncio
async def test_lost_lease_mid_run_writes_nothing(
    session_factory, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(integration_settings, "sync_page_size", 2)
    seed = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com":
            body = json.loads(request.content)
            if "page" in (body.get("dimensions") or []) and body.get("startRow") == 2:
                # Lease stolen mid-run (sweeper reclaim) during page 2.
                async with session_factory() as session:
                    await session.execute(
                        update(IntegrationSyncRun)
                        .where(IntegrationSyncRun.id == run.id)
                        .values(lease_owner="another-worker")
                    )
                    await session.commit()
            return fake._gsc_response(request)
        return fake.sync_handler(request)

    transport = _AsyncScriptedTransport(handler)
    await _worker(session_factory, transport).run_until_idle()

    await db_session.refresh(run)
    # The worker wrote nothing after the theft: the run stays owned by the
    # thief, un-finalized, with only the pre-theft page durable.
    assert run.lease_owner == "another-worker"
    assert run.status == TASK_STATUS_RUNNING
    assert run.completed_at is None
    artifacts = await _artifacts(db_session, run.id)
    assert len(artifacts) == 1
    assert artifacts[0].query_snapshot["startRow"] == 0
    assert await _metric_rows(db_session, run.id) == []
    assert list((await db_session.scalars(select(AnalyticsTask))).all()) == []
    events = await _events(db_session, seed.workspace_id)
    assert [event.event_type for event in events] == [EVENT_INTEGRATION_SYNC_STARTED]
    connection = await db_session.get(IntegrationConnection, seed.connection_id)
    assert connection.last_synced_at is None


@pytest.mark.asyncio
async def test_cancel_at_page_boundary(
    session_factory, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(integration_settings, "sync_page_size", 2)
    seed = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake()
    queue = PostgresTaskQueue(session_factory, INTEGRATION_QUEUE_SPEC)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com":
            body = json.loads(request.content)
            if "page" in (body.get("dimensions") or []) and body.get("startRow") == 2:
                # Cancellation lands while page 2 is in flight.
                await queue.cancel(task_id=run.id)
            return fake._gsc_response(request)
        return fake.sync_handler(request)

    transport = _AsyncScriptedTransport(handler)
    await _worker(session_factory, transport).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_CANCELLED
    # The page fetched BEFORE the cancel stays as immutable evidence; the
    # in-flight page was discarded and nothing downstream was written.
    artifacts = await _artifacts(db_session, run.id)
    assert len(artifacts) == 1
    assert artifacts[0].query_snapshot["startRow"] == 0
    assert artifacts[0].payload_hash == _canonical_hash(artifacts[0].payload)
    assert await _metric_rows(db_session, run.id) == []
    assert list((await db_session.scalars(select(AnalyticsTask))).all()) == []
    events = await _events(db_session, seed.workspace_id)
    assert [event.event_type for event in events] == [EVENT_INTEGRATION_SYNC_STARTED]
    connection = await db_session.get(IntegrationConnection, seed.connection_id)
    assert connection.last_synced_at is None


# --- Error taxonomy ----------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_5xx_retries_with_backoff(session_factory, db_session) -> None:
    seed = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake(gsc_status={"page": 500, "query": 500})

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_RETRY_WAIT
    assert run.error_code == ERROR_PROVIDER_API
    assert run.attempt_count == 1
    assert run.lease_owner is None
    assert run.available_at > datetime.now(UTC)
    assert await _artifacts(db_session, run.id) == []


@pytest.mark.asyncio
async def test_provider_error_terminal_after_budget(
    session_factory, db_session
) -> None:
    seed = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, seed)
    run.max_attempts = 1
    await db_session.commit()
    fake = _ProviderFake(gsc_status={"page": 500, "query": 500})

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_FAILED
    assert run.error_code == ERROR_MAX_ATTEMPTS
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_rate_limited_honors_retry_after(session_factory, db_session) -> None:
    seed = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake(gsc_status={"page": 429, "query": 429})

    before = datetime.now(UTC)
    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_RETRY_WAIT
    assert run.error_code == ERROR_RATE_LIMITED
    delay = (run.available_at - before).total_seconds()
    assert 5.0 < delay <= 8.0  # the Retry-After: 7 header, not the backoff


@pytest.mark.asyncio
async def test_data_api_auth_failure_marks_grant_needs_reauth(
    session_factory, db_session
) -> None:
    seed = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake(gsc_status={"page": 401, "query": 401})

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_FAILED
    assert run.error_code == ERROR_GRANT_AUTH_FAILED
    grant = await db_session.get(IntegrationOAuthGrant, seed.grant_id)
    assert grant.status == GRANT_STATUS_NEEDS_REAUTH
    assert grant.access_token_encrypted != ""  # tokens retained
    events = await _events(db_session, seed.workspace_id)
    assert [event.event_type for event in events] == [
        EVENT_INTEGRATION_SYNC_STARTED,
        EVENT_INTEGRATION_REAUTH_REQUIRED,
    ]


@pytest.mark.asyncio
async def test_refresh_failure_retries(session_factory, db_session) -> None:
    near_expiry = datetime.now(UTC) + timedelta(seconds=5)
    seed = await _seed_graph(db_session, token_expires_at=near_expiry)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake(token_status=500)

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_RETRY_WAIT
    assert run.error_code == ERROR_TOKEN_REFRESH_FAILED
    assert len(fake.token_calls) == 1
    assert fake.gsc_auth == []  # never reached the data API


@pytest.mark.asyncio
async def test_payload_too_large_fails(
    session_factory, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(integration_settings, "max_inline_payload_bytes", 10)
    seed = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake()

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_FAILED
    assert run.error_code == ERROR_PAYLOAD_TOO_LARGE
    assert await _artifacts(db_session, run.id) == []


@pytest.mark.asyncio
async def test_unsupported_provider_fails_clean(session_factory, db_session) -> None:
    seed = await _seed_graph(db_session, provider=INTEGRATION_PROVIDER_GA4)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake()

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_FAILED
    assert run.error_code == ERROR_PROVIDER_API
    assert run.attempt_count == 1  # terminal: no retry budget burned
    assert fake.gsc_auth == [] and fake.token_calls == []


@pytest.mark.asyncio
async def test_grant_not_connected_fails_without_provider_calls(
    session_factory, db_session
) -> None:
    seed = await _seed_graph(db_session, grant_status=GRANT_STATUS_NEEDS_REAUTH)
    run = await _enqueue_run(db_session, seed)
    fake = _ProviderFake()

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_FAILED
    assert run.error_code == ERROR_GRANT_AUTH_FAILED
    assert fake.gsc_auth == [] and fake.token_calls == []


# --- Retry resume --------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_resumes_from_durable_artifacts(
    session_factory, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry never refetches a persisted page (immutability + idempotency)."""
    monkeypatch.setattr(integration_settings, "sync_page_size", 2)
    seed = await _seed_graph(db_session)
    run = await _enqueue_run(db_session, seed)
    # Simulate a crashed first attempt: page 0 of the page dataset is
    # already durable (full page => the dataset resumes at startRow 2).
    page1 = _fixture("gsc_search_analytics_page1.json")
    db_session.add(
        IntegrationImportArtifact(
            sync_run_id=run.id,
            connection_id=seed.connection_id,
            workspace_id=seed.workspace_id,
            provider=INTEGRATION_PROVIDER_GSC,
            dataset=DATASET_GSC_PAGE_DAILY,
            query_snapshot={
                "api_method": "searchAnalytics.query",
                "dataset": DATASET_GSC_PAGE_DAILY,
                "property_ref": _PROPERTY_REF,
                "startDate": _WINDOW[0].isoformat(),
                "endDate": _WINDOW[1].isoformat(),
                "dimensions": ["page", "date"],
                "metrics": ["clicks", "impressions", "ctr", "position"],
                "rowLimit": 2,
                "startRow": 0,
            },
            payload_hash=_canonical_hash(page1),
            row_count=2,
            payload=page1,
        )
    )
    await db_session.commit()
    fake = _ProviderFake()

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_SUCCEEDED
    # Page 0 was NOT refetched: the only requests were page startRow 2 +
    # query startRow 0.
    assert sorted(fake.gsc_pages) == [(("page", "date"), 2), (("query", "date"), 0)]
    artifacts = await _artifacts(db_session, run.id)
    assert len(artifacts) == 3  # the pre-seeded page + two new ones
    assert len({artifact.id for artifact in artifacts}) == 3
