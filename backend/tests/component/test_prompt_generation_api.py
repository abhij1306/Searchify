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
from typing import cast

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.api.prompts as prompts_api
from app.connectors.agent.client import AgentNotConfiguredError, DefaultAgentClient
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
    _project, prompt_set_id = await _make_project_and_set(client, "gen1@example.com")

    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 3, "confirm_send_evidence": True},
    )
    assert resp.status_code == 201
    body = resp.json()

    assert body["dropped_duplicates"] == 0
    assert len(body["generated"]) == 3
    by_status = {p["text"]: p["status"] for p in body["generated"]}
    # Fresh set, 3 < the 20-active pool -> unbranded prompts promoted to
    # active. The branded comparison prompt stays proposed: the branded cap
    # applies to the post-activation pool (int(2 * 0.2) = 0 slots), not the
    # configured threshold.
    assert by_status["best running shoes in australia"] == "active"
    assert by_status["how do running shoe sizes work"] == "active"
    assert by_status["acme vs globex running shoes"] == "proposed"
    for prompt in body["generated"]:
        assert prompt["origin"] == "generated"
        assert prompt["topic_id"] is not None
    assert {t["name"] for t in body["topics"]} == {"Footwear", "Sizing"}
    footwear = next(t for t in body["topics"] if t["name"] == "Footwear")
    assert footwear["origin"] == "generated"
    assert footwear["active_count"] == 1
    assert footwear["proposed_count"] == 1

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
        assert evidence is not None
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


def _agent_response_with_n_prompts(n: int, *, topic: str = "Bulk") -> str:
    """A single-topic response carrying ``n`` distinct prompts.

    Texts embed the topic so responses from different runs never collide on
    the per-set dedupe hash (letting a test insert fresh rows each run).
    """
    return json.dumps(
        {
            "topics": [
                {
                    "name": topic,
                    "prompts": [
                        {"text": f"{topic} prompt number {i}", "intent": "discovery"}
                        for i in range(n)
                    ],
                }
            ]
        }
    )


@pytest.mark.asyncio
async def test_generate_activates_only_up_to_pool_then_proposed(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First 20 generated prompts become active; the rest stay proposed."""
    _, prompt_set_id = await _make_project_and_set(client, "pool1@example.com")
    agent = FakeAgent(response=_agent_response_with_n_prompts(25))
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: agent)

    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 20, "confirm_send_evidence": True},
    )
    assert resp.status_code == 201
    body = resp.json()
    # Model returned 25 but only 20 were requested -> output trimmed to 20.
    assert len(body["generated"]) == 20
    statuses = [p["status"] for p in body["generated"]]
    # The set-wide active pool is 20, so all 20 are active.
    assert statuses == ["active"] * 20


@pytest.mark.asyncio
async def test_generate_second_run_stays_proposed_when_pool_full(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once the 20-active pool is full, later generations land proposed."""
    _, prompt_set_id = await _make_project_and_set(client, "pool2@example.com")

    first_agent = FakeAgent(response=_agent_response_with_n_prompts(20, topic="One"))
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: first_agent)
    first = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 20, "confirm_send_evidence": True},
    )
    assert first.status_code == 201
    assert {p["status"] for p in first.json()["generated"]} == {"active"}

    second_agent = FakeAgent(response=_agent_response_with_n_prompts(5, topic="Two"))
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: second_agent)
    second = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 5, "confirm_send_evidence": True},
    )
    assert second.status_code == 201
    # Pool already full from run 1 -> every new prompt stays proposed.
    assert {p["status"] for p in second.json()["generated"]} == {"proposed"}


