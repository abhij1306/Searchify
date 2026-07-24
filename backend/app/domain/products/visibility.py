# Product visibility projections (invariant 7 — persisted rows only).
#
# Every function here reads persisted rows (``ProductMetricSnapshot`` /
# ``ProductResponseAnalysis`` / ``ProductMention`` / ``Audit``) and NEVER
# calls a provider and NEVER recomputes a score. They back the product
# visibility/evidence/export endpoints. All queries are workspace-scoped
# (invariant 5). Mirrors ``domain/analysis/service.py`` (B6).
from __future__ import annotations

import csv
import io
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.product_service import build_product_scoring_config
from app.core.config.audits import (
    AUDIT_STATUS_COMPLETED,
    AUDIT_STATUS_PARTIALLY_COMPLETED,
)
from app.core.config.products import (
    PRODUCT_EVIDENCE_DEFAULT_LIMIT,
    PRODUCT_EVIDENCE_MAX_LIMIT,
)
from app.core.config.provider_catalog import LOGICAL_ENGINES
from app.domain.analysis.service import AnalysisNotFoundError, TrendQueryError
from app.domain.products.schemas import (
    CompetitorProductVisibilityEntry,
    ProductEvidenceItem,
    ProductEvidenceResponse,
    ProductVisibilityEntry,
    ProductVisibilityResponse,
)
from app.domain.products.service import ProductNotFoundError
from app.models.audit import Audit, AuditPromptSnapshot, AuditTask
from app.models.product import (
    Product,
    ProductMention,
    ProductMetricSnapshot,
    ProductResponseAnalysis,
)
from app.models.project import Project

# A run is projection-eligible when fully or partially completed (mirror B6).
_DASHBOARD_STATUSES = (
    AUDIT_STATUS_COMPLETED,
    AUDIT_STATUS_PARTIALLY_COMPLETED,
)

# Single source for the repeated "audit missing" detail (asserted by tests).
_AUDIT_NOT_FOUND = "Audit not found"

_CSV_COLUMNS = [
    "audit_id",
    "product",
    "sku",
    "mentions",
    "sov",
    "avg_rank",
    "price_accuracy",
    "engine",
]


