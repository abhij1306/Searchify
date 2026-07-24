# Product-analysis persistence + finalize wiring (invariants 4/7/9).
#
# Sibling of ``analysis/service.py`` (brand level): the deterministic product
# analyzer pass runs over the same persisted ``RawResponseArtifact`` rows and
# writes sibling derived rows (``ProductResponseAnalysis`` / ``ProductMention``
# / ``ProductMetricSnapshot``) — it NEVER touches the brand-level
# ``ResponseAnalysis`` / ``BrandMention`` / ... rows.
#   - ``analyze_task_products`` scores ONE completed execution from its frozen
#     catalog + persisted answer (no provider call) and persists the derived
#     rows with raw-artifact provenance + product analyzer versions.
#     Idempotent per task; a no-op when the frozen catalog is empty.
#   - ``finalize_audit_product_analysis`` upserts one ``ProductMetricSnapshot``
#     per (audit, product) / (audit, competitor_product) from the persisted
#     analyses only (invariant 7), stamping the exact evidence set.
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.product_scoring import (
    ProductScoringConfig,
    aggregate_product_run,
    score_product_execution,
)
from app.core.config.audits import TASK_STATUS_SUCCEEDED
from app.core.config.products import (
    PRODUCT_ANALYZER_VERSION,
    PRODUCT_SCORING_RULE_VERSION,
)
from app.models.audit import Audit, AuditTask
from app.models.product import (
    ProductMention,
    ProductMetricSnapshot,
    ProductResponseAnalysis,
)


def build_product_scoring_config(configuration: dict | None) -> ProductScoringConfig:
    """Build the product scorer config from the audit's FROZEN catalog.

    The planner froze the catalog into ``configuration`` at creation (via
    ``project_product_identity``); scoring reads that frozen copy, never the
    live catalog (determinism, invariant 9).
    """
    return ProductScoringConfig.from_project(configuration or {})


async def analyze_task_products(
    session: AsyncSession,
    *,
    task: AuditTask,
    config: ProductScoringConfig,
) -> ProductResponseAnalysis | None:
    """Score one completed execution's product signals and persist them.

    Deterministic + idempotent: an existing analysis for this task is
    returned unchanged; a task with no answer text still yields an analysis
    row (all-false signals) so provenance is complete. No-op (returns None)
    when the frozen catalog is empty. Caller owns the commit.
    """
    existing = await session.scalar(
        select(ProductResponseAnalysis).where(
            ProductResponseAnalysis.task_id == task.id
        )
    )
    if existing is not None:
        return existing
    if not config.products and not config.competitor_products:
        return None

    score = score_product_execution(answer_text=task.answer_text or "", config=config)
    analysis = ProductResponseAnalysis(
        workspace_id=task.workspace_id,
        audit_id=task.audit_id,
        task_id=task.id,
        artifact_id=task.result_artifact_id,
        product_analyzer_version=PRODUCT_ANALYZER_VERSION,
        product_scoring_rule_version=PRODUCT_SCORING_RULE_VERSION,
        logical_engine=task.logical_engine,
        transport_provider=task.transport_provider,
        transport_model=task.transport_model,
        prompt_index=task.prompt_index,
        repetition=task.repetition,
        own_product_mention_count=score["own_product_mention_count"],
        competitor_product_mention_count=score["competitor_product_mention_count"],
        products_with_price_match=score["products_with_price_match"],
        score=score,
    )
    session.add(analysis)
    await session.flush()  # assign analysis.id for child rows

    entry_names = {entry.id: entry.name for entry in config.products}
    entry_skus = {entry.id: entry.sku for entry in config.products}
    for signals in score["products"]:
        if not signals.get("mentioned"):
            continue
        session.add(
            _mention_row(
                task=task,
                analysis=analysis,
                signals=signals,
                product_id=uuid.UUID(signals["product_id"]),
                competitor_product_id=None,
                matched_name=entry_names.get(signals["product_id"], ""),
                matched_sku=entry_skus.get(signals["product_id"], ""),
            )
        )
    competitor_names = {entry.id: entry.name for entry in config.competitor_products}
    for signals in score["competitor_products"]:
        if not signals.get("mentioned"):
            continue
        session.add(
            _mention_row(
                task=task,
                analysis=analysis,
                signals=signals,
                product_id=None,
                competitor_product_id=uuid.UUID(signals["competitor_product_id"]),
                matched_name=competitor_names.get(
                    signals["competitor_product_id"], ""
                ),
                matched_sku="",
            )
        )
    return analysis