@pytest.mark.asyncio
async def test_generate_caps_branded_share_of_active_pool(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Branded prompts fill at most 20% of the post-activation active pool;
    the slots they would have taken go to later unbranded prompts, and the
    skipped branded rows stay proposed."""
    _, prompt_set_id = await _make_project_and_set(client, "brandcap@example.com")
    # 10 branded prompts first, then 10 unbranded. Pool lands at 12 active
    # (2 branded + 10 unbranded): cap iterates to int(12 * 0.2) = 2.
    branded_prompts = [
        {"text": f"is Acme Corp good for use case {i}", "intent": "discovery"}
        for i in range(10)
    ]
    unbranded_prompts = [
        {"text": f"best running shoes for terrain {i}", "intent": "discovery"}
        for i in range(10)
    ]
    all_prompts = branded_prompts + unbranded_prompts
    agent = FakeAgent(
        response=json.dumps({"topics": [{"name": "Mix", "prompts": all_prompts}]})
    )
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: agent)

    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 20, "confirm_send_evidence": True},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["generated"]) == 20

    by_text = {p["text"]: p for p in body["generated"]}
    active_branded = [
        p for p in body["generated"] if p["branded"] and p["status"] == "active"
    ]
    # Projected pool = 12 active -> branded cap = int(12 * 0.2) = 2; the
    # first 2 branded rows take the slots.
    assert len(active_branded) == 2
    for i in range(2):
        assert by_text[f"is Acme Corp good for use case {i}"]["status"] == "active"
    # Branded rows beyond the cap stay proposed even though pool slots remained.
    for i in range(2, 10):
        assert by_text[f"is Acme Corp good for use case {i}"]["status"] == "proposed"
    # Every unbranded row is activated (2 branded + 10 unbranded = 12 <= 20).
    for i in range(10):
        assert by_text[f"best running shoes for terrain {i}"]["status"] == "active"


@pytest.mark.asyncio
async def test_generate_branded_cap_holds_for_undersized_pool(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An initially empty pool that stays below the threshold still honors the
    branded share against the pool that actually exists after activation, not
    the configured threshold."""
    _, prompt_set_id = await _make_project_and_set(client, "brandcap2@example.com")
    # Only 5 prompts (3 branded + 2 unbranded) into an empty pool. Capping
    # against threshold alone (int(20 * 0.2) = 4) would activate all 3 branded
    # -> a 5-row pool at 60% branded. Against the projected pool the cap
    # iterates down to int(2 * 0.2) = 0: no branded row auto-activates.
    prompts = [
        {"text": f"is Acme Corp good for use case {i}", "intent": "discovery"}
        for i in range(3)
    ] + [
        {"text": f"best running shoes for terrain {i}", "intent": "discovery"}
        for i in range(2)
    ]
    agent = FakeAgent(
        response=json.dumps({"topics": [{"name": "Mix", "prompts": prompts}]})
    )
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: agent)

    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 5, "confirm_send_evidence": True},
    )
    assert resp.status_code == 201
    by_text = {p["text"]: p for p in resp.json()["generated"]}
    for i in range(3):
        assert by_text[f"is Acme Corp good for use case {i}"]["status"] == "proposed"
    for i in range(2):
        assert by_text[f"best running shoes for terrain {i}"]["status"] == "active"


@pytest.mark.asyncio
async def test_generate_branded_cap_allows_share_of_undersized_pool(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pool below threshold still admits branded rows up to its own share."""
    _, prompt_set_id = await _make_project_and_set(client, "brandcap3@example.com")
    # 4 branded + 6 unbranded into an empty pool. Fixed point: 1 branded + 6
    # unbranded = 7 active, int(7 * 0.2) = 1 branded allowed.
    prompts = [
        {"text": f"is Acme Corp good for use case {i}", "intent": "discovery"}
        for i in range(4)
    ] + [
        {"text": f"best running shoes for terrain {i}", "intent": "discovery"}
        for i in range(6)
    ]
    agent = FakeAgent(
        response=json.dumps({"topics": [{"name": "Mix", "prompts": prompts}]})
    )
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: agent)

    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 10, "confirm_send_evidence": True},
    )
    assert resp.status_code == 201
    by_text = {p["text"]: p for p in resp.json()["generated"]}
    assert by_text["is Acme Corp good for use case 0"]["status"] == "active"
    for i in range(1, 4):
        assert by_text[f"is Acme Corp good for use case {i}"]["status"] == "proposed"
    for i in range(6):
        assert by_text[f"best running shoes for terrain {i}"]["status"] == "active"


@pytest.mark.asyncio
async def test_generate_manual_active_rows_count_toward_pool(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing active (manual) prompts consume slots in the 20-pool."""
    _, prompt_set_id = await _make_project_and_set(client, "pool3@example.com")
    # Seed 18 manual prompts (created active by default).
    for i in range(18):
        created = await client.post(
            f"/api/v1/prompt-sets/{prompt_set_id}/prompts",
            json={"text": f"manual prompt {i}"},
        )
        assert created.status_code == 201

    agent = FakeAgent(response=_agent_response_with_n_prompts(5, topic="Gen"))
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: agent)
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 5, "confirm_send_evidence": True},
    )
    assert resp.status_code == 201
    statuses = [p["status"] for p in resp.json()["generated"]]
    # Only 2 slots remain (18 active + 2 = 20) -> first 2 active, rest proposed.
    assert statuses == ["active", "active", "proposed", "proposed", "proposed"]


