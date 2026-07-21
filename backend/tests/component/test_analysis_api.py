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

import uuid as _uuid
from datetime import UTC, datetime

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
    AUDIT_STATUS_PARTIALLY_COMPLETED,
    audit_settings,
)
from app.core.config.provider_catalog import (
    ENGINE_CHATGPT,
    ENGINE_GEMINI,
    TRANSPORT_GOOGLE,
)
from app.domain.analysis import service as analysis_service
from app.domain.analysis.schemas import VisibilityFanoutState
from app.domain.analysis.service import (
    AnalysisNotFoundError,
    TrendQueryError,
    get_execution_evidence,
    get_metrics,
    get_visibility,
    get_visibility_evidence,
    get_visibility_trends,
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
from app.models.audit import (
    Audit,
    AuditEngineSnapshot,
    AuditPromptSnapshot,
    AuditTask,
    RawResponseArtifact,
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
        # No-op: stub holds no state; accepts and ignores adapter build kwargs.
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
        assert refreshed is not None
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
    _seed, audit = await _run_completed_audit(session_factory)

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
        assert competitor and all(c.matched_competitor == "Globex" for c in competitor)


@pytest.mark.asyncio
async def test_execution_evidence_projection(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    seed, audit = await _run_completed_audit(session_factory)

    async with session_factory() as session:
        analysis = await session.scalar(
            select(ResponseAnalysis).where(ResponseAnalysis.audit_id == audit.id)
        )
        assert analysis is not None
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
    _seed, audit = await _run_completed_audit(session_factory)

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
        assert snapshot.source_analysis_ids is not None
        assert snapshot.source_artifact_ids is not None
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
    monkeypatch.setattr(audit_worker, "build_adapter", lambda **_: _UsageStubAdapter())
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
        assert cost["provider_reported_cost_usd"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Cross-run Visibility trend projection (roadmap: visibility-trends).
#
# These seed dashboard-ready ``Audit`` + ``MetricSnapshot`` rows DIRECTLY through
# the ORM (no worker run) so completion timestamps, statuses, engine slices, and
# analyzer/scoring versions are deterministic. Every assertion exercises the pure
# projection ``get_visibility_trends`` — never a provider (invariant 7).
# ---------------------------------------------------------------------------
_BRAND = "Acme Corp"
_COMPETITOR = "Globex"


def _trend_metrics(
    *,
    brand_rate: float,
    owned_rate: float,
    competitor_rate: float,
    brand_count: int,
    competitor_count: int,
    total_completed: int,
    per_engine: dict | None = None,
) -> dict:
    counts = {_BRAND: brand_count, _COMPETITOR: competitor_count}
    total_mentions = sum(counts.values())
    share = {
        name: round(c / total_mentions, 4) if total_mentions else 0.0
        for name, c in counts.items()
    }
    metrics = {
        "total_completed": total_completed,
        "brand_mention_rate": brand_rate,
        "owned_citation_rate": owned_rate,
        "competitor_mention_rate": {_COMPETITOR: competitor_rate},
        "competitor_citation_rate": {_COMPETITOR: 0.0},
        "share_of_voice": {
            "total_mentions": total_mentions,
            "mention_counts": counts,
            "share": share,
        },
        "sentiment": None,
        "avg_position": None,
    }
    if per_engine is not None:
        metrics["per_engine"] = per_engine
    return metrics


async def _seed_snapshot(
    session,
    *,
    workspace_id,
    project_id,
    completed_at: datetime,
    metrics: dict,
    visibility_score: float,
    total_completed: int,
    analyzer_version: str = "b6-analysis-1",
    scoring_rule_version: str = "scoring-v1",
    status: str = AUDIT_STATUS_COMPLETED,
):
    audit = Audit(
        workspace_id=workspace_id,
        project_id=project_id,
        status=status,
        completed_at=completed_at,
        requested_count=total_completed,
        completed_count=total_completed,
    )
    session.add(audit)
    await session.flush()
    snapshot = MetricSnapshot(
        workspace_id=workspace_id,
        audit_id=audit.id,
        project_id=project_id,
        analyzer_version=analyzer_version,
        scoring_rule_version=scoring_rule_version,
        total_completed=total_completed,
        total_failed=0,
        visibility_score=visibility_score,
        metrics=metrics,
        source_analysis_ids=[],
        source_artifact_ids=[],
    )
    session.add(snapshot)
    await session.flush()
    return audit, snapshot


@pytest.mark.asyncio
async def test_trends_raw_points_chronological_with_provenance(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The projection must never touch a provider (invariant 7).
    def _boom(**_: object):
        raise AssertionError("trend projection must not call a provider")

    monkeypatch.setattr(audit_worker, "build_adapter", _boom)

    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        # Seed out of order to prove the endpoint sorts chronologically.
        _, snap_late = await _seed_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 3, 10, tzinfo=UTC),
            metrics=_trend_metrics(
                brand_rate=0.5,
                owned_rate=0.25,
                competitor_rate=1.0,
                brand_count=2,
                competitor_count=4,
                total_completed=4,
            ),
            visibility_score=50.0,
            total_completed=4,
        )
        # A partially-completed audit must still be included (eligibility rule).
        _, snap_early = await _seed_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
            metrics=_trend_metrics(
                brand_rate=1.0,
                owned_rate=0.5,
                competitor_rate=0.5,
                brand_count=4,
                competitor_count=2,
                total_completed=4,
            ),
            visibility_score=100.0,
            total_completed=4,
            status=AUDIT_STATUS_PARTIALLY_COMPLETED,
        )
        await session.commit()

        points = await get_visibility_trends(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
    assert len(points) == 2
    # Chronological order (earliest first) despite insertion order.
    assert points[0].completed_at == datetime(2026, 1, 5, tzinfo=UTC)
    assert points[1].completed_at == datetime(2026, 3, 10, tzinfo=UTC)
    # Raw points carry the single snapshot as provenance + its versions.
    assert points[0].audit_id is not None
    assert points[0].source_snapshot_ids == [snap_early.id]
    assert points[0].analyzer_versions == ["b6-analysis-1"]
    assert points[0].scoring_rule_versions == ["scoring-v1"]
    assert points[0].spans_version_boundary is False
    assert points[0].logical_engine is None
    # Roadmap fields stay null (decision B-2 / invariant 9).
    assert points[0].sentiment is None
    assert points[0].avg_position is None
    assert all(
        r.sentiment is None and r.avg_position is None for r in points[0].rankings
    )
    # Headline values project the persisted snapshot exactly.
    assert points[0].visibility_score == 100.0
    assert points[0].brand_mention_rate == 1.0
    assert points[0].owned_citation_rate == 0.5
    _ = snap_late


@pytest.mark.asyncio
async def test_trends_response_and_mention_sov_and_rankings(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        await _seed_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
            metrics=_trend_metrics(
                brand_rate=1.0,
                owned_rate=0.5,
                competitor_rate=0.5,
                brand_count=4,
                competitor_count=2,
                total_completed=4,
            ),
            visibility_score=100.0,
            total_completed=4,
        )
        await session.commit()
        points = await get_visibility_trends(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
    point = points[0]
    # Response-level SOV = brand_rate / (brand_rate + competitor_rate) = 1/1.5.
    assert point.sov.response == pytest.approx(round(1.0 / 1.5, 4))
    # Mention-level SOV = brand_count / total_mentions = 4/6.
    assert point.sov.mention == pytest.approx(round(4 / 6, 4))
    # Rankings: brand row first (highest SOV), competitor present.
    brand_rows = [r for r in point.rankings if r.is_brand]
    assert len(brand_rows) == 1
    assert brand_rows[0].name == _BRAND
    assert brand_rows[0].mention_count == 4
    competitor_rows = [r for r in point.rankings if not r.is_brand]
    assert competitor_rows[0].name == _COMPETITOR
    assert competitor_rows[0].mention_count == 2


@pytest.mark.asyncio
async def test_trends_date_and_engine_filtering(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    per_engine_gemini = {
        ENGINE_GEMINI: _trend_metrics(
            brand_rate=1.0,
            owned_rate=0.5,
            competitor_rate=0.5,
            brand_count=4,
            competitor_count=2,
            total_completed=4,
        )
    }
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        # In-window audit that measured gemini only.
        await _seed_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 10, tzinfo=UTC),
            metrics=_trend_metrics(
                brand_rate=0.75,
                owned_rate=0.5,
                competitor_rate=0.5,
                brand_count=3,
                competitor_count=2,
                total_completed=4,
                per_engine=per_engine_gemini,
            ),
            visibility_score=75.0,
            total_completed=4,
        )
        # Out-of-window audit (before the from bound) — must be excluded.
        await _seed_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2025, 12, 1, tzinfo=UTC),
            metrics=_trend_metrics(
                brand_rate=0.5,
                owned_rate=0.25,
                competitor_rate=1.0,
                brand_count=2,
                competitor_count=4,
                total_completed=4,
                per_engine=per_engine_gemini,
            ),
            visibility_score=50.0,
            total_completed=4,
        )
        await session.commit()

        # Date filter: only the Feb audit is in range.
        windowed = await get_visibility_trends(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            from_at=datetime(2026, 1, 1, tzinfo=UTC),
            to_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        assert len(windowed) == 1
        assert windowed[0].completed_at == datetime(2026, 2, 10, tzinfo=UTC)

        # Engine filter: gemini slice is present on both; chatgpt slice missing
        # on every snapshot -> the engine-filtered series is empty.
        gemini = await get_visibility_trends(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            logical_engine=ENGINE_GEMINI,
        )
        assert len(gemini) == 2
        assert all(p.logical_engine == ENGINE_GEMINI for p in gemini)
        chatgpt = await get_visibility_trends(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            logical_engine=ENGINE_CHATGPT,
        )
        assert chatgpt == []


@pytest.mark.asyncio
async def test_trends_weekly_and_monthly_bucketing_math(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        # Two snapshots in the SAME ISO week (Mon 2026-01-05 .. Sun 2026-01-11).
        await _seed_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, 9, tzinfo=UTC),  # Monday
            metrics=_trend_metrics(
                brand_rate=1.0,
                owned_rate=0.5,
                competitor_rate=0.5,
                brand_count=4,
                competitor_count=2,
                total_completed=4,
            ),
            visibility_score=100.0,
            total_completed=4,
        )
        await _seed_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 7, 9, tzinfo=UTC),  # Wednesday
            metrics=_trend_metrics(
                brand_rate=0.5,
                owned_rate=0.0,
                competitor_rate=1.0,
                brand_count=1,
                competitor_count=1,
                total_completed=2,
            ),
            visibility_score=50.0,
            total_completed=2,
        )
        await session.commit()

        weekly = await get_visibility_trends(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            granularity="week",
        )
        monthly = await get_visibility_trends(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            granularity="month",
        )

    assert len(weekly) == 1
    bucket = weekly[0]
    # UTC week boundary is the Monday 00:00.
    assert bucket.completed_at == datetime(2026, 1, 5, tzinfo=UTC)
    assert bucket.audit_id is None
    assert len(bucket.source_snapshot_ids) == 2
    # Completion-weighted brand rate: (1.0*4 + 0.5*2) / 6 = 5/6.
    assert bucket.brand_mention_rate == pytest.approx(round(5 / 6, 4))
    # Owned-citation rate: (0.5*4 + 0.0*2) / 6 = 2/6.
    assert bucket.owned_citation_rate == pytest.approx(round(2 / 6, 4))
    # Mention counts SUM before division: Acme 4+1=5, Globex 2+1=3, total 8.
    brand_row = next(r for r in bucket.rankings if r.is_brand)
    assert brand_row.mention_count == 5
    assert brand_row.share_of_voice == pytest.approx(round(5 / 8, 4))
    assert bucket.sov.mention == pytest.approx(round(5 / 8, 4))

    assert len(monthly) == 1
    assert monthly[0].completed_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert len(monthly[0].source_snapshot_ids) == 2


