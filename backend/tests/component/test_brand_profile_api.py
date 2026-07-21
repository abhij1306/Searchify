"""Component coverage for workspace-scoped BrandProfile CRUD."""

from __future__ import annotations

import json

import httpx
import pytest

import app.api.projects as projects_api
from app.connectors.agent.client import AgentNotConfiguredError


class FakeAgent:
    model = "fake-profile-model"
    base_url_host = "agent.test"

    def __init__(self) -> None:
        self.response = json.dumps(
            {
                "description": "Australian family retailer.",
                "positioning": "Value-priced everyday family basics.",
                "products_services": ["Clothing", "Homewares"],
                "target_audience": "Budget-conscious Australian families.",
            }
        )
        self.calls: list[dict[str, str]] = []

    async def complete_json(self, *, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        return self.response


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    agent = FakeAgent()
    monkeypatch.setattr(projects_api, "DefaultAgentClient", lambda: agent)
    return agent


async def _register(client: httpx.AsyncClient, email: str) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert response.status_code == 201


async def _create_project(client: httpx.AsyncClient, name: str = "Acme") -> dict:
    response = await client.post(
        "/api/v1/projects",
        json={
            "name": f"{name} visibility",
            "brand_name": name,
            "website_url": "https://acme.example",
            "country_code": "AU",
            "language_code": "en-AU",
        },
    )
    assert response.status_code == 201
    return response.json()


@pytest.mark.asyncio
async def test_project_creation_provisions_empty_brand_profile(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "profile-create@example.com")
    project = await _create_project(client)

    response = await client.get(f"/api/v1/projects/{project['id']}/brand-profile")

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == project["workspace_id"]
    assert body["project_id"] == project["id"]
    assert body["description"] == ""
    assert body["products_services"] == []
    assert body["sources"] == {
        "description": None,
        "positioning": None,
        "products_services": None,
        "target_audience": None,
    }


@pytest.mark.asyncio
async def test_manual_upsert_marks_supplied_fields_and_preserves_others(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "profile-upsert@example.com")
    project = await _create_project(client)
    url = f"/api/v1/projects/{project['id']}/brand-profile"

    first = await client.put(
        url,
        json={
            "description": "  A practical retailer.  ",
            "positioning": "Value-priced family basics",
            "products_services": [" Clothing ", "Homewares", "clothing"],
        },
    )
    assert first.status_code == 200
    body = first.json()
    assert body["description"] == "A practical retailer."
    assert body["products_services"] == ["Clothing", "Homewares"]
    assert body["sources"]["description"] == "manual"
    assert body["sources"]["positioning"] == "manual"
    assert body["sources"]["products_services"] == "manual"
    assert body["sources"]["target_audience"] is None

    second = await client.put(
        url,
        json={"target_audience": "Budget-conscious families"},
    )
    assert second.status_code == 200
    updated = second.json()
    assert updated["positioning"] == "Value-priced family basics"
    assert updated["target_audience"] == "Budget-conscious families"
    assert updated["sources"]["target_audience"] == "manual"


@pytest.mark.asyncio
async def test_brand_profile_is_workspace_isolated(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "profile-owner@example.com")
    project = await _create_project(client)
    url = f"/api/v1/projects/{project['id']}/brand-profile"

    client.cookies.clear()
    await _register(client, "profile-other@example.com")

    assert (await client.get(url)).status_code == 404
    assert (
        await client.put(url, json={"description": "cross-tenant write"})
    ).status_code == 404


@pytest.mark.asyncio
async def test_suggestion_is_review_only_then_accepts_with_provenance(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    await _register(client, "profile-suggest@example.com")
    project = await _create_project(client, "Best & Less")
    profile_url = f"/api/v1/projects/{project['id']}/brand-profile"

    suggested = await client.post(
        f"{profile_url}/suggest",
        json={"confirm_send_evidence": True},
    )
    assert suggested.status_code == 201
    artifact = suggested.json()
    assert artifact["model_identity"] == {
        "transport_host": "agent.test",
        "transport_model": "fake-profile-model",
    }
    assert artifact["prompt_template_version"] == "brand-profile-suggest-v1"
    assert artifact["draft"]["positioning"].startswith("Value-priced")
    assert "Best & Less" in fake_agent.calls[0]["user"]

    # Drafting is review-only: it must not mutate the curated profile.
    before_accept = (await client.get(profile_url)).json()
    assert before_accept["positioning"] == ""
    assert before_accept["sources"]["positioning"] is None

    accepted = await client.post(
        f"{profile_url}/suggestions/{artifact['id']}/accept",
        json={
            "accepted_fields": ["positioning", "products_services"],
            "manual_overrides": {"description": "Edited by the user during review."},
        },
    )
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["accepted_fields"] == ["positioning", "products_services"]
    assert body["profile"]["sources"]["description"] == "manual"
    assert body["profile"]["sources"]["positioning"] == "ai_suggested"
    assert body["profile"]["source_artifact_ids"]["description"] is None
    assert body["profile"]["source_artifact_ids"]["positioning"] == artifact["id"]


@pytest.mark.asyncio
async def test_later_ai_acceptance_cannot_overwrite_manual_field(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    await _register(client, "profile-manual-wins@example.com")
    project = await _create_project(client)
    profile_url = f"/api/v1/projects/{project['id']}/brand-profile"
    manual_positioning = "User-defined specialist positioning."
    assert (
        await client.put(profile_url, json={"positioning": manual_positioning})
    ).status_code == 200

    artifact = (
        await client.post(
            f"{profile_url}/suggest",
            json={"confirm_send_evidence": True},
        )
    ).json()
    accepted = await client.post(
        f"{profile_url}/suggestions/{artifact['id']}/accept",
        json={"accepted_fields": ["positioning", "target_audience"]},
    )

    assert accepted.status_code == 200
    body = accepted.json()
    assert body["accepted_fields"] == ["target_audience"]
    assert body["skipped_manual_fields"] == ["positioning"]
    assert body["profile"]["positioning"] == manual_positioning
    assert body["profile"]["sources"]["positioning"] == "manual"


@pytest.mark.asyncio
async def test_suggestion_requires_consent_before_agent_resolution(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _register(client, "profile-consent@example.com")
    project = await _create_project(client)

    def _raise() -> None:
        raise AgentNotConfiguredError("no key")

    monkeypatch.setattr(projects_api, "DefaultAgentClient", _raise)
    response = await client.post(
        f"/api/v1/projects/{project['id']}/brand-profile/suggest",
        json={"confirm_send_evidence": False},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "brand_profile_suggestion_invalid"


@pytest.mark.asyncio
async def test_suggestion_artifact_is_workspace_isolated(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    await _register(client, "profile-artifact-owner@example.com")
    project = await _create_project(client)
    profile_url = f"/api/v1/projects/{project['id']}/brand-profile"
    artifact = (
        await client.post(
            f"{profile_url}/suggest",
            json={"confirm_send_evidence": True},
        )
    ).json()

    client.cookies.clear()
    await _register(client, "profile-artifact-other@example.com")
    response = await client.post(
        f"{profile_url}/suggestions/{artifact['id']}/accept",
        json={"accepted_fields": ["description"]},
    )

    assert response.status_code == 404