async def get_product_visibility(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    audit_id: uuid.UUID | None = None,
    engine: str | None = None,
) -> ProductVisibilityResponse:
    """Serve the selected-audit product dashboard projection.

    Defaults to the project's latest completed/partially-completed audit that
    has product snapshots when ``audit_id`` is omitted. Identity (sku/name/
    competitor_name) comes from the audit's FROZEN configuration so the
    projection survives later catalog deletes. Pure read of persisted rows;
    no provider call (invariant 7).

    ``engine`` slices every entry to its PERSISTED per-engine aggregate
    (stored in the snapshot at finalize) — still a pure projection, never a
    recompute. An unknown engine raises ``TrendQueryError`` (HTTP 422).
    """
    if engine is not None and engine not in LOGICAL_ENGINES:
        raise TrendQueryError(f"Unknown logical engine: {engine!r}")
    if audit_id is None:
        audit_id = await _latest_product_audit_id(
            session, workspace_id=workspace_id, project_id=project_id
        )
        if audit_id is None:
            raise AnalysisNotFoundError(
                "No completed audit with product metrics for project"
            )

    audit = await session.scalar(
        select(Audit).where(
            Audit.id == audit_id,
            Audit.workspace_id == workspace_id,
            Audit.project_id == project_id,
        )
    )
    if audit is None:
        raise AnalysisNotFoundError(_AUDIT_NOT_FOUND)

    snapshots = list(
        (
            await session.scalars(
                select(ProductMetricSnapshot).where(
                    ProductMetricSnapshot.audit_id == audit.id,
                    ProductMetricSnapshot.workspace_id == workspace_id,
                )
            )
        ).all()
    )
    if not snapshots:
        raise AnalysisNotFoundError("Product metrics not available for audit")

    config = build_product_scoring_config(audit.configuration)
    by_entry = {_snapshot_entry_id(snapshot): snapshot for snapshot in snapshots}

    sliced = {entry_id: _entry_metrics(s, engine) for entry_id, s in by_entry.items()}

    products: list[ProductVisibilityEntry] = []
    for entry in config.products:
        snapshot = by_entry.get(entry.id)
        if snapshot is None:
            continue
        metrics = sliced[entry.id]
        products.append(
            ProductVisibilityEntry(
                product_id=snapshot.product_id,
                sku=entry.sku,
                name=entry.name,
                mention_count=metrics["mention_count"],
                sov_share=metrics["sov_share"],
                avg_rank=metrics["avg_rank"],
                rank_distribution=metrics["rank_distribution"],
                price_mention_count=metrics["price_mention_count"],
                price_accuracy_rate=metrics["price_accuracy_rate"],
            )
        )

    competitor_products: list[CompetitorProductVisibilityEntry] = []
    for entry in config.competitor_products:
        snapshot = by_entry.get(entry.id)
        if snapshot is None:
            continue
        metrics = sliced[entry.id]
        competitor_products.append(
            CompetitorProductVisibilityEntry(
                competitor_product_id=snapshot.competitor_product_id,
                competitor_name=entry.competitor,
                name=entry.name,
                mention_count=metrics["mention_count"],
                sov_share=metrics["sov_share"],
                avg_rank=metrics["avg_rank"],
                rank_distribution=metrics["rank_distribution"],
                price_mention_count=metrics["price_mention_count"],
                price_accuracy_rate=metrics["price_accuracy_rate"],
            )
        )

    total_analyses = await session.scalar(
        select(func.count())
        .select_from(ProductResponseAnalysis)
        .where(ProductResponseAnalysis.audit_id == audit.id)
    )
    first = snapshots[0]
    return ProductVisibilityResponse(
        project_id=project_id,
        audit_id=audit.id,
        audit_status=audit.status,
        product_analyzer_version=first.product_analyzer_version,
        product_scoring_rule_version=first.product_scoring_rule_version,
        total_mentions=sum(m["mention_count"] for m in sliced.values()),
        total_analyses=int(total_analyses or 0),
        products=products,
        competitor_products=competitor_products,
        created_at=max(s.created_at for s in snapshots),
    )