@pytest.mark.asyncio
async def test_trends_mixed_version_strict_fallback_and_non_strict_marking(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        # Two same-week snapshots produced under DIFFERENT analyzer versions.
        await _seed_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
            metrics=_trend_metrics(
                brand_rate=1.0,
                owned_rate=0.5,
                competitor_rate=0.5,
                brand_count=4,
                competitor_count=2,
                total_completed=4,
            ),
            visibility_score=100.0,
            total_completed=4,
            analyzer_version="b6-analysis-1",
        )
        await _seed_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 7, tzinfo=UTC),
            metrics=_trend_metrics(
                brand_rate=0.5,
                owned_rate=0.0,
                competitor_rate=1.0,
                brand_count=1,
                competitor_count=1,
                total_completed=2,
            ),
            visibility_score=50.0,
            total_completed=2,
            analyzer_version="b6-analysis-2",
        )
        await session.commit()

        # Strict (default): a version-crossing bucket makes the whole range
        # fall back to raw per-run points.
        strict = await get_visibility_trends(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            granularity="week",
        )
        assert len(strict) == 2
        assert all(p.audit_id is not None for p in strict)
        assert all(len(p.source_snapshot_ids) == 1 for p in strict)

        # Non-strict: the mixed bucket is emitted + flagged with both versions.
        monkeypatch.setattr(
            analysis_service, "VISIBILITY_TRENDS_STRICT_VERSION_BUCKETS", False
        )
        marked = await get_visibility_trends(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            granularity="week",
        )
    assert len(marked) == 1
    assert marked[0].spans_version_boundary is True
    assert marked[0].analyzer_versions == ["b6-analysis-1", "b6-analysis-2"]
    assert len(marked[0].source_snapshot_ids) == 2


