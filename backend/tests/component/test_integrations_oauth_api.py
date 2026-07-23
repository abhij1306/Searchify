"""Component tests for the integrations OAuth connect API (I3).

Drives the real 302 start/callback flow through the ASGI app with a fake
OAuth server injected via ``httpx.MockTransport`` (the connector test seam).
Covers: the token round-trip onto the grant (Fernet-encrypted at rest), the
shared-grant shape (one Google consent ⇒ gsc + ga4 connections on ONE
grant), atomic one-time state consumption (replay rejected), cross-user and
cross-workspace state rejection, exchange-failure landing, the Microsoft
(Bing) transport, and that no token/client secret appears in any response or
log line (invariant 6).
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import func, select

from app.connectors.integrations import oauth as integration_oauth
from app.core.config import settings
from app.core.security import decrypt_secret
from app.models.integrations import (
    IntegrationConnection,
    IntegrationEvent,
    IntegrationOAuthGrant,
    IntegrationOAuthState,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "integrations"
_BASE = "/api/v1/integrations"

_GOOGLE_CLIENT_ID = "test-google-client-id"
_GOOGLE_CLIENT_SECRET = "test-google-client-secret"  # pragma: allowlist secret
_MS_CLIENT_ID = "test-ms-client-id"
_MS_CLIENT_SECRET = "test-ms-client-secret"  # pragma: allowlist secret

_GOOGLE_SCOPES = {
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
}


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _google_tokens() -> dict:
    return _fixture("google_token_response.json")


async def _register(client: httpx.AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201


@pytest.fixture
def _oauth_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "integration_google_client_id", _GOOGLE_CLIENT_ID)
    monkeypatch.setattr(
        settings, "integration_google_client_secret", _GOOGLE_CLIENT_SECRET
    )
    monkeypatch.setattr(settings, "integration_microsoft_client_id", _MS_CLIENT_ID)
    monkeypatch.setattr(
        settings, "integration_microsoft_client_secret", _MS_CLIENT_SECRET
    )


class _FakeOAuthServer:
    """MockTransport-backed fake OAuth server routing by host + path."""

    def __init__(
        self,
        *,
        google_token_status: int = 200,
        google_token_payload: dict | None = None,
        microsoft_token_status: int = 200,
    ) -> None:
        self.google_token_status = google_token_status
        self.google_token_payload = google_token_payload or _google_tokens()
        self.microsoft_token_status = microsoft_token_status
        self.requests: list[httpx.Request] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "oauth2.googleapis.com" and request.url.path == "/token":
            return httpx.Response(
                self.google_token_status, json=self.google_token_payload
            )
        if host == "oauth2.googleapis.com" and request.url.path == "/revoke":
            return httpx.Response(200)
        if host == "www.googleapis.com":
            return httpx.Response(200, json=_fixture("gsc_sites_response.json"))
        if host == "login.microsoftonline.com" and request.url.path.endswith("/token"):
            if self.microsoft_token_status != 200:
                return httpx.Response(
                    self.microsoft_token_status,
                    json={
                        "error": "temporarily_unavailable",
                        "error_description": "microsoft boom",
                    },
                )
            return httpx.Response(200, json=_fixture("microsoft_token_response.json"))
        return httpx.Response(404, json={"error": "unexpected"})

    def token_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/token")]


@pytest.fixture
def _fake_oauth(monkeypatch: pytest.MonkeyPatch) -> _FakeOAuthServer:
    """Inject the fake OAuth server into the domain service's client factory."""
    server = _FakeOAuthServer()

    def _build(transport_kind: str, *, transport=None):
        return integration_oauth.IntegrationOAuthClient(
            transport_kind, transport=server.transport
        )

    monkeypatch.setattr(integration_oauth, "build_oauth_client", _build)
    return server


async def _start(client: httpx.AsyncClient, provider: str, **kwargs) -> httpx.Response:
    resp = await client.get(f"{_BASE}/oauth/{provider}/start", **kwargs)
    assert resp.status_code == 302
    return resp


def _state_from_start(resp: httpx.Response) -> str:
    location = resp.headers["location"]
    return parse_qs(urlsplit(location).query)["state"][0]


async def _callback(
    client: httpx.AsyncClient, provider: str, state: str, code: str = "fake-auth-code"
) -> httpx.Response:
    return await client.get(
        f"{_BASE}/oauth/{provider}/callback",
        params={"code": code, "state": state},
    )


async def _grants(db_session) -> list[IntegrationOAuthGrant]:
    return list((await db_session.execute(select(IntegrationOAuthGrant))).scalars())


async def _connections(db_session) -> list[IntegrationConnection]:
    return list((await db_session.execute(select(IntegrationConnection))).scalars())


