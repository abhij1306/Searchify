"""Component tests for the BYOK provider-connections API (v2 direct-only).

Covers the v2 acceptance:
  - connection CRUD is workspace-scoped (invariant 5);
  - the BYOK secret is encrypted at rest and NEVER present in any response DTO
    or log line (explicit redaction assertion, invariant 6);
  - the active surface is exactly the three direct transports
    ``{openai, anthropic, google}`` — a new OpenRouter connection is impossible;
  - a legacy OpenRouter connection reads safely but update/test are refused with
    409 before any decryption/network call (historical, read-only);
  - ``POST /{id}/test`` returns a status (transport mocked, no real spend);
  - ``GET /provider-catalog`` lists the direct transports/routes only.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from app.core.security import decrypt_secret

_SECRET = "sk-super-secret-byok-value-123456"


async def _register(client: httpx.AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201


def _connection_payload(**overrides: object) -> dict:
    payload: dict[str, Any] = {
        "label": "Prod OpenAI",
        "transport_provider": "openai",
        "api_key": _SECRET,
        "routes": [
            {"logical_engine": "chatgpt", "is_default": True},
        ],
    }
    payload.update(overrides)
    return payload


def _assert_no_secret(blob: object) -> None:
    """Fail if the raw secret or its ciphertext field leaks into a DTO.

    ``api_key_set`` (a boolean presence flag) is allowed; the raw ``api_key``
    value and the ``api_key_encrypted`` ciphertext column are not.
    """
    text = str(blob)
    assert _SECRET not in text
    assert "api_key_encrypted" not in text
    # The write-only "api_key" value field must never round-trip in a response.
    assert '"api_key"' not in text and "'api_key'" not in text


async def _resolve_workspace_id(db_session) -> object:
    from sqlalchemy import select

    from app.models.workspace import Workspace

    return (await db_session.execute(select(Workspace))).scalars().first().id


async def _seed_legacy_openrouter(db_session):
    """Insert a historical OpenRouter connection + route directly via the ORM.

    Mirrors what migration 0008 leaves behind: an inactive legacy connection
    with an inactive route. Returns the connection id.
    """
    from app.core.security import encrypt_secret
    from app.models.provider import ProviderConnection, ProviderRoute

    workspace_id = await _resolve_workspace_id(db_session)
    connection = ProviderConnection(
        workspace_id=workspace_id,
        label="Legacy OpenRouter",
        transport_provider="openrouter",
        api_key_encrypted=encrypt_secret(_SECRET),
        active=False,
        deactivation_reason="openrouter_retired_v2",
    )
    db_session.add(connection)
    await db_session.flush()
    db_session.add(
        ProviderRoute(
            workspace_id=workspace_id,
            connection_id=connection.id,
            logical_engine="chatgpt",
            transport_provider="openrouter",
            transport_model="openai/gpt-5.4",
            is_default=True,
            active=False,
            deactivation_reason="openrouter_retired_v2",
        )
    )
    await db_session.commit()
    return connection.id


@pytest.mark.asyncio
async def test_create_connection_redacts_secret_in_response(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "prov1@example.com")
    resp = await client.post("/api/v1/provider-connections", json=_connection_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert "-" in body["id"] and "-" in body["workspace_id"]
    assert body["transport_provider"] == "openai"
    assert body["api_key_set"] is True
    # Invariant 6: the secret and any key field are absent from the DTO.
    _assert_no_secret(body)
    # Provenance recorded on routes (invariant 10).
    engines = {r["logical_engine"]: r for r in body["routes"]}
    assert engines["chatgpt"]["transport_provider"] == "openai"
    assert engines["chatgpt"]["transport_model"] == "gpt-5.4"
    assert engines["chatgpt"]["is_default"] is True
    # New routes are active.
    assert engines["chatgpt"]["active"] is True


@pytest.mark.asyncio
async def test_create_openrouter_connection_rejected(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "prov-no-or@example.com")
    # openrouter is not an active transport → request validation (422).
    resp = await client.post(
        "/api/v1/provider-connections",
        json={
            "transport_provider": "openrouter",
            "api_key": _SECRET,
            "routes": [{"logical_engine": "chatgpt"}],
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_secret_encrypted_at_rest(client: httpx.AsyncClient, db_session) -> None:
    from sqlalchemy import select

    from app.models.provider import ProviderConnection

    await _register(client, "prov2@example.com")
    resp = await client.post("/api/v1/provider-connections", json=_connection_payload())
    assert resp.status_code == 201

    row = (await db_session.execute(select(ProviderConnection))).scalar_one()
    # Ciphertext at rest is NOT the plaintext, and decrypts back to it.
    assert row.api_key_encrypted != _SECRET
    assert _SECRET not in row.api_key_encrypted
    assert decrypt_secret(row.api_key_encrypted) == _SECRET


@pytest.mark.asyncio
async def test_list_and_get_never_return_secret(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "prov3@example.com")
    await client.post("/api/v1/provider-connections", json=_connection_payload())
    listed = await client.get("/api/v1/provider-connections")
    assert listed.status_code == 200
    _assert_no_secret(listed.json())
    assert listed.json()[0]["api_key_set"] is True


@pytest.mark.asyncio
async def test_update_rotates_key_without_exposing_it(
    client: httpx.AsyncClient, db_session
) -> None:
    from sqlalchemy import select

    from app.models.provider import ProviderConnection

    await _register(client, "prov4@example.com")
    created = await client.post(
        "/api/v1/provider-connections", json=_connection_payload()
    )
    conn_id = created.json()["id"]

    new_secret = "sk-rotated-key-987654321"
    resp = await client.patch(
        f"/api/v1/provider-connections/{conn_id}",
        json={"label": "Renamed", "api_key": new_secret},
    )
    assert resp.status_code == 200
    assert resp.json()["label"] == "Renamed"
    _assert_no_secret(resp.json())

    row = (await db_session.execute(select(ProviderConnection))).scalar_one()
    assert decrypt_secret(row.api_key_encrypted) == new_secret


@pytest.mark.asyncio
async def test_update_without_key_leaves_secret_unchanged(
    client: httpx.AsyncClient, db_session
) -> None:
    from sqlalchemy import select

    from app.models.provider import ProviderConnection

    await _register(client, "prov5@example.com")
    created = await client.post(
        "/api/v1/provider-connections", json=_connection_payload()
    )
    conn_id = created.json()["id"]
    await client.patch(
        f"/api/v1/provider-connections/{conn_id}",
        json={"active": False},
    )
    row = (await db_session.execute(select(ProviderConnection))).scalar_one()
    assert decrypt_secret(row.api_key_encrypted) == _SECRET
    assert row.active is False


@pytest.mark.asyncio
async def test_delete_connection(client: httpx.AsyncClient) -> None:
    await _register(client, "prov6@example.com")
    created = await client.post(
        "/api/v1/provider-connections", json=_connection_payload()
    )
    conn_id = created.json()["id"]
    resp = await client.delete(f"/api/v1/provider-connections/{conn_id}")
    assert resp.status_code == 204
    listed = await client.get("/api/v1/provider-connections")
    assert listed.json() == []


@pytest.mark.asyncio
async def test_invalid_route_rejected(client: httpx.AsyncClient) -> None:
    await _register(client, "prov7@example.com")
    # chatgpt is served ONLY via openai now; chatgpt over anthropic is not
    # an approved route.
    resp = await client.post(
        "/api/v1/provider-connections",
        json={
            "transport_provider": "anthropic",
            "api_key": _SECRET,
            "routes": [{"logical_engine": "chatgpt"}],
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_test_endpoint_returns_status_success(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    await _register(client, "prov8@example.com")
    created = await client.post(
        "/api/v1/provider-connections", json=_connection_payload()
    )
    conn_id = created.json()["id"]

    # Mock the transport so no real API call is made.
    from app.connectors.answer_engines import openai as openai_mod

    _real_client = openai_mod.httpx.AsyncClient

    def _fake_client(*args, **kwargs):  # noqa: ANN002, ANN003
        payload = {
            "id": "resp-x",
            "object": "response",
            "status": "completed",
            "model": "gpt-5.4",
            "output": [
                {
                    "type": "message",
                    "id": "m",
                    "content": [{"type": "output_text", "text": "ok"}],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        kwargs["transport"] = httpx.MockTransport(handler)
        return _real_client(*args, **kwargs)

    monkeypatch.setattr(openai_mod.httpx, "AsyncClient", _fake_client)

    resp = await client.post(f"/api/v1/provider-connections/{conn_id}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["connection_id"] == conn_id
    # Provenance of the probe is recorded (invariant 10).
    assert body["transport_provider"] == "openai"
    assert body["logical_engine"] == "chatgpt"
    _assert_no_secret(body)


@pytest.mark.asyncio
async def test_test_endpoint_reports_failure_and_redacts_logs(
    client: httpx.AsyncClient, monkeypatch, caplog
) -> None:
    await _register(client, "prov9@example.com")
    created = await client.post(
        "/api/v1/provider-connections", json=_connection_payload()
    )
    conn_id = created.json()["id"]

    from app.connectors.answer_engines import openai as openai_mod

    _real_client = openai_mod.httpx.AsyncClient

    def _fake_client(*args, **kwargs):  # noqa: ANN002, ANN003
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        kwargs["transport"] = httpx.MockTransport(handler)
        return _real_client(*args, **kwargs)

    monkeypatch.setattr(openai_mod.httpx, "AsyncClient", _fake_client)

    with caplog.at_level(logging.DEBUG):
        resp = await client.post(f"/api/v1/provider-connections/{conn_id}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "auth_failure"
    # Invariant 6: the secret never appears in the response or any log line.
    _assert_no_secret(body)
    assert _SECRET not in caplog.text


@pytest.mark.asyncio
async def test_legacy_openrouter_reads_but_update_and_test_are_409(
    client: httpx.AsyncClient, db_session, monkeypatch
) -> None:
    await _register(client, "prov-legacy@example.com")
    conn_id = await _seed_legacy_openrouter(db_session)

    # Read remains safe: the historical connection lists with its provenance.
    listed = await client.get("/api/v1/provider-connections")
    assert listed.status_code == 200
    legacy = next(c for c in listed.json() if c["transport_provider"] == "openrouter")
    assert legacy["active"] is False
    assert legacy["routes"][0]["active"] is False
    # The internal deactivation marker is never exposed to read clients.
    assert "openrouter_retired_v2" not in listed.text

    # Update is refused with 409 (before any mutation).
    patched = await client.patch(
        f"/api/v1/provider-connections/{conn_id}",
        json={"label": "reactivate", "active": True},
    )
    assert patched.status_code == 409

    # Test would decrypt/hit the network — it must be refused first (no call).
    from app.connectors.answer_engines import openai as openai_mod

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("no network call must be made for a legacy test")

    monkeypatch.setattr(openai_mod.httpx, "AsyncClient", _boom)

    tested = await client.post(f"/api/v1/provider-connections/{conn_id}/test")
    assert tested.status_code == 409


@pytest.mark.asyncio
async def test_provider_catalog_lists_direct_routes_only(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get("/api/v1/provider-catalog")
    assert resp.status_code == 200
    body = resp.json()
    # Active surface is exactly the three direct transports — no openrouter.
    assert set(body["transports"]) == {"openai", "anthropic", "google"}
    assert "openrouter" not in body["transports"]
    engines = {e["logical_engine"]: e for e in body["engines"]}
    # chatgpt is served ONLY via direct openai now.
    chatgpt_transports = {r["transport_provider"] for r in engines["chatgpt"]["routes"]}
    assert chatgpt_transports == {"openai"}
    gemini_transports = {r["transport_provider"] for r in engines["gemini"]["routes"]}
    assert gemini_transports == {"google"}
    claude_transports = {r["transport_provider"] for r in engines["claude"]["routes"]}
    assert claude_transports == {"anthropic"}


@pytest.mark.asyncio
async def test_cross_workspace_access_denied(
    client: httpx.AsyncClient,
) -> None:
    # Owner creates a connection.
    await _register(client, "owner@example.com")
    created = await client.post(
        "/api/v1/provider-connections", json=_connection_payload()
    )
    conn_id = created.json()["id"]

    # A different user (fresh workspace) must not see or touch it.
    await client.post("/api/v1/auth/logout")
    await _register(client, "intruder@example.com")
    listed = await client.get("/api/v1/provider-connections")
    assert listed.status_code == 200
    assert listed.json() == []

    got = await client.patch(
        f"/api/v1/provider-connections/{conn_id}",
        json={"label": "hijack"},
    )
    assert got.status_code == 404
    tested = await client.post(f"/api/v1/provider-connections/{conn_id}/test")
    assert tested.status_code == 404