@pytest.mark.asyncio
async def test_trends_single_point_and_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        # No snapshots yet -> empty (not an error).
        empty = await get_visibility_trends(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
        assert empty == []

        await _seed_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
            metrics=_trend_metrics(
                brand_rate=1.0,
                owned_rate=0.5,
                competitor_rate=0.5,
                brand_count=4,
                competitor_count=2,
                total_completed=4,
            ),
            visibility_score=100.0,
            total_completed=4,
        )
        await session.commit()
        one = await get_visibility_trends(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
        assert len(one) == 1


@pytest.mark.asyncio
async def test_trends_workspace_isolation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        await _seed_snapshot(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 5, tzinfo=UTC),
            metrics=_trend_metrics(
                brand_rate=1.0,
                owned_rate=0.5,
                competitor_rate=0.5,
                brand_count=4,
                competitor_count=2,
                total_completed=4,
            ),
            visibility_score=100.0,
            total_completed=4,
        )
        await session.commit()
        # A foreign workspace sees nothing (invariant 5).
        foreign = await get_visibility_trends(
            session,
            workspace_id=_uuid.uuid4(),
            project_id=seed.project_id,
        )
        assert foreign == []


@pytest.mark.asyncio
async def test_trends_invalid_query_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        await session.commit()
        with pytest.raises(TrendQueryError):
            await get_visibility_trends(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                granularity="daily",
            )
        with pytest.raises(TrendQueryError):
            await get_visibility_trends(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                logical_engine="bing",
            )
        with pytest.raises(TrendQueryError):
            await get_visibility_trends(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                from_at=datetime(2026, 3, 1, tzinfo=UTC),
                to_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        with pytest.raises(TrendQueryError):
            await get_visibility_trends(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                from_at=datetime(2026, 3, 1),  # naive
            )


# ---------------------------------------------------------------------------
# Execution-evidence projection (roadmap: visibility Mentions & Query Fanout).
#
# These seed dashboard-ready Audit + AuditPromptSnapshot + AuditEngineSnapshot +
# AuditTask + RawResponseArtifact + ResponseAnalysis + mention/citation child
# rows DIRECTLY through the ORM so search-event source, provenance ids, and
# fanout state are deterministic. Every assertion exercises the pure projection
# ``get_visibility_evidence`` — never a provider (invariant 7).
# ---------------------------------------------------------------------------


async def _seed_evidence_execution(
    session,
    *,
    workspace_id,
    project_id,
    completed_at: datetime,
    prompt_index: int = 0,
    repetition: int = 0,
    logical_engine: str = ENGINE_GEMINI,
    transport_provider: str = TRANSPORT_GOOGLE,
    transport_model: str = "gemini-flash-latest",
    prompt_id=None,
    prompt_text: str = "best crm software",
    search_used: bool = True,
    search_query_count: int = 1,
    artifact_events=None,
    task_events=None,
    write_artifact: bool = True,
    brand_mentions=None,
    competitor_mentions=None,
    citations=None,
    audit=None,
    status: str = AUDIT_STATUS_COMPLETED,
    analyzer_version: str = "b6-analysis-1",
):
    """Seed one dashboard-ready execution with full evidence child rows."""
    if audit is None:
        audit = Audit(
            workspace_id=workspace_id,
            project_id=project_id,
            status=status,
            completed_at=completed_at,
            requested_count=1,
            completed_count=1,
        )
        session.add(audit)
        await session.flush()

    # Reuse an existing prompt snapshot for this audit+prompt_index (the unique
    # (audit_id, prompt_index) slot) so multiple repetitions can share one.
    snapshot = await session.scalar(
        select(AuditPromptSnapshot).where(
            AuditPromptSnapshot.audit_id == audit.id,
            AuditPromptSnapshot.prompt_index == prompt_index,
        )
    )
    if snapshot is None:
        snapshot = AuditPromptSnapshot(
            audit_id=audit.id,
            prompt_id=prompt_id,
            prompt_index=prompt_index,
            text=prompt_text,
            theme="general",
            intent="category",
        )
        session.add(snapshot)
        await session.flush()
    # Reuse an existing engine snapshot for this audit+engine (the unique
    # (audit_id, logical_engine) slot) so multiple executions can share one.
    engine_snapshot = await session.scalar(
        select(AuditEngineSnapshot).where(
            AuditEngineSnapshot.audit_id == audit.id,
            AuditEngineSnapshot.logical_engine == logical_engine,
        )
    )
    if engine_snapshot is None:
        engine_snapshot = AuditEngineSnapshot(
            audit_id=audit.id,
            logical_engine=logical_engine,
            transport_provider=transport_provider,
            transport_model=transport_model,
        )
        session.add(engine_snapshot)
        await session.flush()

    task = AuditTask(
        audit_id=audit.id,
        workspace_id=workspace_id,
        prompt_snapshot_id=snapshot.id,
        engine_snapshot_id=engine_snapshot.id,
        prompt_index=prompt_index,
        repetition=repetition,
        randomized_position=0,
        logical_engine=logical_engine,
        transport_provider=transport_provider,
        transport_model=transport_model,
        prompt_text=prompt_text,
        idempotency_key=f"{audit.id}:{prompt_index}:{repetition}:{logical_engine}",
        answer_text="Acme Corp is great. Globex is an alternative.",
        search_used=search_used,
        search_events=task_events if task_events is not None else [],
    )
    session.add(task)
    await session.flush()

    artifact_id = None
    if write_artifact:
        artifact = RawResponseArtifact(
            audit_id=audit.id,
            task_id=task.id,
            logical_engine=logical_engine,
            transport_provider=transport_provider,
            transport_model=transport_model,
            answer_text="Acme Corp is great. Globex is an alternative.",
            search_used=search_used,
            search_events=artifact_events if artifact_events is not None else [],
            citations=[],
        )
        session.add(artifact)
        await session.flush()
        artifact_id = artifact.id
        task.result_artifact_id = artifact_id
        await session.flush()

    analysis = ResponseAnalysis(
        workspace_id=workspace_id,
        audit_id=audit.id,
        task_id=task.id,
        artifact_id=artifact_id,
        analyzer_version=analyzer_version,
        scoring_rule_version="scoring-v1",
        logical_engine=logical_engine,
        transport_provider=transport_provider,
        transport_model=transport_model,
        prompt_index=prompt_index,
        repetition=repetition,
        brand_mentioned=bool(brand_mentions),
        search_used=search_used,
        search_query_count=search_query_count,
    )
    session.add(analysis)
    await session.flush()

    for name, offset in brand_mentions or []:
        session.add(
            BrandMention(
                workspace_id=workspace_id,
                audit_id=audit.id,
                analysis_id=analysis.id,
                artifact_id=artifact_id,
                analyzer_version=analyzer_version,
                brand_name=name,
                first_offset=offset,
            )
        )
    for name in competitor_mentions or []:
        session.add(
            CompetitorMention(
                workspace_id=workspace_id,
                audit_id=audit.id,
                analysis_id=analysis.id,
                artifact_id=artifact_id,
                analyzer_version=analyzer_version,
                competitor_name=name,
            )
        )
    for ordinal, (url, domain, classification) in enumerate(citations or []):
        session.add(
            Citation(
                workspace_id=workspace_id,
                audit_id=audit.id,
                analysis_id=analysis.id,
                artifact_id=artifact_id,
                analyzer_version=analyzer_version,
                ordinal=ordinal,
                url=url,
                title=domain,
                domain=domain,
                classification=classification,
                is_owned=classification == "owned",
                matched_competitor="Globex" if classification == "competitor" else None,
            )
        )
    await session.flush()
    return audit, snapshot, task, analysis


def _event(sequence, query, call_id="", call_sequence=0, query_sequence=0):
    return {
        "sequence": sequence,
        "query": query,
        "call_id": call_id,
        "call_sequence": call_sequence,
        "query_sequence": query_sequence,
    }


@pytest.mark.asyncio
async def test_evidence_projects_mentions_citations_and_queries(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persisted mentions/citations + artifact query text are projected as-is."""

    def _boom(**_: object):
        raise AssertionError("evidence projection must not call a provider")

    monkeypatch.setattr(audit_worker, "build_adapter", _boom)

    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            artifact_events=[
                _event(0, "best crm software", call_id="c1"),
                _event(1, "crm pricing", call_id="c1", query_sequence=1),
            ],
            search_query_count=2,
            brand_mentions=[("Acme Corp", 0)],
            competitor_mentions=["Globex"],
            citations=[
                ("https://acme.com/", "acme.com", "owned"),
                ("https://globex.com/", "globex.com", "competitor"),
            ],
        )
        await session.commit()

        result = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
    assert result.truncated is False
    assert len(result.items) == 1
    item = result.items[0]
    # Query fanout: real query text from the artifact -> queries_available.
    assert item.state == VisibilityFanoutState.QUERIES_AVAILABLE
    assert item.query_text_available is True
    assert item.event_source == "raw_artifact"
    assert [e.query for e in item.search_events] == [
        "best crm software",
        "crm pricing",
    ]
    assert item.search_query_count == 2
    # Persisted mentions projected (never inferred).
    brand = [m for m in item.mentions if m.kind == "brand"]
    competitor = [m for m in item.mentions if m.kind == "competitor"]
    assert brand[0].name == "Acme Corp"
    assert brand[0].first_offset == 0
    assert brand[0].analyzer_version == "b6-analysis-1"
    assert competitor[0].name == "Globex"
    # Classified citations projected.
    classifications = {c.classification for c in item.citations}
    assert classifications == {"owned", "competitor"}
    # Provenance ids present.
    assert item.analysis_id is not None
    assert item.task_id is not None
    assert item.artifact_id is not None
    assert item.prompt_snapshot_id is not None


@pytest.mark.asyncio
async def test_evidence_artifact_first_then_task_fallback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Prefer artifact events; fall back to task events when artifact empty."""
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        # Artifact present but with NO event payload -> fall back to task copy.
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            prompt_index=0,
            artifact_events=[],
            task_events=[_event(0, "fallback query")],
        )
        # Artifact absent/pruned entirely -> also fall back to task copy.
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 1, tzinfo=UTC),
            prompt_index=1,
            write_artifact=False,
            task_events=[_event(0, "pruned artifact query")],
        )
        await session.commit()
        result = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
    by_index = {i.prompt_index: i for i in result.items}
    assert by_index[0].event_source == "audit_task"
    assert [e.query for e in by_index[0].search_events] == ["fallback query"]
    assert by_index[1].event_source == "audit_task"
    assert by_index[1].artifact_id is None
    assert [e.query for e in by_index[1].search_events] == ["pruned artifact query"]