@pytest.mark.asyncio
async def test_generate_counts_intra_response_duplicates(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate texts within one model response are counted as dropped."""
    _, prompt_set_id = await _make_project_and_set(client, "dup1@example.com")
    agent = FakeAgent(
        response=json.dumps(
            {
                "topics": [
                    {
                        "name": "A",
                        "prompts": [
                            {"text": "Best Shoes?", "intent": "discovery"},
                            {"text": "best  shoes", "intent": "discovery"},
                            {"text": "hiking boots", "intent": "discovery"},
                        ],
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: agent)
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 5, "confirm_send_evidence": True},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["generated"]) == 2  # the collapsed duplicate is gone
    assert body["dropped_duplicates"] == 1


@pytest.mark.asyncio
async def test_generate_bounds_existing_prompt_context(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing-prompt list sent to the model is capped by config."""
    from app.core.config.prompts import prompt_generation_settings

    _, prompt_set_id = await _make_project_and_set(client, "ctx1@example.com")
    monkeypatch.setattr(prompt_generation_settings, "existing_prompt_context_limit", 3)
    for i in range(6):
        created = await client.post(
            f"/api/v1/prompt-sets/{prompt_set_id}/prompts",
            json={"text": f"existing context prompt {i}"},
        )
        assert created.status_code == 201

    agent = FakeAgent(response=_agent_response_with_n_prompts(2, topic="New"))
    monkeypatch.setattr(prompts_api, "DefaultAgentClient", lambda: agent)
    resp = await client.post(
        f"/api/v1/prompt-sets/{prompt_set_id}/generate",
        json={"count": 2, "confirm_send_evidence": True},
    )
    assert resp.status_code == 201
    sent = agent.calls[0]["user"]
    # Only the first 3 existing prompts appear in the "do NOT duplicate" block.
    included = [i for i in range(6) if f"existing context prompt {i}" in sent]
    assert len(included) == 3


@pytest.mark.asyncio
async def test_concurrent_generation_never_exceeds_active_pool(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two overlapping generations must not push active past the 20-pool."""
    import asyncio

    from app.domain.prompts.generation import generate_prompts
    from app.domain.prompts.schemas import PromptGenerateRequest

    project, prompt_set_id = await _make_project_and_set(client, "conc1@example.com")

    # Resolve the workspace id from the project's owning workspace.
    from app.models.project import Project

    async with session_factory() as session:
        proj = await session.get(Project, uuid.UUID(project["id"]))
        assert proj is not None
        workspace_id = proj.workspace_id

    class _CountingAgent:
        model = "fake-model"
        base_url_host = "agent.test"

        def __init__(self, topic: str, n: int) -> None:
            self._response = _agent_response_with_n_prompts(n, topic=topic)

        async def complete_json(self, *, system: str, user: str) -> str:
            # Yield so both coroutines interleave before either persists.
            await asyncio.sleep(0)
            return self._response

    async def _run(topic: str, n: int) -> None:
        async with session_factory() as session:
            await generate_prompts(
                session,
                workspace_id=workspace_id,
                prompt_set_id=uuid.UUID(prompt_set_id),
                payload=PromptGenerateRequest(count=n, confirm_send_evidence=True),
                agent=cast(DefaultAgentClient, _CountingAgent(topic, n)),
            )

    await asyncio.gather(_run("Alpha", 15), _run("Beta", 15))

    async with session_factory() as session:
        active = (
            (
                await session.execute(
                    select(Prompt).where(
                        Prompt.prompt_set_id == uuid.UUID(prompt_set_id),
                        Prompt.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
    # 30 prompts inserted total, but the set-wide active pool is capped at 20.
    assert len(active) == 20


@pytest.mark.asyncio
async def test_generation_racing_prompt_set_delete_is_scoped_not_found(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Delete the set mid-generation (provider paused) -> scoped 404, not 500."""
    import asyncio

    from app.domain.prompts.generation import generate_prompts
    from app.domain.prompts.schemas import PromptGenerateRequest
    from app.domain.prompts.service import (
        PromptSetNotFoundError,
        delete_prompt_set,
    )
    from app.models.project import Project

    project, prompt_set_id = await _make_project_and_set(client, "race1@example.com")
    async with session_factory() as session:
        proj = await session.get(Project, uuid.UUID(project["id"]))
        assert proj is not None
        workspace_id = proj.workspace_id

    provider_entered = asyncio.Event()
    delete_done = asyncio.Event()

    class _PausingAgent:
        model = "fake-model"
        base_url_host = "agent.test"

        async def complete_json(self, *, system: str, user: str) -> str:
            # Signal that the read txn is committed, then wait until the set
            # has been deleted before returning (so generation re-resolves a
            # set that no longer exists).
            provider_entered.set()
            await delete_done.wait()
            return _agent_response_with_n_prompts(3, topic="Race")

    async def _generate() -> BaseException | None:
        async with session_factory() as session:
            try:
                await generate_prompts(
                    session,
                    workspace_id=workspace_id,
                    prompt_set_id=uuid.UUID(prompt_set_id),
                    payload=PromptGenerateRequest(count=3, confirm_send_evidence=True),
                    agent=cast(DefaultAgentClient, _PausingAgent()),
                )
                return None
            except BaseException as exc:  # noqa: BLE001
                return exc

    async def _delete() -> None:
        await provider_entered.wait()
        async with session_factory() as session:
            await delete_prompt_set(
                session,
                workspace_id=workspace_id,
                prompt_set_id=uuid.UUID(prompt_set_id),
            )
        delete_done.set()

    gen_result, _ = await asyncio.gather(_generate(), _delete())
    # Disappearance surfaces as the scoped domain error the endpoint maps to
    # 404 — never an unhandled FK 500.
    assert isinstance(gen_result, PromptSetNotFoundError)


@pytest.mark.asyncio
async def test_generation_racing_topic_delete_is_scoped_validation_error(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Delete the target topic mid-generation (paused) -> scoped 422, not 500."""
    import asyncio

    from app.domain.prompts.generation import (
        GenerationValidationError,
        generate_prompts,
    )
    from app.domain.prompts.schemas import PromptGenerateRequest
    from app.domain.prompts.topics import delete_topic
    from app.models.project import Project

    project, prompt_set_id = await _make_project_and_set(client, "race2@example.com")
    topic = (
        await client.post(
            f"/api/v1/projects/{project['id']}/topics", json={"name": "Doomed"}
        )
    ).json()
    async with session_factory() as session:
        proj = await session.get(Project, uuid.UUID(project["id"]))
        assert proj is not None
        workspace_id = proj.workspace_id

    provider_entered = asyncio.Event()
    delete_done = asyncio.Event()

    class _PausingAgent:
        model = "fake-model"
        base_url_host = "agent.test"

        async def complete_json(self, *, system: str, user: str) -> str:
            provider_entered.set()
            await delete_done.wait()
            return _agent_response_with_n_prompts(2, topic="Whatever")

    async def _generate() -> BaseException | None:
        async with session_factory() as session:
            try:
                await generate_prompts(
                    session,
                    workspace_id=workspace_id,
                    prompt_set_id=uuid.UUID(prompt_set_id),
                    payload=PromptGenerateRequest(
                        count=2,
                        confirm_send_evidence=True,
                        topic_id=uuid.UUID(topic["id"]),
                    ),
                    agent=cast(DefaultAgentClient, _PausingAgent()),
                )
                return None
            except BaseException as exc:  # noqa: BLE001
                return exc

    async def _delete() -> None:
        await provider_entered.wait()
        async with session_factory() as session:
            await delete_topic(
                session,
                workspace_id=workspace_id,
                topic_id=uuid.UUID(topic["id"]),
            )
        delete_done.set()

    gen_result, _ = await asyncio.gather(_generate(), _delete())
    # Target topic gone -> scoped validation error the endpoint maps to 422.
    assert isinstance(gen_result, GenerationValidationError)
    # No prompts were persisted into the vanished topic.
    async with session_factory() as session:
        remaining = (
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
    assert remaining == []


@pytest.mark.asyncio
async def test_generation_unrelated_integrity_error_is_not_remapped(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An IntegrityError unrelated to a lost set/topic FK must NOT be masked.

    When the referenced set and (unscoped) topics are all still present, an
    insert-time integrity error is a genuine constraint bug, so it must
    re-raise as ``IntegrityError`` — never a phantom ``PromptSetNotFoundError``
    (404) or ``GenerationValidationError`` (422).
    """
    from sqlalchemy.exc import IntegrityError

    import app.domain.prompts.generation as generation
    from app.domain.prompts.generation import generate_prompts
    from app.domain.prompts.schemas import PromptGenerateRequest
    from app.models.project import Project

    project, prompt_set_id = await _make_project_and_set(client, "unrel1@example.com")
    async with session_factory() as session:
        proj = await session.get(Project, uuid.UUID(project["id"]))
        assert proj is not None
        workspace_id = proj.workspace_id

    async def _boom(*args: object, **kwargs: object) -> object:
        raise IntegrityError("boom", params=None, orig=Exception("unrelated"))

    monkeypatch.setattr(generation, "_insert_prompts_returning", _boom)

    with pytest.raises(IntegrityError):
        async with session_factory() as session:
            await generate_prompts(
                session,
                workspace_id=workspace_id,
                prompt_set_id=uuid.UUID(prompt_set_id),
                payload=PromptGenerateRequest(count=2, confirm_send_evidence=True),
                agent=cast(
                    DefaultAgentClient,
                    FakeAgent(response=_agent_response_with_n_prompts(2, topic="Keep")),
                ),
            )


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
async def test_prompt_topic_assignment_same_project_succeeds(
    client: httpx.AsyncClient,
) -> None:
    """A prompt can be filed under a topic of its own project."""
    project, prompt_set_id = await _make_project_and_set(client, "tscope1@example.com")
    topic = (
        await client.post(
            f"/api/v1/projects/{project['id']}/topics", json={"name": "Footwear"}
        )
    ).json()
    prompt = (
        await client.post(
            f"/api/v1/prompt-sets/{prompt_set_id}/prompts",
            json={"text": "best hiking boots"},
        )
    ).json()
    resp = await client.patch(
        f"/api/v1/prompts/{prompt['id']}", json={"topic_id": topic["id"]}
    )
    assert resp.status_code == 200
    assert resp.json()["topic_id"] == topic["id"]
    # Detaching (topic_id=null) is always allowed.
    detached = await client.patch(
        f"/api/v1/prompts/{prompt['id']}", json={"topic_id": None}
    )
    assert detached.status_code == 200
    assert detached.json()["topic_id"] is None


@pytest.mark.asyncio
async def test_prompt_topic_assignment_cross_project_rejected(
    client: httpx.AsyncClient,
) -> None:
    """A topic from a sibling project (same workspace) can't be attached."""
    await _register(client, "tscope2@example.com")
    project_a = (
        await client.post("/api/v1/projects", json=_project_payload(name="A"))
    ).json()
    prompt_set_a = (
        await client.post(
            "/api/v1/prompt-sets",
            json={"project_id": project_a["id"], "name": "SetA"},
        )
    ).json()["id"]
    project_b = (
        await client.post(
            "/api/v1/projects",
            json=_project_payload(
                name="B", brand_name="Beta", website_url="https://beta.example"
            ),
        )
    ).json()
    topic_b = (
        await client.post(
            f"/api/v1/projects/{project_b['id']}/topics", json={"name": "Other"}
        )
    ).json()

    prompt = (
        await client.post(
            f"/api/v1/prompt-sets/{prompt_set_a}/prompts",
            json={"text": "cross project prompt"},
        )
    ).json()
    resp = await client.patch(
        f"/api/v1/prompts/{prompt['id']}", json={"topic_id": topic_b["id"]}
    )
    assert resp.status_code == 404
    # The assignment did not persist.
    listed = (await client.get(f"/api/v1/prompt-sets/{prompt_set_a}")).json()
    assert listed["prompts"][0]["topic_id"] is None


@pytest.mark.asyncio
async def test_prompt_topic_assignment_cross_workspace_rejected(
    client: httpx.AsyncClient,
) -> None:
    """A topic from another workspace can't be attached to this prompt."""
    # Workspace 1 owns the topic.
    other_project, _ = await _make_project_and_set(client, "tscope3a@example.com")
    other_topic = (
        await client.post(
            f"/api/v1/projects/{other_project['id']}/topics", json={"name": "Theirs"}
        )
    ).json()

    # Workspace 2 owns the prompt.
    client.cookies.clear()
    _, prompt_set_id = await _make_project_and_set(client, "tscope3b@example.com")
    prompt = (
        await client.post(
            f"/api/v1/prompt-sets/{prompt_set_id}/prompts",
            json={"text": "my prompt"},
        )
    ).json()
    resp = await client.patch(
        f"/api/v1/prompts/{prompt['id']}", json={"topic_id": other_topic["id"]}
    )
    assert resp.status_code == 404
    listed = (await client.get(f"/api/v1/prompt-sets/{prompt_set_id}")).json()
    assert listed["prompts"][0]["topic_id"] is None


@pytest.mark.asyncio
async def test_prompt_topic_assignment_unknown_topic_rejected(
    client: httpx.AsyncClient,
) -> None:
    """A non-existent topic id is rejected (no cross-scope FK 500)."""
    _, prompt_set_id = await _make_project_and_set(client, "tscope4@example.com")
    prompt = (
        await client.post(
            f"/api/v1/prompt-sets/{prompt_set_id}/prompts",
            json={"text": "orphan prompt"},
        )
    ).json()
    resp = await client.patch(
        f"/api/v1/prompts/{prompt['id']}", json={"topic_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 404


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
            assert prompt is not None
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
