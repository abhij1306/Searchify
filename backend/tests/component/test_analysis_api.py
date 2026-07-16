"""B6 analysis projections + exports (component, invariants 4/5/7).

Seeds a workspace/project + audit through the ORM, runs the real worker (with a
MOCKED adapter — no network) so the analysis stage produces persisted rows +
one MetricSnapshot, then exercises the projection service + exports directly:

  - metrics + visibility + execution-evidence are PROJECTIONS: they read only
    persisted analysis and never call a provider (invariant 7 — asserted by
    patching ``build_adapter`` to raise before the projection calls);
  - derived rows carry provenance (``analyzer_version``) (invariant 4);
  - citation classification labels are persisted (owned/competitor/...);
  - CSV + Markdown exports render from persisted rows;
  - projections are workspace-scoped (a foreign workspace gets nothing).
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analysis.exports import audit_to_csv, audit_to_markdown
from app.connectors.answer_engines.contracts import (
    AnswerEngineRequest,
    AnswerEngineResponse,
    CitationResult,
    SearchEventResult,
)
from app.core.config.audits import (
    AUDIT_STATUS_COMPLETED,
    audit_settings,
)
from app.core.config.provider_catalog import ENGINE_GEMINI, TRANSPORT_GOOGLE
from app.domain.analysis.service import (
    AnalysisNotFoundError,
    get_execution_evidence,
    get_metrics,
    get_visibility,
    load_export_bundle,
)
from app.domain.audits.planner import create_audit
from app.models.analysis import (
    BrandMention,
    Citation,
    CompetitorMention,
    MetricSnapshot,
    ResponseAnalysis,
)
from app.workers import audit_worker
from app.workers.audit_worker import AuditWorker
from tests.component.audit_helpers import seed_audit_fixtures


class _StubAdapter:
    """In-memory answer-engine stand-in: mentions the brand + cites owned +
    competitor domains so the analysis has signal to aggregate (no network)."""

    logical_engine = ENGINE_GEMINI
    transport_provider = TRANSPORT_GOOGLE

    def __init__(self, **_: object) -> None:
        pass

    async def execute(self, request: AnswerEngineRequest) -> AnswerEngineResponse:
        return AnswerEngineResponse(
            logical_engine=self.logical_engine,
            transport_provider=self.transport_provider,
            transport_model=request.model,
            answer_text=(
                f"Acme Corp is a great option for {request.prompt}. "
                "Globex is an alternative."
            ),
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
                CitationResult(
                    ordinal=1,
                    url="https://globex.com/",
                    title="Globex",
                    domain="globex.com",
                    start_index=0,
                    end_index=6,
                    cited_text="Globex",
                ),
            ),
            provider_metadata={"query_text_available": True},
            usage={"input_tokens": 10, "output_tokens": 20},
            latency_ms=5,
        )


@pytest.fixture
def _stub_adapter(monkeypatch: pytest.MonkeyPatch):
    def _build(**_: object) -> _StubAdapter:
        return _StubAdapter()

    monkeypatch.setattr(audit_worker, "build_adapter", _build)
    monkeypatch.setattr(audit_settings, "min_request_interval_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "heartbeat_interval_seconds", 3600.0)


async def _run_completed_audit(
    session_factory: async_sessionmaker[AsyncSession],
):
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=2)
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
    worker = AuditWorker(session_factory=session_factory, owner="w-b6")
    await worker.run_until_idle()
    return seed, audit


@pytest.mark.asyncio
async def test_metrics_and_visibility_are_projections(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed, audit = await _run_completed_audit(session_factory)

    # After finalize the projections must never touch a provider: make the
    # adapter factory explode so any provider call during a projection fails.
    def _boom(**_: object):
        raise AssertionError("projection must not call a provider (invariant 7)")

    monkeypatch.setattr(audit_worker, "build_adapter", _boom)

    async with session_factory() as session:
        # Audit reached COMPLETED with a populated snapshot.
        refreshed = await session.get(type(audit), audit.id)
        assert refreshed.status == AUDIT_STATUS_COMPLETED

        metrics = await get_metrics(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert metrics.total_completed == 4
        assert metrics.visibility_score == 100.0  # brand mentioned every time
        assert metrics.analyzer_version
        assert "share_of_voice" in metrics.metrics
        assert metrics.metrics["sentiment"] is None
        assert metrics.metrics["avg_position"] is None

        vis = await get_visibility(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
        assert vis.audit_id == audit.id
        assert vis.visibility_score == 100.0
        # Brand-vs-competitor rankings populated; brand row present.
        brand_rows = [r for r in vis.rankings if r.is_brand]
        assert len(brand_rows) == 1
        assert brand_rows[0].mention_rate == 1.0
        # Per-engine comparison for the single engine.
        assert len(vis.per_engine) == 1
        assert vis.per_engine[0].logical_engine == ENGINE_GEMINI
        # Roadmap fields present but null (decision B-2).
        assert vis.sentiment is None
        assert vis.avg_position is None


@pytest.mark.asyncio
async def test_provenance_and_citation_classification_persisted(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, audit = await _run_completed_audit(session_factory)

    async with session_factory() as session:
        analyses = list(
            (
                await session.scalars(
                    select(ResponseAnalysis).where(
                        ResponseAnalysis.audit_id == audit.id
                    )
                )
            ).all()
        )
        assert len(analyses) == 4
        # Every derived row references its artifact + analyzer version (inv. 4).
        for analysis in analyses:
            assert analysis.analyzer_version
            assert analysis.scoring_rule_version
            assert analysis.artifact_id is not None

        # Brand + competitor mentions recorded with provenance.
        brand_count = await session.scalar(
            select(func.count())
            .select_from(BrandMention)
            .where(BrandMention.audit_id == audit.id)
        )
        comp_count = await session.scalar(
            select(func.count())
            .select_from(CompetitorMention)
            .where(CompetitorMention.audit_id == audit.id)
        )
        assert brand_count == 4  # brand mentioned in all 4
        assert comp_count == 4  # Globex mentioned in all 4

        # Citation classification: owned (acme.com) + competitor (globex.com).
        citations = list(
            (
                await session.scalars(
                    select(Citation).where(Citation.audit_id == audit.id)
                )
            ).all()
        )
        assert all(c.analyzer_version for c in citations)
        owned = [c for c in citations if c.classification == "owned"]
        competitor = [c for c in citations if c.classification == "competitor"]
        assert owned and all(c.is_owned for c in owned)
        assert competitor and all(
            c.matched_competitor == "Globex" for c in competitor
        )


@pytest.mark.asyncio
async def test_execution_evidence_projection(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, audit = await _run_completed_audit(session_factory)

    async with session_factory() as session:
        analysis = await session.scalar(
            select(ResponseAnalysis).where(
                ResponseAnalysis.audit_id == audit.id
            )
        )
        # Keyed on the execution (AuditTask) id, matching the id clients get
        # from GET /audits/{id}/executions — not the internal analysis id.
        evidence = await get_execution_evidence(
            session,
            workspace_id=seed.workspace_id,
            task_id=analysis.task_id,
        )
        assert evidence.brand_mentioned is True
        assert evidence.citation_count == 2
        assert len(evidence.citations) == 2
        assert "Globex" in evidence.competitors_mentioned
        # id/task_id are the execution id; analysis_id is the internal id.
        assert evidence.id == analysis.task_id
        assert evidence.task_id == analysis.task_id
        assert evidence.analysis_id == analysis.id
        # Roadmap fields present but null.
        assert evidence.sentiment is None
        assert evidence.avg_position is None

        # A foreign workspace cannot read the evidence (invariant 5).
        import uuid

        with pytest.raises(AnalysisNotFoundError):
            await get_execution_evidence(
                session,
                workspace_id=uuid.uuid4(),
                task_id=analysis.task_id,
            )


@pytest.mark.asyncio
async def test_exports_render_from_persisted_rows(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, audit = await _run_completed_audit(session_factory)

    async with session_factory() as session:
        loaded_audit, tasks = await load_export_bundle(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert len(tasks) == 4

        csv_body = audit_to_csv(loaded_audit, tasks)
        assert "audit_id,prompt_index" in csv_body.splitlines()[0]
        # One header + one row per execution.
        assert len(csv_body.strip().splitlines()) == 1 + 4

        md_body = audit_to_markdown(loaded_audit, tasks)
        assert "# AI Search Visibility Benchmark" in md_body
        assert "## Headline Metrics" in md_body
        assert "## Methodology" in md_body


@pytest.mark.asyncio
async def test_metrics_not_found_for_unanalyzed_audit(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    # Seed + create but DON'T run the worker -> no MetricSnapshot yet.
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
    async with session_factory() as session:
        audit = await create_audit(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            engines=seed.engines,
            prompt_set_id=seed.prompt_set_id,
            repetitions=1,
            random_seed="1",
        )
    async with session_factory() as session:
        with pytest.raises(AnalysisNotFoundError):
            await get_metrics(
                session, workspace_id=seed.workspace_id, audit_id=audit.id
            )
        # No completed audit -> visibility 404s too.
        snapshot = await session.scalar(
            select(MetricSnapshot).where(MetricSnapshot.audit_id == audit.id)
        )
        assert snapshot is None
        with pytest.raises(AnalysisNotFoundError):
            await get_visibility(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
            )


@pytest.mark.asyncio
async def test_snapshot_records_source_provenance(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    """The MetricSnapshot traces back to the exact evidence set (invariant 4).

    ``source_analysis_ids`` must equal the succeeded tasks' analysis ids and
    ``source_artifact_ids`` their raw response artifacts.
    """
    seed, audit = await _run_completed_audit(session_factory)

    async with session_factory() as session:
        analyses = list(
            (
                await session.scalars(
                    select(ResponseAnalysis).where(
                        ResponseAnalysis.audit_id == audit.id
                    )
                )
            ).all()
        )
        snapshot = await session.scalar(
            select(MetricSnapshot).where(MetricSnapshot.audit_id == audit.id)
        )
        assert snapshot is not None
        expected_analysis_ids = {str(a.id) for a in analyses}
        expected_artifact_ids = {
            str(a.artifact_id) for a in analyses if a.artifact_id is not None
        }
        assert set(snapshot.source_analysis_ids) == expected_analysis_ids
        assert set(snapshot.source_artifact_ids) == expected_artifact_ids
        # Every succeeded analysis has an artifact in this fixture.
        assert len(snapshot.source_artifact_ids) == len(analyses)


class _UsageStubAdapter(_StubAdapter):
    """Like the base stub but reports a per-request token/cost usage block
    nested under ``provider_metadata`` (as the real parsers do), so cost/token
    aggregation has non-zero data to sum."""

    async def execute(self, request: AnswerEngineRequest) -> AnswerEngineResponse:
        response = await super().execute(request)
        usage = {
            "total_input_tokens": 100,
            "total_output_tokens": 50,
            "total_tokens": 150,
            "provider_cost_usd": 0.25,
        }
        return AnswerEngineResponse(
            logical_engine=response.logical_engine,
            transport_provider=response.transport_provider,
            transport_model=response.transport_model,
            answer_text=response.answer_text,
            search_used=response.search_used,
            search_events=response.search_events,
            citations=response.citations,
            provider_metadata={
                **dict(response.provider_metadata),
                "usage": usage,
            },
            usage=usage,
            latency_ms=response.latency_ms,
        )


@pytest.mark.asyncio
async def test_aggregation_preserves_provider_usage(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persisted provider usage flows into the aggregate (not dropped as zero).

    Regression: the aggregate input was rebuilt with an empty
    ``provider_metadata``, so token/cost metrics were always zero.
    """
    monkeypatch.setattr(
        audit_worker, "build_adapter", lambda **_: _UsageStubAdapter()
    )
    monkeypatch.setattr(audit_settings, "min_request_interval_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "heartbeat_interval_seconds", 3600.0)

    seed, audit = await _run_completed_audit(session_factory)

    async with session_factory() as session:
        metrics = await get_metrics(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        token_usage = metrics.metrics["token_usage"]
        # 4 executions * 100/50 tokens each.
        assert token_usage["input_tokens"] == 400
        assert token_usage["output_tokens"] == 200
        assert token_usage["total_tokens"] == 600

        cost = metrics.metrics["cost"]
        # 4 executions * $0.25 provider-reported each — previously always zero
        # because provider_metadata was dropped when rebuilding the aggregate.
        assert cost["provider_reported_cost_usd"] == 1.0