@pytest.mark.asyncio
async def test_evidence_malformed_entries_ignored_and_empty_preserved(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Malformed stored entries are skipped; empty query strings preserved."""
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            artifact_events=[
                "not-a-dict",
                123,
                None,
                _event(0, ""),  # empty query preserved (count-only event)
                _event(1, "real query"),
            ],
            search_query_count=2,
        )
        await session.commit()
        result = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
    item = result.items[0]
    # Only the two well-formed dict entries survive; text never fabricated.
    assert [e.query for e in item.search_events] == ["", "real query"]
    assert item.state == VisibilityFanoutState.QUERIES_AVAILABLE


@pytest.mark.asyncio
async def test_evidence_count_only_retired_transport(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Retired transport count-only row: count present, no query text."""
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            logical_engine=ENGINE_CHATGPT,
            transport_provider="retired",
            transport_model="openai/gpt-5.4",
            search_used=True,
            search_query_count=3,
            # A parser can emit count-only empty-query events.
            artifact_events=[_event(0, ""), _event(1, "")],
            # An analysis with citations but no query strings stays count_only.
            citations=[("https://ref.com/", "ref.com", "third_party")],
        )
        await session.commit()
        result = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
    item = result.items[0]
    assert item.state == VisibilityFanoutState.COUNT_ONLY
    assert item.query_text_available is False
    assert item.search_query_count == 3
    # The persisted transport identity remains part of the evidence row.
    assert item.transport_provider == "retired"
    assert item.transport_model == "openai/gpt-5.4"
    assert len(item.citations) == 1


