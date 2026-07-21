"""Component tests for the stateless brand-suggestion endpoints (setup-form AI).

The default agent is always faked at the API boundary
(``app.api.brand_suggestions.DefaultAgentClient``) so no test ever performs
live provider I/O, regardless of what keys exist in the developer's ``.env``.

Covers:
  - competitor / owned-domain happy paths (brand context reaches the agent);
  - backend-enforced ``confirm_send_evidence`` + count cap (422, agent never
    called);
  - unconfigured agent -> 503, but an invalid payload -> 422 first;
  - unparseable model output -> 502;
  - dedupe against the ``existing_*`` lists sent in the request body;
  - unauthenticated requests are rejected.
"""

from __future__ import annotations

import json

import httpx
import pytest

import app.api.brand_suggestions as brand_suggestions_api
from app.connectors.agent.client import AgentNotConfiguredError

VALID_COMPETITOR_RESPONSE = json.dumps(
    {
        "competitors": [
            {"name": "Globex", "aliases": ["Globex Co"], "domains": ["globex.com"]},
            {"name": "Initech", "aliases": [], "domains": ["https://www.initech.com/"]},
        ]
    }
)

VALID_DOMAIN_RESPONSE = json.dumps(
    {"domains": ["acme.com", "https://www.acme.co.uk/", "Acme.io"]}
)


class FakeAgent:
    """Stands in for DefaultAgentClient; records calls, returns a canned body."""

    model = "fake-model"
    base_url_host = "agent.test"

    def __init__(self, response: str = VALID_COMPETITOR_RESPONSE) -> None:
        self.response = response
        self.calls: list[dict[str, str]] = []

    async def complete_json(self, *, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        return self.response


@pytest.fixture
def fake_agent(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    agent = FakeAgent()
    monkeypatch.setattr(brand_suggestions_api, "DefaultAgentClient", lambda: agent)
    return agent


async def _register(client: httpx.AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201


def _competitor_payload(**overrides: object) -> dict:
    payload = {
        "brand_name": "Acme Corp",
        "website_url": "https://acme.com",
        "brand_aliases": ["Acme", "ACME Inc"],
        "country_code": "AU",
        "language_code": "en-AU",
        "confirm_send_evidence": True,
        "existing_competitor_names": [],
    }
    payload.update(overrides)
    return payload


def _domain_payload(**overrides: object) -> dict:
    payload = {
        "brand_name": "Acme Corp",
        "website_url": "https://acme.com",
        "confirm_send_evidence": True,
        "existing_owned_domains": [],
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_suggest_competitors_happy_path(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    await _register(client, "suggest1@example.com")

    resp = await client.post(
        "/api/v1/brand-suggestions/competitors", json=_competitor_payload()
    )

    assert resp.status_code == 201
    body = resp.json()
    assert [c["name"] for c in body["competitors"]] == ["Globex", "Initech"]
    # Agent URLs are normalized to bare domains before they reach the form.
    assert body["competitors"][1]["domains"] == ["initech.com"]
    assert body["dropped_duplicates"] == 0
    # Brand evidence reached the agent's user message (after consent).
    assert len(fake_agent.calls) == 1
    assert "Acme Corp" in fake_agent.calls[0]["user"]
    assert "AU" in fake_agent.calls[0]["user"]


@pytest.mark.asyncio
async def test_suggest_owned_domains_happy_path(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    await _register(client, "suggest2@example.com")
    fake_agent.response = VALID_DOMAIN_RESPONSE

    resp = await client.post(
        "/api/v1/brand-suggestions/owned-domains", json=_domain_payload()
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["domains"] == ["acme.com", "acme.co.uk", "acme.io"]
    assert body["dropped_duplicates"] == 0


# --------------------------------------------------------------------------
# Consent gate + bounds (422, agent never called)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_suggest_requires_evidence_confirmation(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    await _register(client, "suggest3@example.com")

    resp = await client.post(
        "/api/v1/brand-suggestions/competitors",
        json=_competitor_payload(confirm_send_evidence=False),
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "suggestion_invalid"
    assert fake_agent.calls == []


@pytest.mark.asyncio
async def test_suggest_rejects_count_over_cap(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    await _register(client, "suggest4@example.com")

    resp = await client.post(
        "/api/v1/brand-suggestions/owned-domains",
        json=_domain_payload(count=10_000),
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "suggestion_invalid"
    assert fake_agent.calls == []


# --------------------------------------------------------------------------
# Agent configuration (503) + precedence
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_suggest_unconfigured_agent_returns_503(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _register(client, "suggest5@example.com")

    def _raise() -> None:
        raise AgentNotConfiguredError("no key")

    monkeypatch.setattr(brand_suggestions_api, "DefaultAgentClient", _raise)

    resp = await client.post(
        "/api/v1/brand-suggestions/competitors", json=_competitor_payload()
    )

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == "agent_not_configured"
    assert "DEFAULT_AGENT_API_KEY" in detail["message"]


@pytest.mark.asyncio
async def test_suggest_invalid_payload_is_422_even_when_unconfigured(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _register(client, "suggest6@example.com")

    def _raise() -> None:
        raise AgentNotConfiguredError("no key")

    monkeypatch.setattr(brand_suggestions_api, "DefaultAgentClient", _raise)

    resp = await client.post(
        "/api/v1/brand-suggestions/competitors",
        json=_competitor_payload(confirm_send_evidence=False),
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "suggestion_invalid"


# --------------------------------------------------------------------------
# Unparseable output (502)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_suggest_unparseable_output_returns_502(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _register(client, "suggest7@example.com")
    agent = FakeAgent(response="this is not json")
    monkeypatch.setattr(brand_suggestions_api, "DefaultAgentClient", lambda: agent)

    resp = await client.post(
        "/api/v1/brand-suggestions/owned-domains", json=_domain_payload()
    )

    assert resp.status_code == 502
    assert resp.json()["detail"]["code"] == "suggestion_unparseable"


# --------------------------------------------------------------------------
# Dedupe against existing form values
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_suggest_competitors_dedupes_against_existing(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    await _register(client, "suggest8@example.com")

    resp = await client.post(
        "/api/v1/brand-suggestions/competitors",
        json=_competitor_payload(existing_competitor_names=["GLOBEX"]),
    )

    assert resp.status_code == 201
    body = resp.json()
    assert [c["name"] for c in body["competitors"]] == ["Initech"]
    assert body["dropped_duplicates"] == 1


@pytest.mark.asyncio
async def test_suggest_owned_domains_dedupes_against_existing(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    await _register(client, "suggest9@example.com")
    fake_agent.response = VALID_DOMAIN_RESPONSE

    resp = await client.post(
        "/api/v1/brand-suggestions/owned-domains",
        json=_domain_payload(existing_owned_domains=["ACME.com", "acme.io"]),
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["domains"] == ["acme.co.uk"]
    assert body["dropped_duplicates"] == 2


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_suggest_requires_authentication(
    client: httpx.AsyncClient, fake_agent: FakeAgent
) -> None:
    resp = await client.post(
        "/api/v1/brand-suggestions/competitors", json=_competitor_payload()
    )

    assert resp.status_code == 401
    assert fake_agent.calls == []
