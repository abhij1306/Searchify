"""Component tests for the integrations connection-management API (I4).

Covers ``GET /integrations`` (list DTO joined to grant status + scopes, no
token fields — invariant 6), ``POST /integrations/{id}/test`` (probe
ok/failed through the injected fake provider transport, append-only event),
and ``DELETE /integrations/{id}`` (shared-grant semantics: a non-last delete
keeps the grant live; a last-connection delete revokes remotely on success,
parks the grant in ``pending_revocation`` with tokens retained on failure,
and takes the documented local-only path for Microsoft grants).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app.connectors.integrations import bing as bing_connector
from app.connectors.integrations import oauth as integration_oauth
from app.core.security import decrypt_secret, encrypt_secret
from app.models.integrations import (
    IntegrationConnection,
    IntegrationEvent,
    IntegrationOAuthGrant,
)
from app.models.workspace import Workspace

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "integrations"
_BASE = "/api/v1/integrations"

_FAKE_ACCESS = "fake-seed-access-token"  # pragma: allowlist secret
_FAKE_REFRESH = "fake-seed-refresh-token"  # pragma: allowlist secret

_LIST_KEYS = {
    "id",
    "workspace_id",
    "grant_id",
    "provider",
    "label",
    "account_ref",
    "grant_status",
    "granted_scopes",
    "last_synced_at",
    "created_at",
    "updated_at",
}
_TEST_KEYS = {"connection_id", "status", "error_code", "detail", "tested_at"}


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


async def _register(client: httpx.AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201


async def _workspace_id(db_session) -> uuid.UUID:
    return (await db_session.execute(select(Workspace))).scalars().first().id


async def _seed_grant(
    db_session,
    *,
    workspace_id: uuid.UUID,
    transport: str = "google_oauth",
    providers: tuple[str, ...] = ("gsc", "ga4"),
) -> tuple[IntegrationOAuthGrant, list[IntegrationConnection]]:
    grant = IntegrationOAuthGrant(
        workspace_id=workspace_id,
        transport=transport,
        access_token_encrypted=encrypt_secret(_FAKE_ACCESS),
        refresh_token_encrypted=encrypt_secret(_FAKE_REFRESH),
        token_expires_at=datetime.now(UTC) + timedelta(hours=1),
        granted_scopes=["scope-a", "scope-b"],
        status="connected",
    )
    db_session.add(grant)
    await db_session.flush()
    connections = [
        IntegrationConnection(
            workspace_id=workspace_id,
            grant_id=grant.id,
            provider=provider,
            label=f"{provider} label",
            account_ref=f"{provider}-account-ref",
        )
        for provider in providers
    ]
    db_session.add_all(connections)
    await db_session.commit()
    return grant, connections


class _FakeProvider:
    """MockTransport-backed fake provider with configurable outcomes."""

    def __init__(self, *, probe_status: int = 200, revoke_status: int = 200) -> None:
        self.probe_status = probe_status
        self.revoke_status = revoke_status
        self.requests: list[httpx.Request] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "www.googleapis.com":
            return httpx.Response(
                self.probe_status, json=_fixture("gsc_sites_response.json")
            )
        if host == "ssl.bing.com":
            return httpx.Response(
                self.probe_status, json=_fixture("bing_sites_response.json")
            )
        if host == "oauth2.googleapis.com" and request.url.path == "/revoke":
            return httpx.Response(self.revoke_status)
        if host == "login.microsoftonline.com" and request.url.path.endswith("/token"):
            return httpx.Response(200, json=_fixture("microsoft_token_response.json"))
        return httpx.Response(404, json={"error": "unexpected"})

    def probe_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "www.googleapis.com"]

    def bing_probe_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "ssl.bing.com"]

    def revoke_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/revoke"]

    def token_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/token")]


@pytest.fixture
def _fake_provider(monkeypatch: pytest.MonkeyPatch) -> _FakeProvider:
    fake = _FakeProvider()

    def _build(transport_kind: str, *, transport=None):
        return integration_oauth.IntegrationOAuthClient(
            transport_kind, transport=fake.transport
        )

    def _build_bing(*, transport=None):
        return bing_connector.BingClient(transport=fake.transport)

    monkeypatch.setattr(integration_oauth, "build_oauth_client", _build)
    monkeypatch.setattr(bing_connector, "build_bing_client", _build_bing)
    return fake


async def _events(db_session) -> list[IntegrationEvent]:
    return list((await db_session.execute(select(IntegrationEvent))).scalars())


@pytest.mark.asyncio
async def test_list_returns_grant_joined_dto_without_tokens(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "mgmt-list@example.com")
    ws = await _workspace_id(db_session)
    grant, connections = await _seed_grant(db_session, workspace_id=ws)

    resp = await client.get(_BASE)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    by_provider = {row["provider"]: row for row in body}
    assert set(by_provider) == {"gsc", "ga4"}
    for row in body:
        # Exact DTO shape (contract C6 strict schema) — no token fields.
        assert set(row) == _LIST_KEYS
        assert row["grant_status"] == "connected"
        assert row["granted_scopes"] == ["scope-a", "scope-b"]
        assert row["grant_id"] == str(grant.id)
        assert row["last_synced_at"] is None
    assert by_provider["gsc"]["account_ref"] == "gsc-account-ref"
    # Invariant 6: neither token value nor any token-ish key on the wire.
    assert _FAKE_ACCESS not in resp.text
    assert _FAKE_REFRESH not in resp.text
    assert "_token" not in resp.text


@pytest.mark.asyncio
async def test_list_is_workspace_scoped(client: httpx.AsyncClient, db_session) -> None:
    await _register(client, "mgmt-owner@example.com")
    ws = await _workspace_id(db_session)
    await _seed_grant(db_session, workspace_id=ws)

    await client.post("/api/v1/auth/logout")
    await _register(client, "mgmt-intruder@example.com")
    resp = await client.get(_BASE)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_probe_ok_google(
    client: httpx.AsyncClient,
    db_session,
    _fake_provider: _FakeProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _register(client, "mgmt-test-ok@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = next(c for c in connections if c.provider == "gsc")

    with caplog.at_level(logging.DEBUG):
        resp = await client.post(f"{_BASE}/{gsc.id}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _TEST_KEYS
    assert body["connection_id"] == str(gsc.id)
    assert body["status"] == "ok"
    assert body["error_code"] == ""

    # The probe ran once with the decrypted Bearer token (decrypt-in-place).
    (probe,) = _fake_provider.probe_calls()
    assert probe.headers["authorization"] == f"Bearer {_FAKE_ACCESS}"
    # The token never reaches a response or log line (invariant 6).
    assert _FAKE_ACCESS not in resp.text
    assert _FAKE_ACCESS not in caplog.text
    assert _FAKE_REFRESH not in caplog.text

    events = await _events(db_session)
    assert [e.event_type for e in events] == ["integration.tested"]
    assert events[0].connection_id == gsc.id
    assert events[0].payload["status"] == "ok"


@pytest.mark.asyncio
async def test_probe_ok_microsoft_via_bing_get_sites(
    client: httpx.AsyncClient, db_session, _fake_provider: _FakeProvider
) -> None:
    await _register(client, "mgmt-test-ms@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(
        db_session, workspace_id=ws, transport="microsoft_oauth", providers=("bing",)
    )
    (bing,) = connections
    resp = await client.post(f"{_BASE}/{bing.id}/test")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    # Microsoft grants probe through a real authenticated call against the
    # pinned Bing host (GetSites — I12), never a token-endpoint round-trip.
    (probe,) = _fake_provider.bing_probe_calls()
    assert probe.url.path.endswith("/GetSites")
    assert probe.headers["authorization"] == f"Bearer {_FAKE_ACCESS}"
    assert _fake_provider.token_calls() == []
    # The stored grant is untouched by the probe (no rotation persisted).
    grant = (await db_session.execute(select(IntegrationOAuthGrant))).scalar_one()
    assert decrypt_secret(grant.refresh_token_encrypted) == _FAKE_REFRESH
    assert grant.status == "connected"


@pytest.mark.asyncio
async def test_probe_failed_microsoft_maps_grant_auth_failed(
    client: httpx.AsyncClient, db_session, _fake_provider: _FakeProvider
) -> None:
    await _register(client, "mgmt-test-ms-401@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(
        db_session, workspace_id=ws, transport="microsoft_oauth", providers=("bing",)
    )
    (bing,) = connections
    _fake_provider.probe_status = 401
    resp = await client.post(f"{_BASE}/{bing.id}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "grant_auth_failed"
    events = await _events(db_session)
    assert [e.event_type for e in events] == ["integration.tested"]
    assert events[0].payload["error_code"] == "grant_auth_failed"


@pytest.mark.asyncio
async def test_probe_failed_records_event(
    client: httpx.AsyncClient, db_session, _fake_provider: _FakeProvider
) -> None:
    await _register(client, "mgmt-test-fail@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = next(c for c in connections if c.provider == "gsc")

    _fake_provider.probe_status = 401
    resp = await client.post(f"{_BASE}/{gsc.id}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == _TEST_KEYS
    assert body["status"] == "failed"
    assert body["error_code"] == "grant_auth_failed"
    events = await _events(db_session)
    assert [e.event_type for e in events] == ["integration.tested"]
    assert events[0].payload["error_code"] == "grant_auth_failed"


@pytest.mark.asyncio
async def test_probe_provider_error_maps_to_provider_api_error(
    client: httpx.AsyncClient, db_session, _fake_provider: _FakeProvider
) -> None:
    await _register(client, "mgmt-test-500@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    ga4 = next(c for c in connections if c.provider == "ga4")
    _fake_provider.probe_status = 500
    resp = await client.post(f"{_BASE}/{ga4.id}/test")
    assert resp.json()["status"] == "failed"
    assert resp.json()["error_code"] == "provider_api_error"


@pytest.mark.asyncio
async def test_delete_non_last_connection_keeps_shared_grant_live(
    client: httpx.AsyncClient,
    db_session,
    _fake_provider: _FakeProvider,
) -> None:
    await _register(client, "mgmt-del-gsc@example.com")
    ws = await _workspace_id(db_session)
    grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = next(c for c in connections if c.provider == "gsc")

    resp = await client.delete(f"{_BASE}/{gsc.id}")
    assert resp.status_code == 204

    # The shared Google grant stays live for GA4 — tokens retained, no
    # remote revoke attempted.
    assert _fake_provider.revoke_calls() == []
    await db_session.refresh(grant)
    assert grant.status == "connected"
    assert decrypt_secret(grant.access_token_encrypted) == _FAKE_ACCESS
    assert decrypt_secret(grant.refresh_token_encrypted) == _FAKE_REFRESH

    remaining = list(
        (await db_session.execute(select(IntegrationConnection))).scalars()
    )
    assert [c.provider for c in remaining] == ["ga4"]
    listed = await client.get(_BASE)
    assert [row["provider"] for row in listed.json()] == ["ga4"]

    events = await _events(db_session)
    assert [e.event_type for e in events] == ["integration.disconnected"]
    assert events[0].payload["grant_retained"] is True
    assert events[0].payload["provider"] == "gsc"


@pytest.mark.asyncio
async def test_delete_last_connection_revokes_grant_and_drops_tokens(
    client: httpx.AsyncClient,
    db_session,
    _fake_provider: _FakeProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _register(client, "mgmt-del-last@example.com")
    ws = await _workspace_id(db_session)
    grant, connections = await _seed_grant(
        db_session, workspace_id=ws, providers=("gsc",)
    )
    (gsc,) = connections

    with caplog.at_level(logging.DEBUG):
        resp = await client.delete(f"{_BASE}/{gsc.id}")
    assert resp.status_code == 204

    # The long-lived refresh token is what gets revoked remotely.
    (revoke_call,) = _fake_provider.revoke_calls()
    form = parse_qs(revoke_call.content.decode("utf-8"))
    assert form["token"] == [_FAKE_REFRESH]

    await db_session.refresh(grant)
    assert grant.status == "revoked"
    assert grant.access_token_encrypted == ""
    assert grant.refresh_token_encrypted == ""
    assert grant.token_expires_at is None
    events = await _events(db_session)
    assert [e.event_type for e in events] == ["integration.revoked"]
    assert events[0].payload["remote_revoke"] is True
    # The revoked token never reaches a log line (invariant 6).
    assert _FAKE_REFRESH not in caplog.text
    assert _FAKE_ACCESS not in caplog.text


@pytest.mark.asyncio
async def test_delete_last_connection_revoke_failure_parks_grant(
    client: httpx.AsyncClient, db_session, _fake_provider: _FakeProvider
) -> None:
    await _register(client, "mgmt-del-fail@example.com")
    ws = await _workspace_id(db_session)
    grant, connections = await _seed_grant(
        db_session, workspace_id=ws, providers=("gsc",)
    )
    (gsc,) = connections
    _fake_provider.revoke_status = 500

    resp = await client.delete(f"{_BASE}/{gsc.id}")
    assert resp.status_code == 204

    # Connection removed locally; tokens RETAINED and the grant parked in
    # pending_revocation so a later retry can finish the remote revoke.
    remaining = list(
        (await db_session.execute(select(IntegrationConnection))).scalars()
    )
    assert remaining == []
    await db_session.refresh(grant)
    assert grant.status == "pending_revocation"
    assert decrypt_secret(grant.access_token_encrypted) == _FAKE_ACCESS
    assert decrypt_secret(grant.refresh_token_encrypted) == _FAKE_REFRESH
    events = await _events(db_session)
    assert [e.event_type for e in events] == ["integration.revoke_failed"]
    assert events[0].payload["error_code"] == "provider_api_error"


@pytest.mark.asyncio
async def test_delete_bing_uses_local_only_revocation(
    client: httpx.AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _register(client, "mgmt-del-bing@example.com")
    ws = await _workspace_id(db_session)
    grant, connections = await _seed_grant(
        db_session, workspace_id=ws, transport="microsoft_oauth", providers=("bing",)
    )
    (bing,) = connections

    def _no_http(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Microsoft grants must not attempt a remote revoke")

    def _build(transport_kind: str, *, transport=None):
        return integration_oauth.IntegrationOAuthClient(
            transport_kind, transport=httpx.MockTransport(_no_http)
        )

    monkeypatch.setattr(integration_oauth, "build_oauth_client", _build)

    resp = await client.delete(f"{_BASE}/{bing.id}")
    assert resp.status_code == 204
    await db_session.refresh(grant)
    assert grant.status == "revoked"
    assert grant.access_token_encrypted == ""
    assert grant.refresh_token_encrypted == ""
    events = await _events(db_session)
    assert [e.event_type for e in events] == ["integration.revoked"]
    assert events[0].payload["remote_revoke"] is False


@pytest.mark.asyncio
async def test_cross_workspace_test_and_delete_are_404(
    client: httpx.AsyncClient, db_session, _fake_provider: _FakeProvider
) -> None:
    await _register(client, "mgmt-xws-owner@example.com")
    ws = await _workspace_id(db_session)
    _grant, connections = await _seed_grant(db_session, workspace_id=ws)
    gsc = connections[0]

    await client.post("/api/v1/auth/logout")
    await _register(client, "mgmt-xws-intruder@example.com")
    tested = await client.post(f"{_BASE}/{gsc.id}/test")
    assert tested.status_code == 404
    deleted = await client.delete(f"{_BASE}/{gsc.id}")
    assert deleted.status_code == 404
    # Nothing reached the provider; nothing was written.
    assert _fake_provider.requests == []
    assert await _events(db_session) == []


@pytest.mark.asyncio
async def test_unauthenticated_management_rejected(client: httpx.AsyncClient) -> None:
    some_id = uuid.uuid4()
    assert (await client.get(_BASE)).status_code == 401
    assert (await client.post(f"{_BASE}/{some_id}/test")).status_code == 401
    assert (await client.delete(f"{_BASE}/{some_id}")).status_code == 401