@pytest.mark.asyncio
async def test_evidence_no_search_state(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No search signal + zero count + no query text -> no_search."""
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            search_used=False,
            search_query_count=0,
            artifact_events=[],
            task_events=[],
        )
        await session.commit()
        result = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
    item = result.items[0]
    assert item.state == VisibilityFanoutState.NO_SEARCH
    assert item.event_source == "none"
    assert item.search_events == []


@pytest.mark.asyncio
async def test_evidence_prompt_engine_audit_and_date_filters(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        prompt_a = seed.prompt_ids[0]
        # Gemini, prompt_a, Feb.
        audit_gemini, *_ = await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 10, tzinfo=UTC),
            logical_engine=ENGINE_GEMINI,
            prompt_id=prompt_a,
            artifact_events=[_event(0, "gemini query")],
        )
        # ChatGPT, no source prompt, January.
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 1, 10, tzinfo=UTC),
            logical_engine=ENGINE_CHATGPT,
            prompt_id=None,
            artifact_events=[_event(0, "chatgpt query")],
        )
        await session.commit()

        # Engine filter.
        gemini = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            logical_engine=ENGINE_GEMINI,
        )
        assert {i.logical_engine for i in gemini.items} == {ENGINE_GEMINI}

        # Prompt filter (source prompt on the frozen snapshot).
        by_prompt = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            prompt_id=prompt_a,
        )
        assert len(by_prompt.items) == 1
        assert by_prompt.items[0].prompt_id == prompt_a

        # Audit filter.
        by_audit = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            audit_id=audit_gemini.id,
        )
        assert len(by_audit.items) == 1
        assert by_audit.items[0].audit_id == audit_gemini.id

        # Date window (only Feb).
        windowed = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            from_at=datetime(2026, 2, 1, tzinfo=UTC),
            to_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        assert len(windowed.items) == 1
        assert windowed.items[0].logical_engine == ENGINE_GEMINI

        # Audit + date INTERSECT: the audit outside the window yields nothing.
        empty = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            audit_id=audit_gemini.id,
            from_at=datetime(2025, 1, 1, tzinfo=UTC),
            to_at=datetime(2025, 12, 31, tzinfo=UTC),
        )
        assert empty.items == []


@pytest.mark.asyncio
async def test_evidence_limit_truncation_and_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        # Three audits on distinct days.
        for day in (1, 2, 3):
            await _seed_evidence_execution(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                completed_at=datetime(2026, 2, day, tzinfo=UTC),
                artifact_events=[_event(0, f"day {day}")],
            )
        await session.commit()

        # limit=2 -> newest two, truncated True.
        limited = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            limit=2,
        )
        assert limited.truncated is True
        assert len(limited.items) == 2
        # Newest-first by completion.
        assert limited.items[0].completed_at == datetime(2026, 2, 3, tzinfo=UTC)
        assert limited.items[1].completed_at == datetime(2026, 2, 2, tzinfo=UTC)

        full = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            limit=100,
        )
        assert full.truncated is False
        assert len(full.items) == 3


@pytest.mark.asyncio
async def test_evidence_deterministic_order_within_audit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Within one audit, order by prompt index, engine, repetition."""
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=2)
        audit = Audit(
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            status=AUDIT_STATUS_COMPLETED,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            requested_count=3,
            completed_count=3,
        )
        session.add(audit)
        await session.flush()
        # Seed out of natural order.
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            prompt_index=1,
            repetition=0,
            audit=audit,
        )
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            prompt_index=0,
            repetition=1,
            audit=audit,
        )
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            prompt_index=0,
            repetition=0,
            audit=audit,
        )
        await session.commit()
        result = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
    order = [(i.prompt_index, i.repetition) for i in result.items]
    assert order == [(0, 0), (0, 1), (1, 0)]