async def get_product_evidence(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    product_id: uuid.UUID,
    audit_id: uuid.UUID | None = None,
    engine: str | None = None,
    limit: int = PRODUCT_EVIDENCE_DEFAULT_LIMIT,
) -> ProductEvidenceResponse:
    """Project the workspace-scoped product mention evidence (invariant 7).

    Pure READ-ONLY projection over persisted ``ProductMention`` rows joined to
    their parent analysis + frozen prompt text (``AuditPromptSnapshot``) and
    task/execution ids for ``/runs`` linking. Optional ``audit_id`` / ``engine``
    filters intersect. Returns at most ``limit`` items newest-first with
    ``truncated`` set when more matches exist (mirror
    ``get_visibility_evidence``).
    """
    if engine is not None and engine not in LOGICAL_ENGINES:
        raise TrendQueryError(f"Unknown logical engine: {engine!r}")
    if limit < 1 or limit > PRODUCT_EVIDENCE_MAX_LIMIT:
        raise TrendQueryError(
            f"'limit' must be between 1 and {PRODUCT_EVIDENCE_MAX_LIMIT}"
        )

    # Ownership pre-check: the product must belong to this workspace (else a
    # cross-workspace/missing id must 404 — never leak that it exists).
    owning = await session.scalar(
        select(Product.id)
        .join(Project, Project.id == Product.project_id)
        .where(Product.id == product_id, Project.workspace_id == workspace_id)
    )
    if owning is None:
        raise ProductNotFoundError(f"Product {product_id} not found")

    # If an audit is selected, it must belong to this workspace (else 404).
    if audit_id is not None:
        owning_audit = await session.scalar(
            select(Audit.id).where(
                Audit.id == audit_id, Audit.workspace_id == workspace_id
            )
        )
        if owning_audit is None:
            raise AnalysisNotFoundError(_AUDIT_NOT_FOUND)

    stmt = (
        select(
            ProductMention,
            ProductResponseAnalysis,
            AuditTask,
            AuditPromptSnapshot,
        )
        .join(
            ProductResponseAnalysis,
            ProductResponseAnalysis.id == ProductMention.analysis_id,
        )
        .join(AuditTask, AuditTask.id == ProductResponseAnalysis.task_id)
        .join(
            AuditPromptSnapshot,
            AuditPromptSnapshot.id == AuditTask.prompt_snapshot_id,
        )
        .join(Audit, Audit.id == ProductMention.audit_id)
        .where(
            ProductMention.workspace_id == workspace_id,
            ProductMention.product_id == product_id,
            Audit.status.in_(_DASHBOARD_STATUSES),
        )
    )
    if audit_id is not None:
        stmt = stmt.where(ProductMention.audit_id == audit_id)
    if engine is not None:
        stmt = stmt.where(ProductResponseAnalysis.logical_engine == engine)
    # Newest-first window, deterministic tie-breaks (mirror B6 evidence).
    stmt = stmt.order_by(
        Audit.completed_at.desc().nullslast(),
        Audit.created_at.desc(),
        ProductResponseAnalysis.prompt_index.asc(),
        ProductResponseAnalysis.logical_engine.asc(),
        ProductResponseAnalysis.repetition.asc(),
        ProductMention.id.asc(),
    ).limit(limit + 1)

    rows = list((await session.execute(stmt)).all())
    truncated = len(rows) > limit
    rows = rows[:limit]
    items = [
        ProductEvidenceItem(
            mention_id=mention.id,
            audit_id=mention.audit_id,
            task_id=analysis.task_id,
            artifact_id=mention.artifact_id,
            logical_engine=analysis.logical_engine,
            transport_model=analysis.transport_model,
            prompt_text=prompt_snapshot.text or "",
            prompt_index=analysis.prompt_index,
            repetition=analysis.repetition,
            matched_name=mention.matched_name,
            matched_sku=mention.matched_sku,
            first_offset=mention.first_offset,
            rank_position=mention.rank_position,
            price_text=mention.price_text,
            price_value=(
                float(mention.price_value)
                if mention.price_value is not None
                else None
            ),
            price_currency=mention.price_currency,
            price_matches_catalog=mention.price_matches_catalog,
            created_at=mention.created_at,
        )
        for mention, analysis, _task, prompt_snapshot in rows
    ]
    return ProductEvidenceResponse(items=items, truncated=truncated)


async def load_product_visibility_export_bundle(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    project_id: uuid.UUID,
    audit_id: uuid.UUID | None = None,
) -> tuple[Audit, list[ProductMetricSnapshot]]:
    """Load the audit + its product snapshots for CSV export (invariant 7).

    Defaults to the latest dashboard-eligible audit with product snapshots
    when ``audit_id`` is omitted (same resolution as the dashboard
    projection). 404-class ``AnalysisNotFoundError`` when there is nothing
    persisted to render.
    """
    if audit_id is None:
        audit_id = await _latest_product_audit_id(
            session, workspace_id=workspace_id, project_id=project_id
        )
        if audit_id is None:
            raise AnalysisNotFoundError(
                "No completed audit with product metrics for project"
            )
    audit = await session.scalar(
        select(Audit).where(
            Audit.id == audit_id,
            Audit.workspace_id == workspace_id,
            Audit.project_id == project_id,
        )
    )
    if audit is None:
        raise AnalysisNotFoundError(_AUDIT_NOT_FOUND)
    snapshots = list(
        (
            await session.scalars(
                select(ProductMetricSnapshot).where(
                    ProductMetricSnapshot.audit_id == audit.id,
                    ProductMetricSnapshot.workspace_id == workspace_id,
                )
            )
        ).all()
    )
    if not snapshots:
        raise AnalysisNotFoundError("Product metrics not available for audit")
    return audit, snapshots