def _mention_row(
    *,
    task: AuditTask,
    analysis: ProductResponseAnalysis,
    signals: dict,
    product_id: uuid.UUID | None,
    competitor_product_id: uuid.UUID | None,
    matched_name: str,
    matched_sku: str,
) -> ProductMention:
    return ProductMention(
        workspace_id=task.workspace_id,
        audit_id=task.audit_id,
        analysis_id=analysis.id,
        artifact_id=task.result_artifact_id,
        product_analyzer_version=PRODUCT_ANALYZER_VERSION,
        product_id=product_id,
        competitor_product_id=competitor_product_id,
        matched_name=matched_name,
        matched_sku=matched_sku,
        first_offset=signals.get("first_offset"),
        rank_position=signals.get("rank_position"),
        price_text=str(signals.get("price_text") or "")[:64],
        price_value=signals.get("price_value"),
        price_currency=str(signals.get("price_currency") or "")[:3],
        price_matches_catalog=signals.get("price_matches_catalog"),
    )


async def finalize_audit_product_analysis(
    session: AsyncSession, *, audit: Audit
) -> list[ProductMetricSnapshot]:
    """Upsert the per-(audit, entry) ``ProductMetricSnapshot`` rows.

    Defensively ensures every succeeded task has a product analysis (mirror
    ``finalize_audit_analysis``), then aggregates from the PERSISTED analyses
    only (invariant 7) and stamps the exact evidence set per snapshot
    (invariant 4). Idempotent. Caller owns the commit. Returns [] when the
    frozen catalog is empty (product analysis disabled for the audit).
    """
    config = build_product_scoring_config(audit.configuration)
    if not config.products and not config.competitor_products:
        return []

    succeeded_tasks = list(
        (
            await session.scalars(
                select(AuditTask)
                .where(AuditTask.audit_id == audit.id)
                .where(AuditTask.status == TASK_STATUS_SUCCEEDED)
            )
        ).all()
    )
    for task in succeeded_tasks:
        await analyze_task_products(session, task=task, config=config)
    await session.flush()

    analyses = list(
        (
            await session.scalars(
                select(ProductResponseAnalysis).where(
                    ProductResponseAnalysis.audit_id == audit.id
                )
            )
        ).all()
    )
    aggregates = aggregate_product_run(
        [analysis.score or {} for analysis in analyses], config
    )

    # Per-engine breakdown (mirrors ``aggregate_run`` per-engine pattern):
    # group the persisted analyses by engine and re-aggregate each group.
    per_engine: dict[str, dict[str, dict]] = {}
    engines = sorted({analysis.logical_engine for analysis in analyses})
    for engine in engines:
        per_engine[engine] = aggregate_product_run(
            [
                analysis.score or {}
                for analysis in analyses
                if analysis.logical_engine == engine
            ],
            config,
        )

    existing_snapshots = list(
        (
            await session.scalars(
                select(ProductMetricSnapshot).where(
                    ProductMetricSnapshot.audit_id == audit.id
                )
            )
        ).all()
    )
    by_entry = {
        str(snapshot.product_id or snapshot.competitor_product_id): snapshot
        for snapshot in existing_snapshots
    }

    snapshots: list[ProductMetricSnapshot] = []
    for entry_id, aggregate in aggregates.items():
        is_product = aggregate["kind"] == "product"
        # The exact evidence set for this entry (invariant 4): the persisted
        # analyses that mention it, and their raw artifacts.
        evidence = [
            analysis
            for analysis in analyses
            if _mentions_entry(analysis.score or {}, entry_id, is_product)
        ]
        snapshot = by_entry.get(entry_id)
        if snapshot is None:
            snapshot = ProductMetricSnapshot(
                workspace_id=audit.workspace_id,
                audit_id=audit.id,
                project_id=audit.project_id,
            )
            session.add(snapshot)
        snapshot.product_id = uuid.UUID(entry_id) if is_product else None
        snapshot.competitor_product_id = None if is_product else uuid.UUID(entry_id)
        snapshot.product_analyzer_version = PRODUCT_ANALYZER_VERSION
        snapshot.product_scoring_rule_version = PRODUCT_SCORING_RULE_VERSION
        snapshot.mention_count = int(aggregate["mention_count"])
        snapshot.sov_share = float(aggregate["sov_share"])
        snapshot.avg_rank = aggregate["avg_rank"]
        snapshot.rank_distribution = aggregate["rank_distribution"]
        snapshot.price_mention_count = int(aggregate["price_mention_count"])
        snapshot.price_accuracy_rate = aggregate["price_accuracy_rate"]
        snapshot.metrics = {
            # Frozen entry id: survives the SET NULL a catalog delete triggers
            # on the live FKs, so projections can still key the snapshot.
            "entry_id": entry_id,
            **aggregate,
            "per_engine": {
                engine: engine_aggregates.get(entry_id)
                for engine, engine_aggregates in per_engine.items()
            },
        }
        snapshot.source_analysis_ids = [str(a.id) for a in evidence]
        snapshot.source_artifact_ids = [
            str(a.artifact_id) for a in evidence if a.artifact_id is not None
        ]
        snapshots.append(snapshot)
    return snapshots


def _mentions_entry(score: dict, entry_id: str, is_product: bool) -> bool:
    section = "products" if is_product else "competitor_products"
    key = "product_id" if is_product else "competitor_product_id"
    return any(
        str(signals.get(key) or "") == entry_id and signals.get("mentioned")
        for signals in score.get(section) or []
    )
