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

import uuid as _uuid
from datetime import UTC, datetime

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
from app.core.config.audits import AUDIT_STATUS_COMPLETED, audit_settings
from app.core.config.provider_catalog import ENGINE_GEMINI, TRANSPORT_GOOGLE
from app.domain.audits.planner import create_audit
from app.models.analysis import MetricSnapshot
from app.models.audit import Audit
from app.models.user import User
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

    # Execution evidence. The executions list and the single-execution route
    # must share one id space: the id from GET /audits/{id}/executions must
    # resolve at GET /executions/{id} (regression: it used to 404 because the
    # single-execution route keyed on the internal analysis id).
    execs = await client.get(
        f"/api/v1/audits/{audit.id}/executions", headers=headers
    )
    assert execs.status_code == 200
    exec_rows = execs.json()
    assert exec_rows
    execution_id = exec_rows[0]["id"]
    e = await client.get(
        f"/api/v1/executions/{execution_id}", headers=headers
    )
    assert e.status_code == 200
    ebody = e.json()
    assert ebody["brand_mentioned"] is True
    # The returned id echoes the execution id the client passed in, and the
    # internal analysis id is surfaced separately for traceability.
    assert ebody["id"] == execution_id
    assert ebody["task_id"] == execution_id
    assert ebody["analysis_id"] != execution_id

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


# ---------------------------------------------------------------------------
# Cross-run Visibility trend endpoint over HTTP (roadmap: visibility-trends).
# ---------------------------------------------------------------------------
async def _register_and_attach(client, session_factory, *, prompt_count=1):
    """Register a real user, seed a workspace/project, attach them as owner."""
    email = f"trends-{_uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert reg.status_code == 201
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=prompt_count)
        user = await session.scalar(select(User).where(User.email == email))
        session.add(
            WorkspaceMember(
                workspace_id=seed.workspace_id, user_id=user.id, role="owner"
            )
        )
        await session.commit()
    return seed


def _http_metrics() -> dict:
    counts = {"Acme Corp": 4, "Globex": 2}
    return {
        "total_completed": 4,
        "brand_mention_rate": 1.0,
        "owned_citation_rate": 0.5,
        "competitor_mention_rate": {"Globex": 0.5},
        "competitor_citation_rate": {"Globex": 0.0},
        "share_of_voice": {
            "total_mentions": 6,
            "mention_counts": counts,
            "share": {"Acme Corp": round(4 / 6, 4), "Globex": round(2 / 6, 4)},
        },
        "sentiment": None,
        "avg_position": None,
    }


async def _seed_http_snapshot(session, *, workspace_id, project_id, completed_at):
    audit = Audit(
        workspace_id=workspace_id,
        project_id=project_id,
        status=AUDIT_STATUS_COMPLETED,
        completed_at=completed_at,
        requested_count=4,
        completed_count=4,
    )
    session.add(audit)
    await session.flush()
    session.add(
        MetricSnapshot(
            workspace_id=workspace_id,
            audit_id=audit.id,
            project_id=project_id,
            analyzer_version="b6-analysis-1",
            scoring_rule_version="scoring-v1",
            total_completed=4,
            total_failed=0,
            visibility_score=100.0,
            metrics=_http_metrics(),
            source_analysis_ids=[],
            source_artifact_ids=[],
        )
    )
    await session.flush()
    return audit


