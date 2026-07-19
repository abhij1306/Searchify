"""Component tests for AI prompt generation, topics, and the review lifecycle.

The default agent is always faked at the API boundary
(``app.api.prompts.DefaultAgentClient``) so no test ever performs live
provider I/O, regardless of what keys exist in the developer's ``.env``.

Covers:
  - generate happy path: topics get-or-created, prompts land ``proposed`` /
    ``origin='generated'`` with provenance evidence (invariant 4);
  - backend-enforced ``confirm_send_evidence`` + count cap (422);
  - unconfigured agent -> 503, but foreign set -> 404 first (invariant 5);
  - unparseable model output -> 502;
  - DB-level duplicate dropping across repeat runs (conflict-safe dedupe);
  - topics CRUD with per-status counts + duplicate-name 409;
  - bulk status review transitions + duplicate prompt text 409;
  - the audit planner never consumes ``proposed``/``archived`` prompts.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.api.prompts as prompts_api
from app.connectors.agent.client import AgentNotConfiguredError
from app.domain.audits.planner import AuditValidationError, create_audit, list_tasks
from app.models.prompt import Prompt
from tests.component.audit_helpers import seed_audit_fixtures

VALID_AGENT_RESPONSE = json.dumps(
    {
        "topics": [
            {
                "name": "Footwear",
                "prompts": [
                    {"text": "best running shoes in australia", "intent": "discovery"},
                    {"text": "acme vs globex running shoes", "intent": "comparison"},
                ],
            },
            {
                "name": "Sizing",
                "prompts": [
                    {"text": "how do running shoe sizes work", "intent": "category"},
                ],
            },
        ]
    }
)


class FakeAgent:
    """Stands in for DefaultAgentClient; records calls, returns a canned body."""

    model = "fake-model"
    base_url_host = "agent.test"

    def __init__(self, response: str = VALID_AGENT_RESPONSE) -> None:
        self.response = response
        self.calls: list[dict[str, str]] = []

    async def complete_json(self, *, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        return self.response


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    agent = FakeAgent()
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: agent)
    return agent


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
        "unintended_domains": [],
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


async def _make_project_and_set(
    client: httpx.AsyncClient, email: str
) -> tuple[dict, str]:
    await _register(client, email)
    project = (await client.post("/api/v1/projects", json=_project_payload())).json()
    prompt_set_id = (
        await client.post(
            "/api/v1/prompt-sets",
            json={"project_id": project["id"], "name": "Default"},
        )
    ).json()["id"]
    return project, prompt_set_id


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_generate_creates_proposed_prompts_and_topics(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    project, prompt_set_id = await _make_project_and_set(client, "gen1@example.com")

    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 3, "confirm_send_evidence": True},
    )
    assert resp.status_code == 201
    body = resp.json()

    assert body["dropped_duplicates"] == 0
    assert len(body["generated"]) == 3
    for prompt in body["generated"]:
        assert prompt["status"] == "proposed"
        assert prompt["origin"] == "generated"
        assert prompt["topic_id"] is not None
    assert {t["name"] for t in body["topics"]} == {"Footwear", "Sizing"}
    footwear = next(t for t in body["topics"] if t["name"] == "Footwear")
    assert footwear["origin"] == "generated"
    assert footwear["proposed_count"] == 2
    assert footwear["active_count"] == 0

    # The brand evidence went to the agent (confirmed above), and the
    # request embedded identity + count instructions.
    assert len(fake_agent.calls) == 1
    sent = fake_agent.calls[0]["user"]
    assert "Acme Corp" in sent
    assert "Globex" in sent
    assert "exactly 3 prompts" in sent

    # Provenance evidence is persisted but the API response never includes
    # any credential material — only host + model identity.
    listed = (await client.get(f"/api/v1/prompt-sets/{prompt_set_id}")).json()
    assert len(listed["prompts"]) == 3
    # comparison prompt names both brands -> branded classification
    branded = {p["text"]: p["branded"] for p in listed["prompts"]}
    assert branded["acme vs globex running shoes"] is True
    assert branded["how do running shoe sizes work"] is False


@pytest.mark.asyncio
async def test_generate_persists_provenance_evidence(
    client: httpx.AsyncClient,
    fake_agent: FakeAgent,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, prompt_set_id = await _make_project_and_set(client, "gen2@example.com")
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 3, "confirm_send_evidence": True},
    )
    assert resp.status_code == 201

    async with session_factory() as session:
        prompts = (
            (
                await session.execute(
                    select(Prompt).where(
                        Prompt.prompt_set_id == uuid.UUID(prompt_set_id)
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(prompts) == 3
    run_ids = set()
    for prompt in prompts:
        evidence = prompt.generation_evidence
        assert evidence["generator_version"] == "prompt-gen-v1"
        assert evidence["model_identity"] == {
            "transport_host": "agent.test",
            "transport_model": "fake-model",
        }
        assert evidence["requested_count"] == 3
        run_ids.add(evidence["generation_run_id"])
    assert len(run_ids) == 1  # one run id for the whole batch


@pytest.mark.asyncio
async def test_generate_requires_evidence_confirmation(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    _, prompt_set_id = await _make_project_and_set(client, "gen3@example.com")
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 3},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "generation_invalid"
    assert fake_agent.calls == []  # nothing was sent without consent


@pytest.mark.asyncio
async def test_generate_rejects_count_over_cap(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    _, prompt_set_id = await _make_project_and_set(client, "gen4@example.com")
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 9999, "confirm_send_evidence": True},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "generation_invalid"
    assert fake_agent.calls == []


@pytest.mark.asyncio
async def test_generate_rejects_foreign_topic_id(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    _, prompt_set_id = await _make_project_and_set(client, "gen5@example.com")
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={
            "count": 3,
            "confirm_send_evidence": True,
            "topic_id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 422
    assert fake_agent.calls == []


@pytest.mark.asyncio
async def test_generate_unconfigured_agent_returns_503(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, prompt_set_id = await _make_project_and_set(client, "gen6@example.com")

    def _unconfigured() -> None:
        raise AgentNotConfiguredError("no key")

    monkeypatch.setattr(prompts_api, "DefaultAgentClient", _unconfigured)
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 3, "confirm_send_evidence": True},
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == "agent_not_configured"
    assert "DEFAULT_AGENT_API_KEY" in detail["message"]


@pytest.mark.asyncio
async def test_generate_foreign_set_is_404_even_when_unconfigured(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scope check wins over configuration state (no existence oracle)."""
    _, prompt_set_id = await _make_project_and_set(client, "gen7a@example.com")
    client.cookies.clear()
    await _register(client, "gen7b@example.com")

    def _unconfigured() -> None:
        raise AgentNotConfiguredError("no key")

    monkeypatch.setattr(prompts_api, "DefaultAgentClient", _unconfigured)
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 3, "confirm_send_evidence": True},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_generate_invalid_payload_is_422_even_when_unconfigured(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation wins over configuration state (422 before 503)."""
    _, prompt_set_id = await _make_project_and_set(client, "gen7c@example.com")

    def _unconfigured() -> None:
        raise AgentNotConfiguredError("no key")

    monkeypatch.setattr(prompts_api, "DefaultAgentClient", _unconfigured)
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 3, "confirm_send_evidence": False},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "generation_invalid"


@pytest.mark.asyncio
async def test_generate_unparseable_output_returns_502(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, prompt_set_id = await _make_project_and_set(client, "gen8@example.com")
    agent = FakeAgent(response="this is not json")
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: agent)
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 3, "confirm_send_evidence": True},
    )
    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "generation_unparseable"


@pytest.mark.asyncio
async def test_generate_twice_drops_duplicates(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    """Same model output twice: run 2 inserts nothing, reports drops."""
    _, prompt_set_id = await _make_project_and_set(client, "gen9@example.com")

    first = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 3, "confirm_send_evidence": True},
    )
    assert first.status_code == 201
    assert len(first.json()["generated"]) == 3

    second = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 3, "confirm_send_evidence": True},
    )
    assert second.status_code == 201
    assert second.json()["generated"] == []
    assert second.json()["dropped_duplicates"] == 3

    listed = (await client.get(f"/api/v1/prompt-sets/{prompt_set_id}")).json()
    assert len(listed["prompts"]) == 3  # no dupes, and no reused topics broke


@pytest.mark.asyncio
async def test_generate_into_target_topic(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, prompt_set_id = await _make_project_and_set(client, "gen10@example.com")
    topic = (
        await client.post(
            f"/api/v1/projects/{project['id']}/topics",
            json={"name": "Pricing"},
        )
    ).json()

    # Model still answers with its own topic name; scoped generation must
    # land everything in the requested topic regardless.
    agent = FakeAgent(
        response=json.dumps(
            {
                "topics": [
                    {
                        "name": "Whatever The Model Said",
                        "prompts": [{"text": "acme pricing plans", "intent": "brand"}],
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: agent)
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={
            "count": 1,
            "confirm_send_evidence": True,
            "topic_id": topic["id"],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert [t["id"] for t in body["topics"]] == [topic["id"]]
    assert body["generated"][0]["topic_id"] == topic["id"]
    assert "ONLY for this topic" in agent.calls[0]["user"]

    # No new topic was created from the model's invented name.
    topics = (await client.get(f"/api/v1/projects/{project['id']}/topics")).json()
    assert [t["name"] for t in topics] == ["Pricing"]


# --------------------------------------------------------------------------
# Topics CRUD
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_topics_crud_with_counts(client: httpx.AsyncClient) -> None:
    project, prompt_set_id = await _make_project_and_set(client, "top1@example.com")
    project_id = project["id"]

    created = await client.post(
        f"/api/v1/projects/{project_id}/topics",
        json={"name": "Footwear", "description": "Shoes and boots"},
    )
    assert created.status_code == 201
    topic = created.json()
    assert topic["origin"] == "manual"
    assert topic["active_count"] == 0 and topic["proposed_count"] == 0

    # Duplicate name (same project) -> 409.
    dup = await client.post(
        f"/api/v1/projects/{project_id}/topics", json={"name": "Footwear"}
    )
    assert dup.status_code == 409

    # A prompt assigned to the topic shows up in the counts.
    prompt = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/prompts",
        json={"text": "best hiking boots"},
    )
    assert prompt.status_code == 201
    patched = await client.patch(
        f"/api/v1/prompts/{prompt.json()['id']}",
        json={"topic_id": topic["id"]},
    )
    assert patched.status_code == 200
    assert patched.json()["topic_id"] == topic["id"]

    listing = await client.get(f"/api/v1/projects/{project_id}/topics")
    assert listing.status_code == 200
    assert listing.json()[0]["active_count"] == 1

    renamed = await client.patch(
        f"/api/v1/topics/{topic['id']}", json={"name": "Boots"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Boots"

    deleted = await client.delete(f"/api/v1/topics/{topic['id']}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/projects/{project_id}/topics")).json() == []

    # Topic delete detaches (SET NULL), never deletes prompts.
    survivor = (await client.get(f"/api/v1/prompt-sets/{prompt_set_id}")).json()
    assert len(survivor["prompts"]) == 1
    assert survivor["prompts"][0]["topic_id"] is None


@pytest.mark.asyncio
async def test_topics_are_workspace_scoped(client: httpx.AsyncClient) -> None:
    project, _ = await _make_project_and_set(client, "top2a@example.com")
    topic = (
        await client.post(
            f"/api/v1/projects/{project['id']}/topics", json={"name": "Mine"}
        )
    ).json()

    client.cookies.clear()
    await _register(client, "top2b@example.com")
    assert (
        await client.get(f"/api/v1/projects/{project['id']}/topics")
    ).status_code == 404
    assert (
        await client.patch(f"/api/v1/topics/{topic['id']}", json={"name": "X"})
    ).status_code == 404
    assert (await client.delete(f"/api/v1/topics/{topic['id']}")).status_code == 404


# --------------------------------------------------------------------------
# Review lifecycle: status edits, bulk transitions, duplicate guard
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prompt_status_update_and_duplicate_409(
    client: httpx.AsyncClient,
) -> None:
    _, prompt_set_id = await _make_project_and_set(client, "rev1@example.com")
    prompt = (
        await client.post(
            f"/api/v1/prompt-sets/{prompt_set_id}/prompts",
            json={"text": "best value shoes"},
        )
    ).json()
    assert prompt["status"] == "active"

    archived = await client.patch(
        f"/api/v1/prompts/{prompt['id']}", json={"status": "archived"}
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    # Same concept ("Best value shoes?" normalizes identically) -> 409.
    dup = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/prompts",
        json={"text": "Best  value shoes?"},
    )
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_bulk_status_accepts_proposed_prompts(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    _, prompt_set_id = await _make_project_and_set(client, "rev2@example.com")
    generated = (
        await client.post(
            f"/api/v1/prompt-sets/{prompt_set_id}/generate",
            json={"count": 3, "confirm_send_evidence": True},
        )
    ).json()["generated"]
    ids = [p["id"] for p in generated]

    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/prompts/bulk-status",
        json={"prompt_ids": ids, "status": "active"},
    )
    assert resp.status_code == 200
    assert {p["status"] for p in resp.json()["prompts"]} == {"active"}


@pytest.mark.asyncio
async def test_bulk_status_rejects_foreign_prompt_ids(
    client: httpx.AsyncClient,
) -> None:
    """One bad id rejects the whole batch — no partial transitions."""
    _, prompt_set_id = await _make_project_and_set(client, "rev3@example.com")
    prompt = (
        await client.post(
            f"/api/v1/prompt-sets/{prompt_set_id}/prompts",
            json={"text": "real prompt"},
        )
    ).json()

    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/prompts/bulk-status",
        json={"prompt_ids": [prompt["id"], str(uuid.uuid4())], "status": "archived"},
    )
    assert resp.status_code == 404
    # The valid prompt was not transitioned.
    listed = (await client.get(f"/api/v1/prompt-sets/{prompt_set_id}")).json()
    assert listed["prompts"][0]["status"] == "active"


# --------------------------------------------------------------------------
# Audits only consume active prompts (no auto-run of AI suggestions)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_planner_excludes_proposed_and_archived_prompts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=3)
        for prompt_id, status in zip(
            seed.prompt_ids[:2], ("proposed", "archived"), strict=True
        ):
            prompt = await session.get(Prompt, prompt_id)
            prompt.status = status
        await session.commit()

    async with session_factory() as session:
        audit = await create_audit(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            engines=seed.engines,
            prompt_set_id=seed.prompt_set_id,
            repetitions=1,
        )
        tasks = await list_tasks(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        # Only the one still-active prompt produced a slot.
        assert len(tasks) == 1

    # Explicitly requesting a proposed prompt is a validation error.
    async with session_factory() as session:
        with pytest.raises(AuditValidationError):
            await create_audit(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                engines=seed.engines,
                prompt_set_id=seed.prompt_set_id,
                prompt_ids=[seed.prompt_ids[0]],
                repetitions=1,
            )