@pytest.mark.asyncio
async def test_evidence_deleted_prompt_snapshot_readable(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A deleted source prompt stays readable via frozen text + null id."""
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            prompt_id=None,  # source prompt deleted (SET NULL)
            prompt_text="frozen prompt text survives",
            artifact_events=[_event(0, "q")],
        )
        await session.commit()
        result = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
        # Not selectable by a current prompt id...
        by_prompt = await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            prompt_id=seed.prompt_ids[0],
        )
    item = result.items[0]
    assert item.prompt_id is None
    assert item.prompt_text == "frozen prompt text survives"
    assert by_prompt.items == []


@pytest.mark.asyncio
async def test_evidence_workspace_isolation_and_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            artifact_events=[_event(0, "q")],
        )
        await session.commit()
        # Foreign workspace sees nothing (invariant 5).
        foreign = await get_visibility_evidence(
            session,
            workspace_id=_uuid.uuid4(),
            project_id=seed.project_id,
        )
        assert foreign.items == []
        assert foreign.truncated is False


@pytest.mark.asyncio
async def test_evidence_cross_workspace_audit_404(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A selected audit outside the workspace/project must 404 (no leak)."""
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        other = await seed_audit_fixtures(session, prompt_count=1)
        other_audit, *_ = await _seed_evidence_execution(
            session,
            workspace_id=other.workspace_id,
            project_id=other.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            artifact_events=[_event(0, "q")],
        )
        await session.commit()
        with pytest.raises(AnalysisNotFoundError):
            await get_visibility_evidence(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                audit_id=other_audit.id,
            )


@pytest.mark.asyncio
async def test_evidence_invalid_query_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        await session.commit()
        with pytest.raises(TrendQueryError):
            await get_visibility_evidence(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                logical_engine="bing",
            )
        with pytest.raises(TrendQueryError):
            await get_visibility_evidence(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                from_at=datetime(2026, 3, 1, tzinfo=UTC),
                to_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        with pytest.raises(TrendQueryError):
            await get_visibility_evidence(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                from_at=datetime(2026, 3, 1),  # naive
            )
        with pytest.raises(TrendQueryError):
            await get_visibility_evidence(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                limit=0,
            )
        with pytest.raises(TrendQueryError):
            await get_visibility_evidence(
                session,
                workspace_id=seed.workspace_id,
                project_id=seed.project_id,
                limit=501,
            )


@pytest.mark.asyncio
async def test_evidence_never_calls_provider_and_is_read_only(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider factory patched to fail; row counts unchanged after read."""

    def _boom(**_: object):
        raise AssertionError("evidence read must never construct an adapter")

    monkeypatch.setattr(audit_worker, "build_adapter", _boom)

    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=1)
        await _seed_evidence_execution(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            completed_at=datetime(2026, 2, 1, tzinfo=UTC),
            artifact_events=[_event(0, "immutable query")],
            brand_mentions=[("Acme Corp", 0)],
            citations=[("https://acme.com/", "acme.com", "owned")],
        )
        await session.commit()

    async with session_factory() as session:
        before_analyses = await session.scalar(
            select(func.count()).select_from(ResponseAnalysis)
        )
        before_events = await session.scalar(
            select(RawResponseArtifact.search_events).limit(1)
        )
        await get_visibility_evidence(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
        )
        after_analyses = await session.scalar(
            select(func.count()).select_from(ResponseAnalysis)
        )
        after_events = await session.scalar(
            select(RawResponseArtifact.search_events).limit(1)
        )
    # A pure read: no derived rows created and stored events unchanged.
    assert before_analyses == after_analyses == 1
    assert before_events == after_events == [_event(0, "immutable query")]
