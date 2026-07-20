"""Component tests for the /content API + content worker (real Postgres).

Drives the full vertical through the HTTP surface (httpx ASGITransport) and
the real ``ContentWorker`` with the real ``MistralDiscoveryClient`` over an
``httpx.MockTransport`` — no live network, no fake-provider branch in app
code. Covers enqueue validation + idempotency, list/detail scoping, cancel
rules, worker attempt accounting (atomic ``finalize_attempt``), retry budget,
cancelled-in-flight, lost-lease immutability, regenerate vs try-again, and
the no-key-leak guarantee.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config.content import (
    CONTENT_LIST_MAX_LIMIT,
    CONTENT_MAX_ATTEMPTS,
    content_settings,
)
from app.core.config.task_queue import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RETRY_WAIT,
    TASK_STATUS_SUCCEEDED,
)
from app.models.content import ContentGeneration, ContentGenerationAttempt
from app.workers.content_worker import ContentWorker

_API_KEY = "test-mistral-key-abc123"


@pytest.fixture(autouse=True)
def _configured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with a configured provider; tests that need the
    unconfigured 409 clear it locally."""
    monkeypatch.setattr(content_settings, "mistral_api_key", SecretStr(_API_KEY))


async def _register(client: httpx.AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201


async def _create_project(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/api/v1/projects",
        json={
            "name": "Content Project",
            "brand_name": "Acme",
            "website_url": "https://acme.com",
            "country_code": "AU",
            "language_code": "en-AU",
            "benchmark_mode": "consumer_like",
            "default_repetitions": 1,
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _enqueue(
    client: httpx.AsyncClient,
    project_id: str,
    *,
    prompt: str = "Write a landing page for Acme.",
    headers: dict | None = None,
    **overrides: object,
) -> httpx.Response:
    body = {"project_id": project_id, "prompt": prompt, **overrides}
    return await client.post(
        "/api/v1/content/generations", json=body, headers=headers or {}
    )


def _mock_transport(
    *,
    content: str = "# Hello\n\nGenerated page.",
    finish_reason: str = "stop",
    model: str = "mistral-small-latest",
    status_code: int = 200,
    seen: list[httpx.Request] | None = None,
    responses: list[dict] | None = None,
) -> httpx.MockTransport:
    """OpenAI-compatible chat-completions mock. ``responses`` (a list of
    per-call overrides) lets one test script successive outcomes."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        spec: dict = {}
        if responses is not None:
            spec = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        code = int(spec.get("status_code", status_code))
        if code >= 400:
            return httpx.Response(code, json={"error": "boom"})
        return httpx.Response(
            200,
            json={
                "model": spec.get("model", model),
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": spec.get("content", content),
                        },
                        "finish_reason": spec.get("finish_reason", finish_reason),
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                },
            },
        )

    return httpx.MockTransport(handler)


def _worker(
    session_factory: async_sessionmaker[AsyncSession],
    transport: httpx.MockTransport,
) -> ContentWorker:
    return ContentWorker(
        session_factory=session_factory, owner="test-owner", transport=transport
    )


async def _get_generation(
    session: AsyncSession, generation_id: uuid.UUID
) -> ContentGeneration:
    """Fetch a row that the test knows exists (asserts non-None for mypy)."""
    row = await session.get(ContentGeneration, generation_id)
    assert row is not None
    return row


# --- Enqueue + validation --------------------------------------------------


async def test_enqueue_returns_queued_detail(client: httpx.AsyncClient) -> None:
    await _register(client, "c1@example.com")
    project_id = await _create_project(client)
    resp = await _enqueue(client, project_id)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == TASK_STATUS_QUEUED
    assert body["project_id"] == project_id
    assert body["output_type"] == "website_page"
    assert body["website_context_enabled"] is True
    # No crawl evidence exists -> context unavailable, generation prompt-only.
    assert body["website_context_status"] == "unavailable"
    assert body["requested_model"] == content_settings.model
    assert body["provider"] == "mistral"
    assert body["returned_model"] is None
    assert body["output_text"] is None
    assert body["output_truncated"] is False
    assert body["prompt_preview"].startswith("Write a landing page")
    assert _API_KEY not in resp.text


async def test_enqueue_validation_422s(client: httpx.AsyncClient) -> None:
    await _register(client, "c2@example.com")
    project_id = await _create_project(client)
    assert (await _enqueue(client, project_id, prompt="   ")).status_code == 422
    assert (await _enqueue(client, project_id, prompt="x" * 5000)).status_code == 422
    assert (await _enqueue(client, project_id, output_type="tweet")).status_code == 422


async def test_enqueue_unknown_project_404(client: httpx.AsyncClient) -> None:
    await _register(client, "c3@example.com")
    await _create_project(client)
    resp = await _enqueue(client, str(uuid.uuid4()))
    assert resp.status_code == 404


async def test_enqueue_provider_not_configured_409(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _register(client, "c4@example.com")
    project_id = await _create_project(client)
    monkeypatch.setattr(content_settings, "mistral_api_key", SecretStr(""))
    resp = await _enqueue(client, project_id)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "provider_not_configured"


async def test_website_context_disabled_toggle(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "c5@example.com")
    project_id = await _create_project(client)
    resp = await _enqueue(client, project_id, website_context_enabled=False)
    assert resp.status_code == 201
    body = resp.json()
    assert body["website_context_enabled"] is False
    assert body["website_context_status"] == "disabled"
    assert body["website_context_summary"] is None


# --- Idempotency -----------------------------------------------------------


async def test_idempotency_replay_and_conflict(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "c6@example.com")
    project_id = await _create_project(client)
    headers = {"Idempotency-Key": "my-key-1"}
    first = await _enqueue(client, project_id, headers=headers)
    assert first.status_code == 201
    replay = await _enqueue(client, project_id, headers=headers)
    # Same key + same fingerprint -> the SAME record, not a new one.
    assert replay.json()["id"] == first.json()["id"]
    conflict = await _enqueue(
        client, project_id, prompt="A different prompt.", headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "idempotency_conflict"


async def test_keyless_requests_never_collide(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "c7@example.com")
    project_id = await _create_project(client)
    a = await _enqueue(client, project_id)
    b = await _enqueue(client, project_id)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


async def test_same_key_across_workspaces_ok(
    client: httpx.AsyncClient,
) -> None:
    """The composite (workspace_id, idempotency_key) allows key reuse."""
    await _register(client, "ws-a@example.com")
    project_a = await _create_project(client)
    headers = {"Idempotency-Key": "shared-key"}
    a = await _enqueue(client, project_a, headers=headers)
    assert a.status_code == 201

    client.cookies.clear()
    await _register(client, "ws-b@example.com")
    project_b = await _create_project(client)
    b = await _enqueue(client, project_b, headers=headers)
    assert b.status_code == 201
    assert b.json()["id"] != a.json()["id"]


# --- List + detail ---------------------------------------------------------


async def test_list_bounded_newest_first_no_output(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "c8@example.com")
    project_id = await _create_project(client)
    ids = []
    for i in range(3):
        resp = await _enqueue(client, project_id, prompt=f"Prompt {i}")
        ids.append(resp.json()["id"])
    listing = await client.get(
        "/api/v1/content/generations",
        params={"project_id": project_id, "limit": 2},
    )
    assert listing.status_code == 200
    items = listing.json()
    assert len(items) == 2
    assert items[0]["id"] == ids[-1]  # newest first
    assert "output_text" not in items[0]
    assert "prompt" not in items[0]
    assert items[0]["prompt_preview"] == "Prompt 2"

    # limit above the max is rejected by the query validator.
    over = await client.get(
        "/api/v1/content/generations",
        params={"project_id": project_id, "limit": CONTENT_LIST_MAX_LIMIT + 1},
    )
    assert over.status_code == 422

    missing = await client.get(
        "/api/v1/content/generations",
        params={"project_id": str(uuid.uuid4())},
    )
    assert missing.status_code == 404


async def test_detail_cross_workspace_404(client: httpx.AsyncClient) -> None:
    await _register(client, "owner-1@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()

    detail = await client.get(f"/api/v1/content/generations/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["prompt"] == "Write a landing page for Acme."

    client.cookies.clear()
    await _register(client, "intruder@example.com")
    stolen = await client.get(f"/api/v1/content/generations/{created['id']}")
    assert stolen.status_code == 404
    rand = await client.get(f"/api/v1/content/generations/{uuid.uuid4()}")
    assert rand.status_code == 404


# --- Cancel ----------------------------------------------------------------


async def test_cancel_queued_and_terminal_conflict(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "c9@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()
    cancelled = await client.post(f"/api/v1/content/generations/{created['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == TASK_STATUS_CANCELLED
    again = await client.post(f"/api/v1/content/generations/{created['id']}/cancel")
    assert again.status_code == 409
    assert again.json()["detail"] == "cancel_not_allowed"


async def test_cancel_cross_workspace_404(client: httpx.AsyncClient) -> None:
    await _register(client, "c10@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()
    client.cookies.clear()
    await _register(client, "c10-intruder@example.com")
    resp = await client.post(f"/api/v1/content/generations/{created['id']}/cancel")
    assert resp.status_code == 404


# --- Worker ----------------------------------------------------------------


async def test_worker_success_single_attempt(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "w1@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()

    seen: list[httpx.Request] = []
    worker = _worker(session_factory, _mock_transport(seen=seen))
    ran = await worker.run_until_idle()
    assert ran == 1

    detail = (await client.get(f"/api/v1/content/generations/{created['id']}")).json()
    assert detail["status"] == TASK_STATUS_SUCCEEDED
    assert detail["output_text"].startswith("# Hello")
    assert detail["provider"] == "mistral"
    assert detail["requested_model"] == content_settings.model
    assert detail["returned_model"] == "mistral-small-latest"
    assert detail["finish_reason"] == "stop"
    assert detail["output_truncated"] is False
    assert detail["usage"]["total_tokens"] == 30
    assert detail["latency_ms"] is not None
    assert detail["completed_at"] is not None

    # Exactly one attempt row, one counter increment, and the key flowed to
    # the provider only via the Authorization header.
    async with session_factory() as session:
        row = await _get_generation(session, uuid.UUID(created["id"]))
        attempts = (
            await session.scalars(
                select(ContentGenerationAttempt).where(
                    ContentGenerationAttempt.content_generation_id == row.id
                )
            )
        ).all()
    assert row.attempt_count == 1
    assert len(attempts) == 1
    assert attempts[0].attempt_number == 1
    assert attempts[0].status == "succeeded"
    assert attempts[0].returned_model == "mistral-small-latest"
    assert seen[0].headers["authorization"] == f"Bearer {_API_KEY}"
    assert _API_KEY not in json.dumps(detail)


async def test_worker_truncated_output_flagged(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "w2@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()
    worker = _worker(session_factory, _mock_transport(finish_reason="length"))
    await worker.run_until_idle()
    detail = (await client.get(f"/api/v1/content/generations/{created['id']}")).json()
    assert detail["status"] == TASK_STATUS_SUCCEEDED
    assert detail["output_truncated"] is True
    assert detail["finish_reason"] == "length"


async def test_worker_auth_failure_terminal(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "w3@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()
    worker = _worker(session_factory, _mock_transport(status_code=401))
    await worker.run_until_idle()
    detail = (await client.get(f"/api/v1/content/generations/{created['id']}")).json()
    assert detail["status"] == TASK_STATUS_FAILED
    assert detail["error_code"] == "auth_failure"
    assert detail["output_text"] is None
    async with session_factory() as session:
        row = await _get_generation(session, uuid.UUID(created["id"]))
    assert row.attempt_count == 1


async def test_worker_empty_output_retries_then_recovers(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Empty output = parse-class retryable failure, not a success."""
    await _register(client, "w4@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()
    worker = _worker(
        session_factory,
        _mock_transport(
            responses=[{"content": "   "}, {"content": "Recovered output"}]
        ),
    )
    await worker.run_until_idle()
    async with session_factory() as session:
        row = await _get_generation(session, uuid.UUID(created["id"]))
    # First call failed retryable -> retry_wait with a future available_at.
    assert row.status == TASK_STATUS_RETRY_WAIT
    assert row.error_code == "parse_error"
    assert row.attempt_count == 1

    # Make it claimable now and run again: second call succeeds.
    async with session_factory() as session:
        row = await _get_generation(session, uuid.UUID(created["id"]))
        row.available_at = row.created_at
        await session.commit()
    await worker.run_until_idle()
    detail = (await client.get(f"/api/v1/content/generations/{created['id']}")).json()
    assert detail["status"] == TASK_STATUS_SUCCEEDED
    assert detail["output_text"] == "Recovered output"
    async with session_factory() as session:
        row = await _get_generation(session, uuid.UUID(created["id"]))
        attempts = (
            await session.scalars(
                select(ContentGenerationAttempt)
                .where(ContentGenerationAttempt.content_generation_id == row.id)
                .order_by(ContentGenerationAttempt.attempt_number)
            )
        ).all()
    assert row.attempt_count == 2
    assert [a.status for a in attempts] == ["failed", "succeeded"]


async def test_worker_retry_budget_exhausted(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "w5@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()
    worker = _worker(session_factory, _mock_transport(status_code=500))
    for _ in range(CONTENT_MAX_ATTEMPTS + 1):
        async with session_factory() as session:
            row = await _get_generation(session, uuid.UUID(created["id"]))
            if row.status == TASK_STATUS_RETRY_WAIT:
                row.available_at = row.created_at
                await session.commit()
        await worker.run_until_idle()
    detail = (await client.get(f"/api/v1/content/generations/{created['id']}")).json()
    assert detail["status"] == TASK_STATUS_FAILED
    assert detail["error_code"] == "max_attempts_exceeded"
    async with session_factory() as session:
        row = await _get_generation(session, uuid.UUID(created["id"]))
    assert row.attempt_count == CONTENT_MAX_ATTEMPTS


async def test_worker_cancelled_in_flight_records_attempt_discards_output(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A cancel landing while the HTTP call runs: the attempt is recorded
    for auditability but the output is discarded and the row stays
    cancelled."""
    await _register(client, "w6@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()
    gen_id = uuid.UUID(created["id"])

    async def cancelling_handler(request: httpx.Request) -> httpx.Response:
        # Simulate a cancel arriving mid-call (before the worker's terminal
        # write) by flipping the row inside the mocked provider call.
        async with session_factory() as session:
            row = await _get_generation(session, gen_id)
            row.status = TASK_STATUS_CANCELLED
            row.lease_owner = None
            row.lease_expires_at = None
            row.error_code = "cancelled"
            await session.commit()
        return httpx.Response(
            200,
            json={
                "model": "mistral-small-latest",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "SECRET"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 5},
            },
        )

    worker = _worker(session_factory, httpx.MockTransport(cancelling_handler))
    await worker.run_until_idle()

    detail = (await client.get(f"/api/v1/content/generations/{created['id']}")).json()
    assert detail["status"] == TASK_STATUS_CANCELLED
    assert detail["output_text"] is None
    async with session_factory() as session:
        row = await _get_generation(session, gen_id)
        attempts = (
            await session.scalars(
                select(ContentGenerationAttempt).where(
                    ContentGenerationAttempt.content_generation_id == gen_id
                )
            )
        ).all()
    assert row.attempt_count == 1
    assert len(attempts) == 1
    assert attempts[0].status == "succeeded"  # the real provider outcome


async def test_worker_lost_lease_writes_nothing(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "w7@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()
    gen_id = uuid.UUID(created["id"])

    async def lease_stealing_handler(request: httpx.Request) -> httpx.Response:
        async with session_factory() as session:
            row = await _get_generation(session, gen_id)
            row.lease_owner = "another-worker"
            await session.commit()
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "STOLEN"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    worker = _worker(session_factory, httpx.MockTransport(lease_stealing_handler))
    await worker.run_until_idle()

    async with session_factory() as session:
        row = await _get_generation(session, gen_id)
        attempts = (
            await session.scalars(
                select(ContentGenerationAttempt).where(
                    ContentGenerationAttempt.content_generation_id == gen_id
                )
            )
        ).all()
    assert row.output_text is None
    assert row.attempt_count == 0
    assert attempts == []


# --- Regenerate + try-again -----------------------------------------------


async def test_regenerate_creates_new_record(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "r1@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()
    worker = _worker(session_factory, _mock_transport())
    await worker.run_until_idle()

    regen = await client.post(f"/api/v1/content/generations/{created['id']}/regenerate")
    assert regen.status_code == 201
    body = regen.json()
    assert body["id"] != created["id"]
    assert body["status"] == TASK_STATUS_QUEUED
    assert body["prompt"] == created["prompt"]
    # The original is untouched (still succeeded with its output).
    original = (await client.get(f"/api/v1/content/generations/{created['id']}")).json()
    assert original["status"] == TASK_STATUS_SUCCEEDED
    assert original["output_text"] is not None


async def test_try_again_reuses_frozen_snapshot(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _register(client, "r2@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()
    # Fail it so try-again is the natural next step.
    worker = _worker(session_factory, _mock_transport(status_code=401))
    await worker.run_until_idle()

    retry = await client.post(f"/api/v1/content/generations/{created['id']}/try-again")
    assert retry.status_code == 201
    body = retry.json()
    assert body["id"] != created["id"]
    assert body["status"] == TASK_STATUS_QUEUED
    assert body["prompt"] == created["prompt"]
    # The frozen snapshot is byte-identical (reused, not rebuilt).
    async with session_factory() as session:
        source = await _get_generation(session, uuid.UUID(created["id"]))
        clone = await _get_generation(session, uuid.UUID(body["id"]))
    assert clone.website_context_snapshot == source.website_context_snapshot
    assert clone.website_context_status == source.website_context_status
    # And the original failed record was never mutated.
    assert source.status == TASK_STATUS_FAILED


async def test_regenerate_try_again_cross_workspace_404(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "r3@example.com")
    project_id = await _create_project(client)
    created = (await _enqueue(client, project_id)).json()
    client.cookies.clear()
    await _register(client, "r3-intruder@example.com")
    for action in ("regenerate", "try-again"):
        resp = await client.post(
            f"/api/v1/content/generations/{created['id']}/{action}"
        )
        assert resp.status_code == 404
