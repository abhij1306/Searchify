"""Component tests for the Bing (Microsoft OAuth) sync path (I12).

Runs the real ``IntegrationWorker`` against a live Postgres schema with an
injected fake Microsoft token endpoint + Bing Webmaster API
(``httpx.MockTransport``; recorded fixtures). Covers the full worker
contract for a ``bing`` connection on a ``microsoft_oauth`` grant:

  - claim -> serialized refresh against login.microsoftonline.com ->
    ``GetPageStats``/``GetQueryStats`` import on the pinned
    ``ssl.bing.com`` host -> derivation metric rows with provenance.
  - The Bing row mapping (``d``-array ``Query``/``Date``/``Clicks``/
    ``Impressions`` -> GSC-shaped ``keys`` + counts; the serialized
    ``/Date(ms)/`` form normalized to ISO) and the derivation WINDOW
    projection (the stats API takes no date range — out-of-window rows
    are kept on the artifact but never derived).
  - The unpaged-API short-circuit (a request past the first page returns
    an empty page).
  - The cheap authenticated ``GetSites`` probe (the I12 replacement for
    the refresh round-trip placeholder).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.integrations.bing import BingApiError, build_bing_client
from app.core.config import settings
from app.core.config.analytics import (
    ANALYTICS_TASK_KIND_INGEST_REFERRALS,
    ANALYTICS_TASK_KIND_TRAFFIC_SNAPSHOT_REFRESH,
)
from app.core.config.integrations import (
    DATASET_BING_PAGE_DAILY,
    DATASET_BING_QUERY_DAILY,
    ERROR_GRANT_AUTH_FAILED,
    EVENT_INTEGRATION_SYNC_FINISHED,
    EVENT_INTEGRATION_SYNC_STARTED,
    GRANT_STATUS_CONNECTED,
    INTEGRATION_IMPORTER_VERSION,
    INTEGRATION_PROVIDER_BING,
    INTEGRATION_TRANSPORT_MICROSOFT,
    integration_settings,
)
from app.core.config.task_queue import TASK_STATUS_SUCCEEDED
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
_PROPERTY_REF = "https://example.com"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def _fast_pacing_and_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep request pacing out of the test timing budget and give the OAuth
    # refresh path env-injected client credentials (never logged).
    monkeypatch.setattr(integration_settings, "bing_requests_per_minute", 60000)
    monkeypatch.setattr(
        settings, "integration_microsoft_client_id", "test-ms-client-id"
    )
    monkeypatch.setattr(
        settings, "integration_microsoft_client_secret", "test-ms-client-secret"
    )


class _ProviderFake:
    """The fake Microsoft token endpoint + Bing Webmaster API."""

    def __init__(self, *, bing_status: int = 200) -> None:
        self.token_calls: list[httpx.Request] = []
        self.bing_auth: list[str] = []
        self.bing_calls: list[tuple[str, str]] = []  # (method, siteUrl)
        self._bing_status = bing_status

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "login.microsoftonline.com":
            self.token_calls.append(request)
            return httpx.Response(
                200,
                json={
                    "access_token": "fresh-ms-access-token",
                    "refresh_token": "fresh-ms-refresh-token",
                    "expires_in": 3600,
                },
            )
        if host == "ssl.bing.com":
            method = request.url.path.rsplit("/", 1)[-1]
            query = parse_qs(urlsplit(str(request.url)).query)
            site_url = query.get("siteUrl", [""])[0]
            self.bing_auth.append(request.headers.get("authorization", ""))
            self.bing_calls.append((method, site_url))
            if self._bing_status != 200:
                return httpx.Response(self._bing_status, json={"Message": "bing boom"})
            if method == "GetPageStats":
                return httpx.Response(200, json=_fixture("bing_page_stats.json"))
            if method == "GetQueryStats":
                return httpx.Response(200, json=_fixture("bing_query_stats.json"))
            if method == "GetSites":
                return httpx.Response(200, json=_fixture("bing_sites_response.json"))
            return httpx.Response(404, json={"Message": "unknown method"})
        raise AssertionError(f"unexpected request: {request.url}")

    def mock_transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


async def _seed_graph(
    db_session,
    *,
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
        transport=INTEGRATION_TRANSPORT_MICROSOFT,
        access_token_encrypted=encrypt_secret("ms-access-token-1"),
        refresh_token_encrypted=encrypt_secret("ms-refresh-token-1"),
        token_expires_at=token_expires_at or (datetime.now(UTC) + timedelta(hours=1)),
        granted_scopes=["offline_access"],
        status=GRANT_STATUS_CONNECTED,
    )
    db_session.add(grant)
    await db_session.flush()
    connection = IntegrationConnection(
        workspace_id=workspace.id,
        grant_id=grant.id,
        provider=INTEGRATION_PROVIDER_BING,
        label="bing connection",
        account_ref=_PROPERTY_REF,
    )
    db_session.add(connection)
    await db_session.flush()
    db_session.add(
        IntegrationPropertyMapping(
            workspace_id=workspace.id,
            connection_id=connection.id,
            provider=INTEGRATION_PROVIDER_BING,
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
        session_factory=session_factory, owner="bing-test", transport=transport
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
    """claim -> Microsoft refresh -> Bing import -> derivation (windowed)."""
    # A small page size proves the unpaged-API short-circuit: the 3-row
    # page dataset runs the worker's next-page loop, which the client
    # answers with a LOCAL empty page (no second HTTP call is made).
    monkeypatch.setattr(integration_settings, "sync_page_size", 2)
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

    # Exactly ONE serialized refresh on the Microsoft grant, then every
    # Bing call carried the fresh Bearer token.
    (token_call,) = fake.token_calls
    form = parse_qs(token_call.content.decode("utf-8"))
    assert form["grant_type"] == ["refresh_token"]
    grant = await db_session.get(IntegrationOAuthGrant, grant_id)
    assert decrypt_secret(grant.access_token_encrypted) == "fresh-ms-access-token"
    assert fake.bing_auth == ["Bearer fresh-ms-access-token"] * 2
    # The pinned endpoints on the allow-listed host, siteUrl = the
    # connection's property ref: exactly ONE call per dataset (the second
    # page of the page dataset was the local short-circuit).
    assert fake.bing_calls == [
        ("GetPageStats", _PROPERTY_REF),
        ("GetQueryStats", _PROPERTY_REF),
    ]

    artifacts = await _artifacts(db_session, run.id)
    by_dataset: dict[str, list[IntegrationImportArtifact]] = {}
    for artifact in artifacts:
        by_dataset.setdefault(artifact.dataset, []).append(artifact)
    assert sorted(by_dataset) == [DATASET_BING_PAGE_DAILY, DATASET_BING_QUERY_DAILY]
    page_artifacts = by_dataset[DATASET_BING_PAGE_DAILY]
    assert [a.row_count for a in page_artifacts] == [3, 0]
    assert page_artifacts[1].payload == {"rows": []}
    assert by_dataset[DATASET_BING_QUERY_DAILY][0].row_count == 1

    for artifact in artifacts:
        assert artifact.payload_hash == _canonical_hash(artifact.payload)
        assert artifact.provider == INTEGRATION_PROVIDER_BING
        assert artifact.query_snapshot["api_method"] in (
            "GetPageStats",
            "GetQueryStats",
        )
        snapshot_text = json.dumps(artifact.query_snapshot).lower()
        assert "token" not in snapshot_text
        assert "authorization" not in snapshot_text
        for row in artifact.payload["rows"]:
            assert set(row) == {"keys", "clicks", "impressions"}
            assert isinstance(row["clicks"], int)
            assert isinstance(row["impressions"], int)

    # Derivation: the artifact keeps the FULL provider response (3 page
    # rows incl. the out-of-window June row) but only in-window rows are
    # derived — the stats API takes no date range, so the run's window is
    # enforced at projection time.
    rows = await _metric_rows(db_session, run.id)
    assert len(rows) == 3  # 2 in-window page rows + 1 query row
    artifact_ids = {artifact.id for artifact in artifacts}
    for row in rows:
        assert row.source_artifact_id in artifact_ids
        assert row.importer_version == INTEGRATION_IMPORTER_VERSION
        assert row.resync_seq == run.resync_seq == 0
        assert row.project_id == project_id
        assert row.provider == INTEGRATION_PROVIDER_BING
        assert _WINDOW[0] <= row.date <= _WINDOW[1]

    by_key = {row.dimension_key: row for row in rows}
    assert "https://example.com/old-post | 2026-06-15" not in by_key
    home = by_key["https://example.com/ | 2026-07-20"]
    assert home.dataset == DATASET_BING_PAGE_DAILY
    assert home.metrics == {"clicks": 12, "impressions": 340}
    pricing = by_key["https://example.com/pricing | 2026-07-21"]
    assert pricing.metrics == {"clicks": 5, "impressions": 120}
    query = by_key["searchify | 2026-07-20"]
    assert query.dataset == DATASET_BING_QUERY_DAILY
    assert query.metrics == {"clicks": 7, "impressions": 95}

    # Sync lifecycle: events + last_synced_at + the C5 chain enqueued.
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
    assert len(ingest_tasks) == len(artifacts) == 3
    assert len(refresh_tasks) == 1


@pytest.mark.asyncio
async def test_fresh_token_skips_refresh(session_factory, db_session) -> None:
    workspace_id, _project_id, _grant_id, connection_id = await _seed_graph(db_session)
    run = await enqueue_sync_run(
        db_session,
        workspace_id=workspace_id,
        connection_id=connection_id,
        window_start=_WINDOW[0],
        window_end=_WINDOW[1],
    )
    fake = _ProviderFake()

    await _worker(session_factory, fake.mock_transport()).run_until_idle()

    await db_session.refresh(run)
    assert run.status == TASK_STATUS_SUCCEEDED
    assert fake.token_calls == []
    assert fake.bing_auth == ["Bearer ms-access-token-1"] * 2


@pytest.mark.asyncio
async def test_probe_ok_and_auth_failure() -> None:
    """The cheap GetSites probe validates the token (I12 replacement)."""
    fake = _ProviderFake()
    client = build_bing_client(transport=fake.mock_transport())
    await client.probe_access_token(access_token="ms-access-token-1")
    assert fake.bing_calls == [("GetSites", "")]
    assert fake.bing_auth == ["Bearer ms-access-token-1"]

    failing = _ProviderFake(bing_status=401)
    client = build_bing_client(transport=failing.mock_transport())
    with pytest.raises(BingApiError) as excinfo:
        await client.probe_access_token(access_token="ms-access-token-1")
    assert excinfo.value.error_code == ERROR_GRANT_AUTH_FAILED
    assert excinfo.value.retryable is False
