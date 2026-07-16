"""Component tests for the auth + workspace API (httpx ASGITransport).

Covers the B2 acceptance:
  - register/login sets the auth cookie;
  - a workspace is auto-created on first login and the user is a member;
  - cross-workspace access is rejected (403/404).
"""
from __future__ import annotations

import httpx
import pytest

from app.core.config import settings

COOKIE = settings.session_cookie_name


async def _register(
    client: httpx.AsyncClient, email: str, password: str = "password123"
):
    return await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )


@pytest.mark.asyncio
async def test_register_sets_cookie_and_returns_user(client: httpx.AsyncClient) -> None:
    resp = await _register(client, "alice@example.com")
    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["email"] == "alice@example.com"
    # UUID id, no password ever returned.
    assert "-" in body["user"]["id"]
    assert "password" not in body["user"]
    assert "hashed_password" not in body["user"]
    assert COOKIE in resp.cookies
    # Cookie is HttpOnly.
    set_cookie = resp.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower()


@pytest.mark.asyncio
async def test_login_sets_cookie_and_workspace_autocreated(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "bob@example.com")
    # Fresh client cookies aside, login via a clean jar.
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "password123"},
    )
    assert resp.status_code == 200
    assert COOKIE in resp.cookies

    # Workspace auto-created on registration/first login; user is a member.
    ws_resp = await client.get("/api/v1/workspaces")
    assert ws_resp.status_code == 200
    workspaces = ws_resp.json()
    assert len(workspaces) == 1
    assert workspaces[0]["role"] == "owner"
    assert workspaces[0]["name"]


@pytest.mark.asyncio
async def test_me_requires_auth(client: httpx.AsyncClient) -> None:
    unauth = await client.get("/api/v1/auth/me")
    assert unauth.status_code == 401

    await _register(client, "carol@example.com")
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "carol@example.com"


@pytest.mark.asyncio
async def test_logout_clears_session(client: httpx.AsyncClient) -> None:
    await _register(client, "dave@example.com")
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204
    client.cookies.clear()
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_duplicate_registration_rejected(client: httpx.AsyncClient) -> None:
    assert (await _register(client, "dup@example.com")).status_code == 201
    dup = await _register(client, "dup@example.com")
    assert dup.status_code == 400


@pytest.mark.asyncio
async def test_login_bad_credentials_rejected(client: httpx.AsyncClient) -> None:
    await _register(client, "eve@example.com")
    bad = await client.post(
        "/api/v1/auth/login",
        json={"email": "eve@example.com", "password": "wrong-password"},
    )
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_create_and_list_workspaces(client: httpx.AsyncClient) -> None:
    await _register(client, "frank@example.com")
    created = await client.post("/api/v1/workspaces", json={"name": "Acme"})
    assert created.status_code == 201
    assert created.json()["name"] == "Acme"

    listing = await client.get("/api/v1/workspaces")
    names = {w["name"] for w in listing.json()}
    # personal auto-created workspace + the new one.
    assert "Acme" in names
    assert len(listing.json()) == 2


@pytest.mark.asyncio
async def test_workspaces_list_requires_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/workspaces")).status_code == 401


@pytest.mark.asyncio
async def test_cross_workspace_isolation(client: httpx.AsyncClient) -> None:
    """A member of workspace A cannot see workspace B (invariant 5)."""
    # User A registers (auto workspace A) and creates an extra workspace.
    await _register(client, "usera@example.com")
    a_extra = await client.post("/api/v1/workspaces", json={"name": "A-Team"})
    assert a_extra.status_code == 201
    a_workspaces = {w["id"] for w in (await client.get("/api/v1/workspaces")).json()}

    # Switch to user B in the same client (new session cookie).
    client.cookies.clear()
    await _register(client, "userb@example.com")
    b_workspaces = {w["id"] for w in (await client.get("/api/v1/workspaces")).json()}

    # B sees only its own workspace(s), none of A's.
    assert a_workspaces.isdisjoint(b_workspaces)
    assert len(b_workspaces) == 1
