"""Component tests for the property-mapping API (I8).

Covers ``POST /integrations/{id}/mappings`` write-time validation (mapping
provider bound to the connection's provider ⇒ 422 on mismatch; connection +
project must share the workspace ⇒ 404 cross-workspace; the property must
resolve to one of the project's ``OwnedDomain`` rows ⇒ 422; one active owner
per ``(workspace, provider, property_ref)`` ⇒ 409), ``GET .../mappings``
(any-status list for the connection), and ``DELETE
/integrations/mappings/{mapping_id}`` (a status flip to ``disabled``, never
a row delete — which frees the active-owner slot for a re-create).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import httpx
import pytest
from sqlalchemy import select

from app.models.brand import OwnedDomain
from app.models.integrations import (
    IntegrationConnection,
    IntegrationOAuthGrant,
    IntegrationPropertyMapping,
)
from app.models.project import Project
from app.models.workspace import Workspace

_BASE = "/api/v1/integrations"

_MAPPING_KEYS = {
    "id",
    "workspace_id",
    "connection_id",
    "provider",
    "property_ref",
    "project_id",
    "status",
    "created_at",
    "updated_at",
}


async def _register(client: httpx.AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201


async def _workspace_id(db_session) -> uuid.UUID:
    return (await db_session.execute(select(Workspace))).scalars().first().id


async def _seed_connections(
    db_session, *, workspace_id: uuid.UUID, providers: tuple[str, ...] = ("gsc", "ga4")
) -> list[IntegrationConnection]:
    grant = IntegrationOAuthGrant(
        workspace_id=workspace_id, transport="google_oauth", status="connected"
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
    return connections


async def _seed_project(
    db_session, *, workspace_id: uuid.UUID, domains: tuple[str, ...] = ("acme.com",)
) -> Project:
    project = Project(workspace_id=workspace_id, name="Acme Visibility")
    db_session.add(project)
    await db_session.flush()
    for domain in domains:
        db_session.add(OwnedDomain(project_id=project.id, domain=domain))
    await db_session.commit()
    return project


def _assert_mapping_contract_shape(row: dict) -> None:
    assert set(row) == _MAPPING_KEYS
    for key in ("id", "workspace_id", "connection_id", "project_id"):
        uuid.UUID(row[key])
    assert row["status"] in {"active", "disabled"}
    datetime.fromisoformat(row["created_at"])
    datetime.fromisoformat(row["updated_at"])


async def _setup(client: httpx.AsyncClient, db_session, email: str):
    """Register one user; seed GSC+GA4 connections + an owned-domain project."""
    await _register(client, email)
    ws = await _workspace_id(db_session)
    connections = await _seed_connections(db_session, workspace_id=ws)
    project = await _seed_project(db_session, workspace_id=ws)
    gsc = next(c for c in connections if c.provider == "gsc")
    ga4 = next(c for c in connections if c.provider == "ga4")
    return ws, gsc, ga4, project


@pytest.mark.asyncio
async def test_create_mapping_sc_domain_property(
    client: httpx.AsyncClient, db_session
) -> None:
    _ws, gsc, _ga4, project = await _setup(client, db_session, "map-create@example.com")

    resp = await client.post(
        f"{_BASE}/{gsc.id}/mappings",
        json={
            "provider": "gsc",
            "property_ref": "sc-domain:acme.com",
            "project_id": str(project.id),
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    _assert_mapping_contract_shape(body)
    assert body["connection_id"] == str(gsc.id)
    assert body["provider"] == "gsc"
    assert body["property_ref"] == "sc-domain:acme.com"  # stored as given
    assert body["project_id"] == str(project.id)
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_create_mapping_url_prefix_and_www_properties(
    client: httpx.AsyncClient, db_session
) -> None:
    _ws, gsc, _ga4, project = await _setup(client, db_session, "map-url@example.com")

    url_prefix = await client.post(
        f"{_BASE}/{gsc.id}/mappings",
        json={
            "provider": "gsc",
            "property_ref": "https://acme.com/blog?utm_source=x",
            "project_id": str(project.id),
        },
    )
    assert url_prefix.status_code == 201

    www = await client.post(
        f"{_BASE}/{gsc.id}/mappings",
        json={
            "provider": "gsc",
            "property_ref": "https://www.acme.com/",
            "project_id": str(project.id),
        },
    )
    assert www.status_code == 201


@pytest.mark.asyncio
async def test_provider_mismatch_rejected_422(
    client: httpx.AsyncClient, db_session
) -> None:
    _ws, gsc, _ga4, project = await _setup(
        client, db_session, "map-mismatch@example.com"
    )

    resp = await client.post(
        f"{_BASE}/{gsc.id}/mappings",
        json={
            "provider": "ga4",  # the referenced connection is gsc
            "property_ref": "sc-domain:acme.com",
            "project_id": str(project.id),
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "mapping_provider_mismatch"
    assert await db_session.scalar(select(IntegrationPropertyMapping)) is None


@pytest.mark.asyncio
async def test_property_not_owned_domain_rejected_422(
    client: httpx.AsyncClient, db_session
) -> None:
    _ws, gsc, _ga4, project = await _setup(
        client, db_session, "map-unowned@example.com"
    )

    for property_ref in (
        "sc-domain:globex.com",  # a domain the project does not own
        "https://not-acme.example.org/path",
        "123456789",  # a bare provider id never resolves to a host
    ):
        resp = await client.post(
            f"{_BASE}/{gsc.id}/mappings",
            json={
                "provider": "gsc",
                "property_ref": property_ref,
                "project_id": str(project.id),
            },
        )
        assert resp.status_code == 422, property_ref
        assert resp.json()["detail"] == "mapping_property_not_owned"
    assert await db_session.scalar(select(IntegrationPropertyMapping)) is None


@pytest.mark.asyncio
async def test_cross_workspace_project_rejected_404(
    client: httpx.AsyncClient, db_session
) -> None:
    _ws, gsc, _ga4, _project = await _setup(client, db_session, "map-xws-p@example.com")
    # A project in ANOTHER workspace (no membership relationship at all).
    other_workspace = Workspace(name="Other")
    db_session.add(other_workspace)
    await db_session.flush()
    other_project = await _seed_project(db_session, workspace_id=other_workspace.id)

    resp = await client.post(
        f"{_BASE}/{gsc.id}/mappings",
        json={
            "provider": "gsc",
            "property_ref": "sc-domain:acme.com",
            "project_id": str(other_project.id),
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Project not found"


@pytest.mark.asyncio
async def test_cross_workspace_connection_rejected_404(
    client: httpx.AsyncClient, db_session
) -> None:
    _ws, gsc, _ga4, project = await _setup(client, db_session, "map-xws-o@example.com")

    await client.post("/api/v1/auth/logout")
    await _register(client, "map-xws-i@example.com")
    listed = await client.get(f"{_BASE}/{gsc.id}/mappings")
    assert listed.status_code == 404
    created = await client.post(
        f"{_BASE}/{gsc.id}/mappings",
        json={
            "provider": "gsc",
            "property_ref": "sc-domain:acme.com",
            "project_id": str(project.id),
        },
    )
    assert created.status_code == 404
    assert created.json()["detail"] == "Integration connection not found"


@pytest.mark.asyncio
async def test_one_active_owner_conflict_409_then_recreate_after_disable(
    client: httpx.AsyncClient, db_session
) -> None:
    _ws, gsc, _ga4, project = await _setup(client, db_session, "map-owner@example.com")
    payload = {
        "provider": "gsc",
        "property_ref": "sc-domain:acme.com",
        "project_id": str(project.id),
    }

    first = await client.post(f"{_BASE}/{gsc.id}/mappings", json=payload)
    assert first.status_code == 201
    duplicate = await client.post(f"{_BASE}/{gsc.id}/mappings", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "mapping_active_owner_conflict"

    # Disabling frees the (workspace, provider, property_ref) slot: the
    # partial unique index only covers ACTIVE rows.
    disabled = await client.delete(f"{_BASE}/mappings/{first.json()['id']}")
    assert disabled.status_code == 204
    recreated = await client.post(f"{_BASE}/{gsc.id}/mappings", json=payload)
    assert recreated.status_code == 201
    assert recreated.json()["id"] != first.json()["id"]


@pytest.mark.asyncio
async def test_same_property_allowed_for_another_provider(
    client: httpx.AsyncClient, db_session
) -> None:
    """The active-owner slot is scoped by (workspace, provider, property)."""
    _ws, gsc, ga4, project = await _setup(
        client, db_session, "map-providers@example.com"
    )

    gsc_mapping = await client.post(
        f"{_BASE}/{gsc.id}/mappings",
        json={
            "provider": "gsc",
            "property_ref": "sc-domain:acme.com",
            "project_id": str(project.id),
        },
    )
    assert gsc_mapping.status_code == 201
    ga4_mapping = await client.post(
        f"{_BASE}/{ga4.id}/mappings",
        json={
            "provider": "ga4",
            "property_ref": "properties/123456789",
            "project_id": str(project.id),
        },
    )
    assert ga4_mapping.status_code == 201
    # The prefixed provider resource-name spelling persists canonical.
    assert ga4_mapping.json()["property_ref"] == "123456789"


@pytest.mark.asyncio
async def test_create_ga4_mapping_numeric_property_ids(
    client: httpx.AsyncClient, db_session
) -> None:
    """GA4 refs are numeric property ids: the owned-domain rule does not
    apply (a numeric id can never resolve to an ``OwnedDomain`` host).
    Both spellings are accepted and persist as the CANONICAL bare numeric
    id, so the two spellings share one active-owner slot."""
    _ws, _gsc, ga4, project = await _setup(client, db_session, "map-ga4@example.com")

    for submitted, canonical in (
        ("123456789", "123456789"),
        ("properties/987654321", "987654321"),
    ):
        resp = await client.post(
            f"{_BASE}/{ga4.id}/mappings",
            json={
                "provider": "ga4",
                "property_ref": submitted,
                "project_id": str(project.id),
            },
        )
        assert resp.status_code == 201, submitted
        body = resp.json()
        _assert_mapping_contract_shape(body)
        assert body["provider"] == "ga4"
        assert body["property_ref"] == canonical


@pytest.mark.asyncio
async def test_create_ga4_mapping_malformed_id_rejected_422(
    client: httpx.AsyncClient, db_session
) -> None:
    """A domain-shaped or otherwise non-numeric GA4 ref is a 422."""
    _ws, _gsc, ga4, project = await _setup(
        client, db_session, "map-ga4-bad@example.com"
    )

    for property_ref in (
        "https://acme.com/",  # domain-shaped is not a GA4 property id
        "sc-domain:acme.com",
        "properties/",  # the resource prefix without an id
        "properties/acme",  # a non-numeric id
    ):
        resp = await client.post(
            f"{_BASE}/{ga4.id}/mappings",
            json={
                "provider": "ga4",
                "property_ref": property_ref,
                "project_id": str(project.id),
            },
        )
        assert resp.status_code == 422, property_ref
        assert resp.json()["detail"] == "mapping_property_not_owned"
    assert await db_session.scalar(select(IntegrationPropertyMapping)) is None


@pytest.mark.asyncio
async def test_list_and_disable_mapping(
    client: httpx.AsyncClient, db_session
) -> None:
    _ws, gsc, _ga4, project = await _setup(client, db_session, "map-list@example.com")
    for property_ref in ("sc-domain:acme.com", "https://acme.com/docs"):
        created = await client.post(
            f"{_BASE}/{gsc.id}/mappings",
            json={
                "provider": "gsc",
                "property_ref": property_ref,
                "project_id": str(project.id),
            },
        )
        assert created.status_code == 201

    listed = await client.get(f"{_BASE}/{gsc.id}/mappings")
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 2
    for row in body:
        _assert_mapping_contract_shape(row)
        assert row["status"] == "active"
    assert [row["property_ref"] for row in body] == [
        "sc-domain:acme.com",
        "https://acme.com/docs",
    ]

    # Disable is a STATUS FLIP, never a row delete: the mapping stays listed.
    mapping_id = body[0]["id"]
    deleted = await client.delete(f"{_BASE}/mappings/{mapping_id}")
    assert deleted.status_code == 204
    relisted = await client.get(f"{_BASE}/{gsc.id}/mappings")
    assert len(relisted.json()) == 2
    by_id = {row["id"]: row for row in relisted.json()}
    assert by_id[mapping_id]["status"] == "disabled"

    row = await db_session.get(IntegrationPropertyMapping, uuid.UUID(mapping_id))
    assert row is not None
    assert row.status == "disabled"

    # Idempotent; an unknown/cross-workspace id is a 404.
    assert (await client.delete(f"{_BASE}/mappings/{mapping_id}")).status_code == 204
    assert (await client.delete(f"{_BASE}/mappings/{uuid.uuid4()}")).status_code == 404

    await client.post("/api/v1/auth/logout")
    await _register(client, "map-list-intruder@example.com")
    assert (await client.delete(f"{_BASE}/mappings/{mapping_id}")).status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_mappings_rejected(client: httpx.AsyncClient) -> None:
    some_id = uuid.uuid4()
    assert (await client.get(f"{_BASE}/{some_id}/mappings")).status_code == 401
    assert (
        await client.post(f"{_BASE}/{some_id}/mappings", json={})
    ).status_code == 401
    assert (await client.delete(f"{_BASE}/mappings/{some_id}")).status_code == 401
