"""Component tests for the projects + prompts API (B3, httpx ASGITransport).

Adapted from the reference ``tests/component/test_ai_visibility_api.py`` to
Searchify's UUID + workspace-scoped model. Covers the B3 acceptance:
  - project CRUD persists normalized brand identity + prompts, workspace-scoped;
  - prompt-intent + benchmark_mode validation;
  - the ``/generate`` stub returns not-implemented;
  - CSV bulk-import persists prompts as ``imported``;
  - cross-workspace access is denied (reuses the B2 isolation pattern).
"""
from __future__ import annotations

import io

import httpx
import pytest


async def _register(client: httpx.AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201


def _project_payload(**overrides: object) -> dict:
    payload = {
        "name": "Acme Visibility",
        "brand_name": "Acme Corp",
        "brand": {"aliases": ["Acme", "ACME Inc"]},
        "website_url": "https://acme.com",
        "owned_domains": ["acme.com"],
        "unintended_domains": ["support.acme.com"],
        "competitors": [
            {"name": "Globex", "aliases": ["Globex Co"], "domains": ["globex.com"]}
        ],
        "country_code": "AU",
        "language_code": "en-AU",
        "benchmark_mode": "controlled_localized",
        "default_repetitions": 3,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_project_persists_normalized_identity(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "p1@example.com")
    resp = await client.post("/api/v1/projects", json=_project_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert "-" in body["id"] and "-" in body["workspace_id"]
    assert body["brand_name"] == "Acme Corp"
    assert body["brand"]["aliases"] == ["Acme", "ACME Inc"]
    assert body["owned_domains"] == ["acme.com"]
    assert body["unintended_domains"] == ["support.acme.com"]
    assert len(body["competitors"]) == 1
    assert body["competitors"][0]["name"] == "Globex"
    assert "-" in body["competitors"][0]["id"]
    assert body["prompt_sets"] == []

    # Round-trips on GET.
    got = await client.get(f"/api/v1/projects/{body['id']}")
    assert got.status_code == 200
    assert got.json()["brand"]["aliases"] == ["Acme", "ACME Inc"]


@pytest.mark.asyncio
async def test_project_list_and_update_and_delete(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "p2@example.com")
    created = (
        await client.post("/api/v1/projects", json=_project_payload())
    ).json()

    listing = await client.get("/api/v1/projects")
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    patched = await client.patch(
        f"/api/v1/projects/{created['id']}",
        json={
            "name": "Renamed",
            "brand": {"aliases": ["NewAlias"]},
            "benchmark_mode": "forced_grounded",
        },
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed"
    assert patched.json()["brand"]["aliases"] == ["NewAlias"]
    assert patched.json()["benchmark_mode"] == "forced_grounded"

    deleted = await client.delete(f"/api/v1/projects/{created['id']}")
    assert deleted.status_code == 204
    assert (await client.get("/api/v1/projects")).json() == []


@pytest.mark.asyncio
async def test_benchmark_mode_validation(client: httpx.AsyncClient) -> None:
    await _register(client, "p3@example.com")
    resp = await client.post(
        "/api/v1/projects", json=_project_payload(benchmark_mode="warp_speed")
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_prompt_set_and_prompt_crud_and_intent_validation(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "p4@example.com")
    project = (
        await client.post("/api/v1/projects", json=_project_payload())
    ).json()

    ps = await client.post(
        "/api/v1/prompt-sets",
        json={"project_id": project["id"], "name": "Launch set"},
    )
    assert ps.status_code == 201
    prompt_set_id = ps.json()["id"]
    assert ps.json()["prompts"] == []

    # Known intent is kept.
    created = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/prompts",
        json={"text": "best running shoes", "intent": "Discovery"},
    )
    assert created.status_code == 201
    assert created.json()["intent"] == "discovery"
    assert created.json()["origin"] == "manual"
    prompt_id = created.json()["id"]

    # Unknown intent normalizes to "".
    created2 = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/prompts",
        json={"text": "another prompt", "intent": "teleport"},
    )
    assert created2.status_code == 201
    assert created2.json()["intent"] == ""

    # Set now reports its prompts.
    got = await client.get(f"/api/v1/prompt-sets/{prompt_set_id}")
    assert got.json()["prompt_count"] == 2
    assert len(got.json()["prompts"]) == 2

    # Update + delete a prompt.
    upd = await client.patch(
        f"/api/v1/prompts/{prompt_id}",
        json={"enabled": False, "intent": "purchase"},
    )
    assert upd.status_code == 200
    assert upd.json()["enabled"] is False
    assert upd.json()["intent"] == "purchase"

    dele = await client.delete(f"/api/v1/prompts/{prompt_id}")
    assert dele.status_code == 204


@pytest.mark.asyncio
async def test_csv_import_bulk_creates_imported_prompts(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "p5@example.com")
    project = (
        await client.post("/api/v1/projects", json=_project_payload())
    ).json()
    prompt_set_id = (
        await client.post(
            "/api/v1/prompt-sets",
            json={"project_id": project["id"], "name": "Imported"},
        )
    ).json()["id"]

    csv_bytes = (
        b"text,theme,intent\n"
        b"cheap laptops,tech,discovery\n"
        b"Acme vs Globex,compare,comparison\n"
        b"   ,skip,discovery\n"
    )
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/import",
        files={"file": ("prompts.csv", io.BytesIO(csv_bytes), "text/csv")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["prompt_count"] == 2  # blank-text row dropped
    assert {p["origin"] for p in body["prompts"]} == {"imported"}
    assert {p["intent"] for p in body["prompts"]} == {"discovery", "comparison"}


@pytest.mark.asyncio
async def test_csv_import_accepts_json_rows(client: httpx.AsyncClient) -> None:
    await _register(client, "p5b@example.com")
    project = (
        await client.post("/api/v1/projects", json=_project_payload())
    ).json()
    prompt_set_id = (
        await client.post(
            "/api/v1/prompt-sets",
            json={"project_id": project["id"], "name": "JSON rows"},
        )
    ).json()["id"]

    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/import",
        json={"prompts": [{"text": "row one"}, {"text": "row two"}]},
    )
    assert resp.status_code == 201
    assert resp.json()["prompt_count"] == 2
    assert {p["origin"] for p in resp.json()["prompts"]} == {"imported"}


@pytest.mark.asyncio
async def test_generate_is_not_implemented_stub(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "p6@example.com")
    project = (
        await client.post("/api/v1/projects", json=_project_payload())
    ).json()
    prompt_set_id = (
        await client.post(
            "/api/v1/prompt-sets",
            json={"project_id": project["id"], "name": "Gen"},
        )
    ).json()["id"]

    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate"
    )
    assert resp.status_code == 501
    assert resp.json()["detail"]["code"] == "not_implemented"


@pytest.mark.asyncio
async def test_projects_require_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/projects")).status_code == 401


@pytest.mark.asyncio
async def test_cross_workspace_project_isolation(
    client: httpx.AsyncClient,
) -> None:
    """User B cannot see or fetch user A's project (invariant 5)."""
    await _register(client, "owner-a@example.com")
    a_project = (
        await client.post("/api/v1/projects", json=_project_payload())
    ).json()

    # Switch to user B (fresh session cookie in the same client).
    client.cookies.clear()
    await _register(client, "owner-b@example.com")

    # B's list is empty and B cannot fetch A's project by id.
    assert (await client.get("/api/v1/projects")).json() == []
    got = await client.get(f"/api/v1/projects/{a_project['id']}")
    assert got.status_code == 404

    # B also cannot create a prompt set against A's project.
    ps = await client.post(
        "/api/v1/prompt-sets",
        json={"project_id": a_project["id"], "name": "sneaky"},
    )
    assert ps.status_code == 404