@pytest.mark.asyncio
async def test_google_connect_happy_path_shared_grant(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _register(client, "int-google@example.com")
    start = await _start(client, "gsc")
    location = start.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    query = parse_qs(urlsplit(location).query)
    assert query["client_id"] == [_GOOGLE_CLIENT_ID]
    assert query["redirect_uri"] == [
        "http://testserver/api/v1/integrations/oauth/gsc/callback"
    ]
    assert query["response_type"] == ["code"]
    assert set(query["scope"][0].split(" ")) == _GOOGLE_SCOPES
    assert query["access_type"] == ["offline"]
    state = query["state"][0]
    # The client secret never leaves the server (invariant 6).
    assert _GOOGLE_CLIENT_SECRET not in location

    # The state row is persisted, unconsumed, and bound to the workspace/user.
    state_row = (
        await db_session.execute(select(IntegrationOAuthState))
    ).scalar_one()
    assert state_row.consumed_at is None
    assert state_row.provider == "gsc"

    with caplog.at_level(logging.DEBUG):
        callback = await _callback(client, "gsc", state)
    assert callback.status_code == 302
    assert callback.headers["location"] == "/settings?tab=integrations&connected=gsc"

    # One grant carries the Fernet-encrypted tokens (never the plaintext).
    (grant,) = await _grants(db_session)
    assert grant.transport == "google_oauth"
    assert grant.status == "connected"
    expected = _google_tokens()
    assert grant.access_token_encrypted != expected["access_token"]
    assert decrypt_secret(grant.access_token_encrypted) == expected["access_token"]
    assert decrypt_secret(grant.refresh_token_encrypted) == expected["refresh_token"]
    assert grant.token_expires_at is not None
    assert set(grant.granted_scopes) == _GOOGLE_SCOPES

    # Shared-grant shape: one consent ⇒ gsc + ga4 rows on the ONE grant.
    connections = await _connections(db_session)
    assert {c.provider for c in connections} == {"gsc", "ga4"}
    assert {c.grant_id for c in connections} == {grant.id}
    assert {c.workspace_id for c in connections} == {grant.workspace_id}

    # The state row is consumed and the connect event appended.
    await db_session.refresh(state_row)
    assert state_row.consumed_at is not None
    events = list((await db_session.execute(select(IntegrationEvent))).scalars())
    assert [e.event_type for e in events] == ["integration.connected"]
    assert events[0].grant_id == grant.id
    assert sorted(events[0].payload["providers"]) == ["ga4", "gsc"]

    # Invariant 6: no token or client secret in any response or log line.
    forbidden = [
        expected["access_token"],
        expected["refresh_token"],
        _GOOGLE_CLIENT_SECRET,
    ]
    blob = caplog.text + start.text + callback.text
    for value in forbidden:
        assert value not in blob
    assert "access_token" not in callback.text


@pytest.mark.asyncio
async def test_replayed_state_rejected(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    await _register(client, "int-replay@example.com")
    state = _state_from_start(await _start(client, "gsc"))
    first = await _callback(client, "gsc", state)
    assert "connected=gsc" in first.headers["location"]

    replay = await _callback(client, "gsc", state)
    assert replay.status_code == 302
    assert (
        replay.headers["location"]
        == "/settings?tab=integrations&error=oauth_state_invalid"
    )
    # The exchange ran exactly once; the grant graph is unchanged.
    assert len(_fake_oauth.token_calls()) == 1
    assert len(await _grants(db_session)) == 1
    assert len(await _connections(db_session)) == 2


@pytest.mark.asyncio
async def test_cross_user_state_rejected(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    await _register(client, "int-owner@example.com")
    state = _state_from_start(await _start(client, "gsc"))

    # A different authenticated user must not consume the owner's state.
    await client.post("/api/v1/auth/logout")
    await _register(client, "int-intruder@example.com")
    callback = await _callback(client, "gsc", state)
    assert callback.status_code == 302
    assert (
        callback.headers["location"]
        == "/settings?tab=integrations&error=oauth_state_invalid"
    )
    assert _fake_oauth.token_calls() == []
    assert await _grants(db_session) == []


@pytest.mark.asyncio
async def test_workspace_comes_from_verified_state_not_client(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    await _register(client, "int-crossws@example.com")
    second = await client.post("/api/v1/workspaces", json={"name": "Second WS"})
    assert second.status_code == 201
    ws2 = second.json()["id"]

    # Start bound to the SECOND workspace via the active-workspace header.
    start = await _start(client, "gsc", headers={"X-Workspace-Id": ws2})
    state = _state_from_start(start)

    # The callback carries NO workspace selection: the grant must land on the
    # state-bound workspace, never on a client-influenced one (invariant 5).
    callback = await client.get(
        f"{_BASE}/oauth/gsc/callback",
        params={"code": "fake-auth-code", "state": state},
    )
    assert "connected=gsc" in callback.headers["location"]
    (grant,) = await _grants(db_session)
    assert str(grant.workspace_id) == ws2


@pytest.mark.asyncio
async def test_exchange_failure_landing_and_state_consumed(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    _fake_oauth.google_token_status = 400
    _fake_oauth.google_token_payload = _fixture("google_token_error.json")
    await _register(client, "int-exchange-fail@example.com")
    state = _state_from_start(await _start(client, "gsc"))
    callback = await _callback(client, "gsc", state)
    assert callback.status_code == 302
    assert (
        callback.headers["location"]
        == "/settings?tab=integrations&error=oauth_exchange_failed"
    )
    assert await _grants(db_session) == []
    # The state was consumed before the exchange — a retry is a replay.
    retry = await _callback(client, "gsc", state)
    assert "oauth_state_invalid" in retry.headers["location"]
    assert len(_fake_oauth.token_calls()) == 1


@pytest.mark.asyncio
async def test_provider_error_param_and_missing_params(
    client: httpx.AsyncClient,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    await _register(client, "int-params@example.com")
    denied = await client.get(
        f"{_BASE}/oauth/gsc/callback",
        params={"error": "access_denied", "state": "whatever"},
    )
    assert denied.status_code == 302
    assert "error=oauth_exchange_failed" in denied.headers["location"]

    missing = await client.get(f"{_BASE}/oauth/gsc/callback")
    assert missing.status_code == 302
    assert "error=oauth_state_invalid" in missing.headers["location"]
    assert _fake_oauth.token_calls() == []


@pytest.mark.asyncio
async def test_microsoft_connect_attaches_bing_connection(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    await _register(client, "int-bing@example.com")
    start = await _start(client, "bing")
    location = start.headers["location"]
    assert location.startswith(
        "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?"
    )
    query = parse_qs(urlsplit(location).query)
    assert query["client_id"] == [_MS_CLIENT_ID]
    # The pinned Bing Webmaster scope (I12) + offline_access for refresh.
    assert set(query["scope"][0].split(" ")) == {
        "offline_access",
        "https://webmaster.bing.com/api/webmaster.manage",
    }
    # Google-only offline/consent params are not sent to Microsoft.
    assert "access_type" not in query
    assert _MS_CLIENT_SECRET not in location

    callback = await _callback(client, "bing", _state_from_start(start))
    assert callback.headers["location"] == "/settings?tab=integrations&connected=bing"

    (grant,) = await _grants(db_session)
    assert grant.transport == "microsoft_oauth"
    assert grant.status == "connected"
    expected = _fixture("microsoft_token_response.json")
    assert decrypt_secret(grant.access_token_encrypted) == expected["access_token"]
    assert decrypt_secret(grant.refresh_token_encrypted) == expected["refresh_token"]
    assert set(grant.granted_scopes) == {
        "offline_access",
        "https://webmaster.bing.com/api/webmaster.manage",
    }
    connections = await _connections(db_session)
    assert [c.provider for c in connections] == ["bing"]
    assert connections[0].grant_id == grant.id


@pytest.mark.asyncio
async def test_bing_reconnect_keeps_single_grant_and_connection(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    """A second Bing consent rotates tokens on the ONE Microsoft grant."""
    await _register(client, "int-bing-reconnect@example.com")
    await _callback(client, "bing", _state_from_start(await _start(client, "bing")))
    (grant,) = await _grants(db_session)
    grant_id = grant.id

    state2 = _state_from_start(await _start(client, "bing"))
    callback2 = await _callback(client, "bing", state2)
    assert "connected=bing" in callback2.headers["location"]

    # Find-or-create: still ONE microsoft_oauth grant, ONE bing connection.
    grants = await _grants(db_session)
    assert [g.id for g in grants] == [grant_id]
    connections = await _connections(db_session)
    assert [c.provider for c in connections] == ["bing"]
    assert len(_fake_oauth.token_calls()) == 2


@pytest.mark.asyncio
async def test_bing_exchange_failure_landing(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    await _register(client, "int-bing-xfail@example.com")
    _fake_oauth.microsoft_token_status = 400
    state = _state_from_start(await _start(client, "bing"))
    callback = await _callback(client, "bing", state)
    assert callback.status_code == 302
    assert (
        callback.headers["location"]
        == "/settings?tab=integrations&error=oauth_exchange_failed"
    )
    assert await _grants(db_session) == []
    assert await _connections(db_session) == []
    # The state was consumed before the exchange — a retry is a replay.
    retry = await _callback(client, "bing", state)
    assert "oauth_state_invalid" in retry.headers["location"]


@pytest.mark.asyncio
async def test_reconnect_rotates_tokens_on_same_grant(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    await _register(client, "int-reconnect@example.com")
    state = _state_from_start(await _start(client, "gsc"))
    await _callback(client, "gsc", state)
    (grant,) = await _grants(db_session)
    grant_id = grant.id

    rotated = dict(_google_tokens())
    # pragma: allowlist secret
    rotated["access_token"] = "ya29.fake-rotated-access-token"
    _fake_oauth.google_token_payload = rotated
    state2 = _state_from_start(await _start(client, "gsc"))
    callback2 = await _callback(client, "gsc", state2)
    assert "connected=gsc" in callback2.headers["location"]

    # Find-or-create: still ONE grant, tokens rotated, no duplicate rows.
    grants = await _grants(db_session)
    assert [g.id for g in grants] == [grant_id]
    await db_session.refresh(grants[0])
    assert decrypt_secret(grants[0].access_token_encrypted) == rotated["access_token"]
    connections = await _connections(db_session)
    assert {c.provider for c in connections} == {"gsc", "ga4"}
    assert len(connections) == 2
    count = (
        await db_session.execute(select(func.count(IntegrationEvent.id)))
    ).scalar_one()
    assert count == 2


@pytest.mark.asyncio
async def test_unknown_provider_404(
    client: httpx.AsyncClient, _oauth_credentials: None
) -> None:
    await _register(client, "int-unknown@example.com")
    start = await client.get(f"{_BASE}/oauth/netscape/start")
    assert start.status_code == 404
    callback = await client.get(f"{_BASE}/oauth/netscape/callback")
    assert callback.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_flow_rejected(client: httpx.AsyncClient) -> None:
    start = await client.get(f"{_BASE}/oauth/gsc/start")
    assert start.status_code == 401
    callback = await client.get(
        f"{_BASE}/oauth/gsc/callback", params={"code": "x", "state": "y"}
    )
    assert callback.status_code == 401


@pytest.mark.asyncio
async def test_start_unconfigured_provider_503(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "integration_google_client_id", "")
    monkeypatch.setattr(settings, "integration_google_client_secret", "")
    await _register(client, "int-unconfigured@example.com")
    resp = await client.get(f"{_BASE}/oauth/gsc/start")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "oauth_not_configured"


@pytest.mark.asyncio
async def test_state_minted_for_one_provider_cannot_complete_another(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    await _register(client, "int-provider-mix@example.com")
    state = _state_from_start(await _start(client, "gsc"))
    callback = await _callback(client, "ga4", state)
    assert "error=oauth_state_invalid" in callback.headers["location"]
    assert _fake_oauth.token_calls() == []
    assert await _grants(db_session) == []
    # The state remains unconsumed — it is still valid for its own provider.
    ok = await _callback(client, "gsc", state)
    assert "connected=gsc" in ok.headers["location"]


@pytest.mark.asyncio
async def test_auth_scaffold_state_cannot_drive_connect(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    """A sign-in scaffold state (no jti/workspace/user claims) is rejected."""
    from app.core.security import create_oauth_state

    await _register(client, "int-scaffold@example.com")
    scaffold_state, _nonce = create_oauth_state("gsc")
    callback = await _callback(client, "gsc", scaffold_state)
    assert "error=oauth_state_invalid" in callback.headers["location"]
    assert _fake_oauth.token_calls() == []
    assert await _grants(db_session) == []


@pytest.mark.asyncio
async def test_state_rows_are_bound_per_mint(
    client: httpx.AsyncClient,
    db_session,
    _oauth_credentials: None,
    _fake_oauth: _FakeOAuthServer,
) -> None:
    """Two minted states have distinct jtis and each consumes exactly once."""
    await _register(client, "int-jti@example.com")
    state1 = _state_from_start(await _start(client, "gsc"))
    state2 = _state_from_start(await _start(client, "gsc"))
    assert state1 != state2
    rows = list((await db_session.execute(select(IntegrationOAuthState))).scalars())
    assert len(rows) == 2
    assert len({r.jti for r in rows}) == 2
    ok = await _callback(client, "gsc", state1)
    assert "connected=gsc" in ok.headers["location"]
    await db_session.refresh(rows[0])
    await db_session.refresh(rows[1])
    consumed = [r for r in (rows[0], rows[1]) if r.consumed_at is not None]
    assert len(consumed) == 1
    # uuid sanity: the grant id is a real UUID.
    (grant,) = await _grants(db_session)
    assert isinstance(grant.id, uuid.UUID)
