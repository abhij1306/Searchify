"""Product analyzer pass: worker wiring + derived rows + snapshots.

Runs the real claim/persist/finalize loop (provider calls mocked, no
network) against a seeded catalog and asserts the agentic-commerce
acceptance:
  - every succeeded task yields a ``ProductResponseAnalysis`` + one
    ``ProductMention`` per mentioned catalog entry, each stamped with
    raw-artifact provenance + the PRODUCT analyzer/rule versions
    (invariant 4);
  - finalize upserts one ``ProductMetricSnapshot`` per (audit, product) and
    per (audit, competitor_product) with SOV/rank/price aggregates + the
    exact evidence set (invariant 7);
  - the brand-level derived rows are byte-identical (zero mutation);
  - finalize is idempotent on re-run; an empty catalog writes nothing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analysis.product_service import finalize_audit_product_analysis
from app.connectors.answer_engines.contracts import (
    AnswerEngineRequest,
    AnswerEngineResponse,
    CitationResult,
    SearchEventResult,
)
from app.core.config.analysis import ANALYZER_VERSION
from app.core.config.audits import AUDIT_STATUS_COMPLETED, audit_settings
from app.core.config.products import (
    PRODUCT_ANALYZER_VERSION,
    PRODUCT_SCORING_RULE_VERSION,
)
from app.core.config.provider_catalog import ENGINE_GEMINI, TRANSPORT_GOOGLE
from app.domain.audits.planner import create_audit, list_tasks
from app.models.analysis import BrandMention, MetricSnapshot, ResponseAnalysis
from app.models.audit import Audit
from app.models.brand import Competitor
from app.models.product import (
    CompetitorProduct,
    Product,
    ProductMention,
    ProductMetricSnapshot,
    ProductResponseAnalysis,
)
from app.workers import audit_worker
from app.workers.audit_worker import AuditWorker
from tests.component.audit_helpers import seed_audit_fixtures

_ANSWER = (
    "1. Acme VoltBike 500 — the best commuter pick at $2,499.00\n"
    "2. Globex CityBike 450 — a solid alternative at $2,399.00\n"
    "3. Something generic with no catalog entry"
)


class _ProductStubAdapter:
    """In-memory stand-in answering with a product list (no network)."""

    logical_engine = ENGINE_GEMINI
    transport_provider = TRANSPORT_GOOGLE

    def __init__(self, **_: object) -> None:
        pass

    async def execute(self, request: AnswerEngineRequest) -> AnswerEngineResponse:
        return AnswerEngineResponse(
            logical_engine=self.logical_engine,
            transport_provider=self.transport_provider,
            transport_model=request.model,
            answer_text=_ANSWER,
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
    def _build(**_: object) -> _ProductStubAdapter:
        return _ProductStubAdapter()

    monkeypatch.setattr(audit_worker, "build_adapter", _build)
    monkeypatch.setattr(audit_settings, "min_request_interval_seconds", 0.0)
    monkeypatch.setattr(audit_settings, "heartbeat_interval_seconds", 3600.0)


async def _seed_with_catalog(session: AsyncSession, *, prompts: int = 2):
    seed = await seed_audit_fixtures(session, prompt_count=prompts)
    product = Product(
        project_id=seed.project_id,
        sku="AC-VB500",
        name="Acme VoltBike 500",
        aliases=["VoltBike"],
        price=Decimal("2499.00"),
        currency="USD",
        url="https://acme.com/p/voltbike",
    )
    session.add(product)
    competitor = await session.scalar(
        select(Competitor).where(Competitor.project_id == seed.project_id)
    )
    assert competitor is not None
    competitor_product = CompetitorProduct(
        project_id=seed.project_id,
        competitor_id=competitor.id,
        name="Globex CityBike 450",
        price=Decimal("2399.00"),
        currency="USD",
    )
    session.add(competitor_product)
    await session.commit()
    return seed, product, competitor_product


async def _run_audit(
    session_factory: async_sessionmaker[AsyncSession], seed, *, reps: int = 1
):
    async with session_factory() as session:
        audit = await create_audit(
            session,
            workspace_id=seed.workspace_id,
            project_id=seed.project_id,
            engines=seed.engines,
            prompt_set_id=seed.prompt_set_id,
            repetitions=reps,
            random_seed="1",
        )
    worker = AuditWorker(session_factory=session_factory, owner="w-products")
    await worker.run_until_idle()
    return audit


@pytest.mark.asyncio
async def test_worker_writes_product_derived_rows_and_snapshots(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    async with session_factory() as session:
        seed, product, competitor_product = await _seed_with_catalog(session, prompts=2)
    audit = await _run_audit(session_factory, seed, reps=1)  # 2 tasks

    async with session_factory() as session:
        tasks = await list_tasks(
            session, workspace_id=seed.workspace_id, audit_id=audit.id
        )
        assert {t.status for t in tasks} == {"succeeded"}

        # One ProductResponseAnalysis per execution, full provenance (inv 4).
        analyses = list(
            (
                await session.scalars(
                    select(ProductResponseAnalysis).where(
                        ProductResponseAnalysis.audit_id == audit.id
                    )
                )
            ).all()
        )
        assert len(analyses) == 2
        artifact_by_task = {t.id: t.result_artifact_id for t in tasks}
        for analysis in analyses:
            assert analysis.artifact_id == artifact_by_task[analysis.task_id]
            assert analysis.product_analyzer_version == PRODUCT_ANALYZER_VERSION
            assert analysis.product_scoring_rule_version == PRODUCT_SCORING_RULE_VERSION
            assert analysis.logical_engine == ENGINE_GEMINI
            assert analysis.transport_provider == TRANSPORT_GOOGLE
            assert analysis.own_product_mention_count == 1
            assert analysis.competitor_product_mention_count == 1
            assert analysis.products_with_price_match == 2

        # One ProductMention per mentioned entry per execution.
        mentions = list(
            (
                await session.scalars(
                    select(ProductMention).where(ProductMention.audit_id == audit.id)
                )
            ).all()
        )
        assert len(mentions) == 4
        own = [m for m in mentions if m.product_id == product.id]
        competitor = [
            m for m in mentions if m.competitor_product_id == competitor_product.id
        ]
        assert len(own) == 2
        assert len(competitor) == 2
        for mention in own:
            assert mention.matched_name == "Acme VoltBike 500"
            assert mention.matched_sku == "AC-VB500"
            assert mention.rank_position == 1
            assert mention.price_value == Decimal("2499.00")
            assert mention.price_currency == "USD"
            assert mention.price_matches_catalog is True
            assert mention.artifact_id is not None
            assert mention.product_analyzer_version == PRODUCT_ANALYZER_VERSION
            assert mention.workspace_id == seed.workspace_id
        assert {m.rank_position for m in competitor} == {2}

        # One ProductMetricSnapshot per catalog entry (partial unique indexes).
        snapshots = list(
            (
                await session.scalars(
                    select(ProductMetricSnapshot).where(
                        ProductMetricSnapshot.audit_id == audit.id
                    )
                )
            ).all()
        )
        assert len(snapshots) == 2
        own_snapshot = next(s for s in snapshots if s.product_id == product.id)
        competitor_snapshot = next(
            s for s in snapshots if s.competitor_product_id == competitor_product.id
        )
        assert own_snapshot.mention_count == 2
        assert own_snapshot.sov_share == 0.5
        assert own_snapshot.avg_rank == 1.0
        assert own_snapshot.rank_distribution["top_1"] == 2
        assert own_snapshot.price_mention_count == 2
        assert own_snapshot.price_accuracy_rate == 1.0
        assert own_snapshot.product_analyzer_version == PRODUCT_ANALYZER_VERSION
        assert own_snapshot.project_id == seed.project_id
        assert len(own_snapshot.source_analysis_ids) == 2
        assert len(own_snapshot.source_artifact_ids) == 2
        assert competitor_snapshot.avg_rank == 2.0
        assert competitor_snapshot.sov_share == 0.5

        # Brand-level rows are untouched by the product pass.
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ResponseAnalysis)
                .where(ResponseAnalysis.audit_id == audit.id)
            )
        ) == 2
        brand_mentions = list(
            (
                await session.scalars(
                    select(BrandMention).where(BrandMention.audit_id == audit.id)
                )
            ).all()
        )
        assert len(brand_mentions) == 2
        assert all(m.analyzer_version == ANALYZER_VERSION for m in brand_mentions)
        brand_snapshot = await session.scalar(
            select(MetricSnapshot).where(MetricSnapshot.audit_id == audit.id)
        )
        assert brand_snapshot is not None
        assert brand_snapshot.visibility_score == 100.0

        refreshed = await session.get(Audit, audit.id)
        assert refreshed is not None
        assert refreshed.status == AUDIT_STATUS_COMPLETED

        # Finalize is idempotent: re-running reuses the same snapshot rows.
        snapshot_ids = {s.id for s in snapshots}
        again = await finalize_audit_product_analysis(session, audit=refreshed)
        await session.commit()
        assert {s.id for s in again} == snapshot_ids
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProductResponseAnalysis)
                .where(ProductResponseAnalysis.audit_id == audit.id)
            )
        ) == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProductMetricSnapshot)
                .where(ProductMetricSnapshot.audit_id == audit.id)
            )
        ) == 2


@pytest.mark.asyncio
async def test_worker_empty_catalog_writes_no_product_rows(
    session_factory: async_sessionmaker[AsyncSession],
    _stub_adapter,
) -> None:
    async with session_factory() as session:
        seed = await seed_audit_fixtures(session, prompt_count=2)
    audit = await _run_audit(session_factory, seed, reps=1)

    async with session_factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProductResponseAnalysis)
                .where(ProductResponseAnalysis.audit_id == audit.id)
            )
        ) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProductMetricSnapshot)
                .where(ProductMetricSnapshot.audit_id == audit.id)
            )
        ) == 0
        # The brand loop is unaffected by the empty catalog.
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ResponseAnalysis)
                .where(ResponseAnalysis.audit_id == audit.id)
            )
        ) == 2