@pytest.mark.asyncio
async def test_trends_endpoint_serves_projection_over_http(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _register_and_attach(client, session_factory)
    async with session_factory() as session:
        await _seed_http_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
        await session.commit()

    headers = {"X-Workspace-Id": str(seed.workspace_id)}
    resp = await client.get(
        f"/api/v1/projects/{seed.project_id}/visibility/trends",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    point = body[0]
    assert point["audit_id"] is not None
    assert point["visibility_score"] == 100.0
    assert point["sentiment"] is None
    assert point["avg_position"] is None
    assert point["spans_version_boundary"] is False
    assert len(point["source_snapshot_ids"]) == 1
    assert point["analyzer_versions"] == ["b6-analysis-1"]
    assert any(r["is_brand"] for r in point["rankings"])


@pytest.mark.asyncio
async def test_trends_endpoint_query_parsing_and_422(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _register_and_attach(client, session_factory)
    async with session_factory() as session:
        await _seed_http_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
        await session.commit()
    headers = {"X-Workspace-Id": str(seed.workspace_id)}
    base = f"/api/v1/projects/{seed.project_id}/visibility/trends"

    # Aliased from/to + granularity parse and filter correctly.
    ok = await client.get(
        base,
        params={
            "from": "2026-01-01T00:00:00+00:00",
            "to": "2026-02-01T00:00:00+00:00",
            "granularity": "week",
            "engine": ENGINE_GEMINI,
        },
        headers=headers,
    )
    # Gemini slice absent on the seeded snapshot -> empty, still 200.
    assert ok.status_code == 200
    assert ok.json() == []

    # Invalid granularity -> 422.
    bad_gran = await client.get(
        base, params={"granularity": "daily"}, headers=headers
    )
    assert bad_gran.status_code == 422

    # Unknown engine -> 422.
    bad_engine = await client.get(
        base, params={"engine": "bing"}, headers=headers
    )
    assert bad_engine.status_code == 422

    # Reversed range -> 422.
    bad_range = await client.get(
        base,
        params={
            "from": "2026-03-01T00:00:00+00:00",
            "to": "2026-01-01T00:00:00+00:00",
        },
        headers=headers,
    )
    assert bad_range.status_code == 422

    # Naive timestamp -> 422.
    naive = await client.get(
        base, params={"from": "2026-03-01T00:00:00"}, headers=headers
    )
    assert naive.status_code == 422


@pytest.mark.asyncio
async def test_trends_endpoint_empty_for_valid_project(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _register_and_attach(client, session_factory)
    headers = {"X-Workspace-Id": str(seed.workspace_id)}
    resp = await client.get(
        f"/api/v1/projects/{seed.project_id}/visibility/trends",
        headers=headers,
    )
    # Valid project, no matching snapshots -> empty list, not 404.
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_trends_endpoint_foreign_workspace_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _register_and_attach(client, session_factory)
    async with session_factory() as session:
        await _seed_http_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
        await session.commit()
    # A member of another workspace cannot resolve this project (invariant 5).
    resp = await client.get(
        f"/api/v1/projects/{seed.project_id}/visibility/trends",
        headers={"X-Workspace-Id": str(_uuid.uuid4())},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Execution-evidence endpoint over HTTP (roadmap: visibility Mentions/Fanout).
# ---------------------------------------------------------------------------
async def _seed_http_evidence(
    session, *, workspace_id, project_id, completed_at, prompt_id=None
):
    """Seed one dashboard-ready execution with query text + a brand mention."""
    from app.models.analysis import BrandMention, ResponseAnalysis
    from app.models.audit import (
        AuditEngineSnapshot,
        AuditPromptSnapshot,
        AuditTask,
        RawResponseArtifact,
    )

    audit = Audit(
        workspace_id=workspace_id,
        project_id=project_id,
        status=AUDIT_STATUS_COMPLETED,
        completed_at=completed_at,
        requested_count=1,
        completed_count=1,
    )
    session.add(audit)
    await session.flush()
    snapshot = AuditPromptSnapshot(
        audit_id=audit.id,
        prompt_id=prompt_id,
        prompt_index=0,
        text="best crm software",
    )
    session.add(snapshot)
    engine_snapshot = AuditEngineSnapshot(
        audit_id=audit.id,
        logical_engine=ENGINE_GEMINI,
        transport_provider=TRANSPORT_GOOGLE,
        transport_model="gemini-flash-latest",
    )
    session.add(engine_snapshot)
    await session.flush()
    task = AuditTask(
        audit_id=audit.id,
        workspace_id=workspace_id,
        prompt_snapshot_id=snapshot.id,
        engine_snapshot_id=engine_snapshot.id,
        prompt_index=0,
        repetition=0,
        randomized_position=0,
        logical_engine=ENGINE_GEMINI,
        transport_provider=TRANSPORT_GOOGLE,
        transport_model="gemini-flash-latest",
        prompt_text="best crm software",
        idempotency_key=f"{audit.id}:0:0:{ENGINE_GEMINI}",
        answer_text="Acme Corp is great.",
        search_used=True,
        search_events=[],
    )
    session.add(task)
    await session.flush()
    artifact = RawResponseArtifact(
        audit_id=audit.id,
        task_id=task.id,
        logical_engine=ENGINE_GEMINI,
        transport_provider=TRANSPORT_GOOGLE,
        transport_model="gemini-flash-latest",
        answer_text="Acme Corp is great.",
        search_used=True,
        search_events=[
            {
                "sequence": 0,
                "query": "best crm software",
                "call_id": "c1",
                "call_sequence": 0,
                "query_sequence": 0,
            }
        ],
        citations=[],
    )
    session.add(artifact)
    await session.flush()
    task.result_artifact_id = artifact.id
    await session.flush()
    analysis = ResponseAnalysis(
        workspace_id=workspace_id,
        audit_id=audit.id,
        task_id=task.id,
        artifact_id=artifact.id,
        analyzer_version="b6-analysis-1",
        scoring_rule_version="scoring-v1",
        logical_engine=ENGINE_GEMINI,
        transport_provider=TRANSPORT_GOOGLE,
        transport_model="gemini-flash-latest",
        prompt_index=0,
        repetition=0,
        brand_mentioned=True,
        search_used=True,
        search_query_count=1,
    )
    session.add(analysis)
    await session.flush()
    session.add(
        BrandMention(
            workspace_id=workspace_id,
            audit_id=audit.id,
            analysis_id=analysis.id,
            artifact_id=artifact.id,
            analyzer_version="b6-analysis-1",
            brand_name="Acme Corp",
            first_offset=0,
        )
    )
    await session.flush()
    return audit


@pytest.mark.asyncio
async def test_evidence_endpoint_serves_projection_over_http(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _register_and_attach(client, session_factory)
    async with session_factory() as session:
        await _seed_http_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
        await session.commit()
    headers = {"X-Workspace-Id": str(seed.workspace_id)}
    resp = await client.get(
        f"/api/v1/projects/{seed.project_id}/visibility/evidence",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is False
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["state"] == "queries_available"
    assert item["event_source"] == "raw_artifact"
    assert item["search_events"][0]["query"] == "best crm software"
    assert item["mentions"][0]["kind"] == "brand"
    assert item["mentions"][0]["name"] == "Acme Corp"
    assert item["analysis_id"] is not None
    assert item["task_id"] is not None
    assert item["prompt_snapshot_id"] is not None


@pytest.mark.asyncio
async def test_evidence_endpoint_query_parsing_and_422(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _register_and_attach(client, session_factory)
    async with session_factory() as session:
        await _seed_http_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
        await session.commit()
    headers = {"X-Workspace-Id": str(seed.workspace_id)}
    base = f"/api/v1/projects/{seed.project_id}/visibility/evidence"

    # Aliased from/to + engine + limit parse and filter correctly.
    ok = await client.get(
        base,
        params={
            "from": "2026-01-01T00:00:00+00:00",
            "to": "2026-02-01T00:00:00+00:00",
            "engine": ENGINE_GEMINI,
            "limit": 50,
        },
        headers=headers,
    )
    assert ok.status_code == 200
    assert len(ok.json()["items"]) == 1

    # Unknown engine -> 422.
    bad_engine = await client.get(
        base, params={"engine": "bing"}, headers=headers
    )
    assert bad_engine.status_code == 422

    # Reversed range -> 422.
    bad_range = await client.get(
        base,
        params={
            "from": "2026-03-01T00:00:00+00:00",
            "to": "2026-01-01T00:00:00+00:00",
        },
        headers=headers,
    )
    assert bad_range.status_code == 422

    # Naive timestamp -> 422.
    naive = await client.get(
        base, params={"from": "2026-03-01T00:00:00"}, headers=headers
    )
    assert naive.status_code == 422

    # limit above the cap -> 422 (FastAPI query bound).
    over = await client.get(base, params={"limit": 501}, headers=headers)
    assert over.status_code == 422


@pytest.mark.asyncio
async def test_evidence_endpoint_empty_for_valid_project(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _register_and_attach(client, session_factory)
    headers = {"X-Workspace-Id": str(seed.workspace_id)}
    resp = await client.get(
        f"/api/v1/projects/{seed.project_id}/visibility/evidence",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "truncated": False}


@pytest.mark.asyncio
async def test_evidence_endpoint_foreign_workspace_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _register_and_attach(client, session_factory)
    async with session_factory() as session:
        await _seed_http_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
        await session.commit()
    # A member of another workspace cannot resolve this project (invariant 5).
    resp = await client.get(
        f"/api/v1/projects/{seed.project_id}/visibility/evidence",
        headers={"X-Workspace-Id": str(_uuid.uuid4())},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_evidence_endpoint_cross_workspace_audit_404(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A guessed audit id from another workspace 404s without a leak."""
    seed = await _register_and_attach(client, session_factory)
    async with session_factory() as session:
        other_seed = await seed_audit_fixtures(session, prompt_count=1)
        other_audit = await _seed_http_evidence(
            session,
            workspace_id=other_seed.workspace_id,
            project_id=other_seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
        await session.commit()
    headers = {"X-Workspace-Id": str(seed.workspace_id)}
    resp = await client.get(
        f"/api/v1/projects/{seed.project_id}/visibility/evidence",
        params={"audit_id": str(other_audit.id)},
        headers=headers,
    )
    assert resp.status_code == 404