def product_visibility_csv(
    audit: Audit, snapshots: list[ProductMetricSnapshot]
) -> str:
    """Render the per-entry product visibility rows as CSV (invariant 7).

    Renders PERSISTED ``ProductMetricSnapshot`` rows only — one overall row
    (``engine=all``) plus one row per engine from the snapshot's persisted
    per-engine breakdown. Column style mirrors ``analysis/exports.py``.
    """
    config = build_product_scoring_config(audit.configuration)
    by_entry = {_snapshot_entry_id(snapshot): snapshot for snapshot in snapshots}
    ordered = [(entry.id, entry.name, entry.sku) for entry in config.products]
    ordered += [
        (entry.id, entry.name, "") for entry in config.competitor_products
    ]

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_COLUMNS)
    writer.writeheader()

    def _row(
        *, name: str, sku: str, engine: str, aggregate: dict
    ) -> dict[str, object]:
        return {
            "audit_id": str(audit.id),
            "product": name,
            "sku": sku,
            "mentions": aggregate.get("mention_count", 0),
            "sov": aggregate.get("sov_share", 0.0),
            "avg_rank": aggregate.get("avg_rank") or "",
            "price_accuracy": aggregate.get("price_accuracy_rate") or "",
            "engine": engine,
        }

    for entry_id, name, sku in ordered:
        snapshot = by_entry.get(entry_id)
        if snapshot is None:
            continue
        overall = {
            "mention_count": snapshot.mention_count,
            "sov_share": snapshot.sov_share,
            "avg_rank": snapshot.avg_rank,
            "price_accuracy_rate": snapshot.price_accuracy_rate,
        }
        writer.writerow(_row(name=name, sku=sku, engine="all", aggregate=overall))
        per_engine = (snapshot.metrics or {}).get("per_engine") or {}
        for engine in sorted(per_engine):
            aggregate = per_engine[engine]
            if aggregate is None:
                continue
            writer.writerow(
                _row(name=name, sku=sku, engine=engine, aggregate=aggregate)
            )
    return buffer.getvalue()


def _entry_metrics(
    snapshot: ProductMetricSnapshot, engine: str | None
) -> dict[str, Any]:
    """One snapshot's entry metrics, optionally engine-sliced (persisted only).

    With ``engine=None`` the overall snapshot columns are served. With an
    engine the PERSISTED per-engine aggregate (written at finalize) is served;
    an entry with no data for that engine reads as a zero-filled aggregate —
    never a recompute (invariant 7).
    """
    if engine is None:
        return {
            "mention_count": snapshot.mention_count,
            "sov_share": snapshot.sov_share,
            "avg_rank": snapshot.avg_rank,
            "rank_distribution": dict(snapshot.rank_distribution or {}),
            "price_mention_count": snapshot.price_mention_count,
            "price_accuracy_rate": snapshot.price_accuracy_rate,
        }
    aggregate = ((snapshot.metrics or {}).get("per_engine") or {}).get(engine)
    return {
        "mention_count": int((aggregate or {}).get("mention_count") or 0),
        "sov_share": float((aggregate or {}).get("sov_share") or 0.0),
        "avg_rank": (aggregate or {}).get("avg_rank"),
        "rank_distribution": dict((aggregate or {}).get("rank_distribution") or {}),
        "price_mention_count": int((aggregate or {}).get("price_mention_count") or 0),
        "price_accuracy_rate": (aggregate or {}).get("price_accuracy_rate"),
    }


def _snapshot_entry_id(snapshot: ProductMetricSnapshot) -> str:
    """Frozen catalog entry id for a snapshot.

    The live FK is SET NULL when the catalog row is deleted, so fall back to
    the frozen ``entry_id`` persisted in ``metrics`` at finalize time.
    """
    live = snapshot.product_id or snapshot.competitor_product_id
    if live is not None:
        return str(live)
    return str((snapshot.metrics or {}).get("entry_id") or "")


async def _latest_product_audit_id(
    session: AsyncSession, *, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> uuid.UUID | None:
    """Latest dashboard-eligible audit having >=1 product snapshot."""
    has_snapshots = (
        select(ProductMetricSnapshot.id)
        .where(ProductMetricSnapshot.audit_id == Audit.id)
        .exists()
    )
    return await session.scalar(
        select(Audit.id)
        .where(
            Audit.workspace_id == workspace_id,
            Audit.project_id == project_id,
            Audit.status.in_(_DASHBOARD_STATUSES),
            has_snapshots,
        )
        .order_by(Audit.completed_at.desc().nullslast(), Audit.created_at.desc())
        .limit(1)
    )
