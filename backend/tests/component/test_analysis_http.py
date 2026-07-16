"""B6 analysis endpoints over HTTP (component, invariants 5/7).

Drives the real HTTP surface through the ASGI client, sharing the per-test
schema with the ORM/worker seeding so a fully-analyzed audit is reachable:

  - ``GET /audits/{id}/metrics`` serves the single-run snapshot;
  - ``GET /projects/{id}/visibility`` serves the dashboard projection;
  - ``GET /executions/{id}`` serves one execution's evidence;
  - ``GET /audits/{id}/export.{csv,md}`` download with the right media types;
  - all are auth-protected + workspace-scoped (a foreign workspace 404s).
"""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.connectors.answer_engines.contracts import (
    AnswerEngineRequest,
    AnswerEngineResponse,
    CitationResult,
    SearchEventResult,
)
from app.core.config.audits import audit_settings
from app.core.config.provider_catalog import ENGINE_GEMINI, TRANSPORT_GOOGLE
from app.domain.audits.planner import create_audit
from app.models.analysis import ResponseAnalysis
from app.models.workspace import WorkspaceMember
from app.workers import audit_worker
from app.workers.audit_worker import AuditWorker
from tests.component.audit_helpers import seed_audit_fixtures


class _StubAdapter:
    logical_engine = ENGINE_GEMINI
    transport_provider = TRANSPORT_GOOGLE

    def __init__(self, **_: object) -> None:
        pass

    async def execute(self, request: AnswerEngineRequest) -> AnswerEngineResponse:
        return AnswerEngineResponse(
            logical_engine=self.logical_engine,
            transport_provider=self.transport_provider,
            transport_model=request.model,
            answer_text="Acme Corp is a great option. Globex is an alternative.",
            search_used=True,
            search_events=(SearchEventResult(sequence=0, query=request.prompt),),
            citations=(
                CitationResult(
                    ordinal=0,
                    url="https://acme.com/",
                    title="Acme",
                    domain="acme.com",
                    start_index=0,
                    end_index=4,
                    cited_text="Acme",
                ),
            ),
            provider_metadata={"query_text_available": True},
            usage={"input_tokens": 10, "output_tokens": 20},
            latency_ms=5,
        )


@pytest.fixture
def _stub_adapter(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        audit_worker, "build_adapter", lambda **_: _StubAdapter()
    )
    monkeypatch.setattr(audit_settings, "min_request_interval_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "heartbeat_interval_seconds", 3600.0)


@pytest.mark.asyncio
async def test_endpoints_serve_projections_over_http(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    # Register a real user (hashed password) so login works, then attach them
    # to the seeded workspace as a member.
    email = "b6-real@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert reg.status_code == 201

    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=2)
        # Attach the registered user to the seeded workspace.
        from app.models.user import User

        user = await session.scalar(select(User).where(User.email == email))
        session.add(
            WorkspaceMember(
                workspace_id=seed.workspace_id, user_id=user.id, role="owner"
            )
        )
        await session.commit()
    async with session_factory() as session:
        audit = await create_audit(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            engines=seed.engines,
            prompt_set_id=seed.prompt_set_id,
            repetitions=2,
            random_seed="1",
        )
    worker = AuditWorker(session_factory=session_factory, owner="w-http")
    await worker.run_until_idle()

    headers = {"X-Workspace-Id": str(seed.workspace_id)}

    # Metrics projection.
    m = await client.get(f"/api/v1/audits/{audit.id}/metrics", headers=headers)
    assert m.status_code == 200
    assert m.json()["visibility_score"] == 100.0

    # Dashboard projection (defaults to latest completed audit).
    v = await client.get(
        f"/api/v1/projects/{seed.project_id}/visibility", headers=headers
    )
    assert v.status_code == 200
    body = v.json()
    assert body["audit_id"] == str(audit.id)
    assert body["sentiment"] is None
    assert any(r["is_brand"] for r in body["rankings"])

    # Execution evidence.
    async with session_factory() as session:
        analysis = await session.scalar(
            select(ResponseAnalysis).where(
                ResponseAnalysis.audit_id == audit.id
            )
        )
    e = await client.get(
        f"/api/v1/executions/{analysis.id}", headers=headers
    )
    assert e.status_code == 200
    assert e.json()["brand_mentioned"] is True

    # Exports with correct media types.
    csv_resp = await client.get(
        f"/api/v1/audits/{audit.id}/export.csv", headers=headers
    )
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_resp.headers["content-disposition"]

    md_resp = await client.get(
        f"/api/v1/audits/{audit.id}/export.md", headers=headers
    )
    assert md_resp.status_code == 200
    assert md_resp.headers["content-type"].startswith("text/markdown")
    assert "# AI Search Visibility Benchmark" in md_resp.text

    # Cross-workspace access is denied (invariant 5): a member of another
    # workspace cannot read this audit's metrics.
    import uuid

    bad = await client.get(
        f"/api/v1/audits/{audit.id}/metrics",
        headers={"X-Workspace-Id": str(uuid.uuid4())},
    )
    assert bad.status_code == 404
