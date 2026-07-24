# Product-catalog persistence models (Agentic Commerce / Product Visibility).
#
# One surface's catalog + derived rows live in this module (convention:
# ``models/site_health.py``). ``Product`` mirrors ``Competitor``'s shape
# (``models/brand.py``): a first-class row with JSONB value-object arrays
# (``aliases``, ``variants``). ``CompetitorProduct`` is a separate table
# FK -> ``competitors.id`` (mirrors the Brand-vs-Competitor separation;
# competitor rows need no attribute completeness).
#
# Everything is scoped to a ``Project`` (itself workspace-scoped), so access is
# enforced through the project's workspace (invariant 5). The catalog is frozen
# into every audit's ``configuration`` at creation (``domain/products/shim.py``)
# so re-scoring is deterministic (invariant 9).
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config.products import DEFAULT_PRODUCT_ORIGIN
from app.core.database import Base
from app.models.constants import (
    CASCADE_ALL_DELETE_ORPHAN,
    FK_AUDITS_ID,
    ON_DELETE_SET_NULL,
)


class Product(Base):
    """One own-catalog SKU tracked for product visibility.

    ``aliases`` and ``variants`` are JSONB value-object arrays consumed
    wholesale by the deterministic product scorer (variant names/SKUs fold
    into the matching alias set). ``price`` is nullable — a product without a
    catalog price still scores mentions/rank; price accuracy is then null.
    """

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("project_id", "sku", name="uq_product_project_sku"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    sku: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    # Value-object array: [{"name": str, "sku": str, "price": float|null}, ...]
    variants: Mapped[list] = mapped_column(JSONB, default=list)
    price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    # Free-form attribute bag (brand/category/gtin/availability/...). The
    # deterministic completeness matrix reads config-owned keys from it.
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    # manual | imported (config/products.py PRODUCT_ORIGIN_*).
    origin: Mapped[str] = mapped_column(String(32), default=DEFAULT_PRODUCT_ORIGIN)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    project: Mapped[Project] = relationship("Project", back_populates="products")


class CompetitorProduct(Base):
    """One competitor product tracked for product share-of-voice.

    Separate from ``Product`` (mirrors Brand-vs-Competitor): competitor rows
    carry no variants/attributes completeness — just identity + price.
    """

    __tablename__ = "competitor_products"
    __table_args__ = (
        UniqueConstraint(
            "competitor_id", "name", name="uq_competitor_product_competitor_name"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    competitor_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("competitors.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255))
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    project: Mapped[Project] = relationship(
        "Project", back_populates="competitor_products"
    )
    competitor: Mapped[Competitor] = relationship(
        "Competitor", back_populates="products"
    )


class ProductResponseAnalysis(Base):
    """Deterministic per-execution PRODUCT analysis of one raw response.

    Sibling of ``ResponseAnalysis`` (``models/analysis.py`` B6): computed from
    the same immutable ``RawResponseArtifact`` by the product analyzer pass,
    stamped with ``product_analyzer_version`` + ``product_scoring_rule_version``
    (invariant 4). One row per execution (unique ``task_id``); its
    ``ProductMention`` children hang off it. Never touches the brand-level
    derived rows.
    """

    __tablename__ = "product_response_analyses"
    __table_args__ = (
        # Exactly one product analysis per execution (single deterministic writer).
        UniqueConstraint("task_id", name="uq_product_response_analysis_task"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(FK_AUDITS_ID, ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit_tasks.id", ondelete="CASCADE"),
        index=True,
    )
    # Provenance (invariant 4): SET NULL keeps the analysis if the artifact is
    # ever pruned.
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("raw_response_artifacts.id", ondelete=ON_DELETE_SET_NULL),
        nullable=True,
    )
    product_analyzer_version: Mapped[str] = mapped_column(String(32))
    product_scoring_rule_version: Mapped[str] = mapped_column(String(32))

    # Denormalized provenance triple + slot identity (invariant 10).
    logical_engine: Mapped[str] = mapped_column(String(32), default="")
    transport_provider: Mapped[str] = mapped_column(String(32), default="")
    transport_model: Mapped[str] = mapped_column(String(255), default="")
    prompt_index: Mapped[int] = mapped_column(Integer, default=0)
    repetition: Mapped[int] = mapped_column(Integer, default=0)

    # Flat headline signals (per-execution).
    own_product_mention_count: Mapped[int] = mapped_column(Integer, default=0)
    competitor_product_mention_count: Mapped[int] = mapped_column(
        Integer, default=0
    )
    products_with_price_match: Mapped[int] = mapped_column(Integer, default=0)

    # Full deterministic product score dict (source of truth for aggregation).
    score: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    product_mentions: Mapped[list[ProductMention]] = relationship(
        "ProductMention",
        back_populates="analysis",
        cascade=CASCADE_ALL_DELETE_ORPHAN,
        passive_deletes=True,
    )


class ProductMention(Base):
    """One recorded product mention in a response (invariant 4).

    Exactly one of ``product_id`` / ``competitor_product_id`` is set at write
    time (single deterministic writer). Both FKs are SET NULL and the matched
    identity is snapshotted onto the row (``matched_name``/``matched_sku``) so
    evidence survives catalog deletes — mirrors nullable
    ``AuditPromptSnapshot.prompt_id``. No exactly-one-target CHECK constraint:
    it would reject the SET NULL a catalog delete legitimately triggers.
    """

    __tablename__ = "product_mentions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(FK_AUDITS_ID, ondelete="CASCADE"),
        index=True,
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("product_response_analyses.id", ondelete="CASCADE"),
        index=True,
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("raw_response_artifacts.id", ondelete=ON_DELETE_SET_NULL),
        nullable=True,
    )
    product_analyzer_version: Mapped[str] = mapped_column(String(32))
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete=ON_DELETE_SET_NULL),
        nullable=True,
    )
    competitor_product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("competitor_products.id", ondelete=ON_DELETE_SET_NULL),
        nullable=True,
    )
    # Snapshotted identity (survives catalog deletes).
    matched_name: Mapped[str] = mapped_column(String(255), default="")
    matched_sku: Mapped[str] = mapped_column(String(128), default="")
    first_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Price extraction evidence (empty/absent when no price was detected).
    price_text: Mapped[str] = mapped_column(String(64), default="")
    price_value: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_currency: Mapped[str] = mapped_column(String(3), default="")
    # null = not verifiable (no catalog price / currency mismatch).
    price_matches_catalog: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    analysis: Mapped[ProductResponseAnalysis] = relationship(
        "ProductResponseAnalysis", back_populates="product_mentions"
    )


class ProductMetricSnapshot(Base):
    """Per-(audit, product) aggregate product-visibility projection.

    One row per (audit, product) and per (audit, competitor_product) —
    enforced by two partial unique indexes (functional/unique ``Index``
    convention exists on ``Topic``). Computed once at finalize from persisted
    ``ProductResponseAnalysis`` rows only (invariant 7), stamped with the
    analyzer/rule versions + the exact evidence set (invariant 4).
    """

    __tablename__ = "product_metric_snapshots"
    __table_args__ = (
        Index(
            "uq_product_metric_snapshot_product",
            "audit_id",
            "product_id",
            unique=True,
            postgresql_where=text("product_id IS NOT NULL"),
        ),
        Index(
            "uq_product_metric_snapshot_competitor_product",
            "audit_id",
            "competitor_product_id",
            unique=True,
            postgresql_where=text("competitor_product_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(FK_AUDITS_ID, ondelete="CASCADE"),
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    # Exactly one set; SET NULL keeps the aggregate if the catalog row is
    # deleted (the ``metrics`` payload still carries the frozen identity).
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete=ON_DELETE_SET_NULL),
        nullable=True,
    )
    competitor_product_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("competitor_products.id", ondelete=ON_DELETE_SET_NULL),
        nullable=True,
    )
    product_analyzer_version: Mapped[str] = mapped_column(String(32), default="")
    product_scoring_rule_version: Mapped[str] = mapped_column(
        String(32), default=""
    )
    mention_count: Mapped[int] = mapped_column(Integer, default=0)
    sov_share: Mapped[float] = mapped_column(default=0.0)
    avg_rank: Mapped[float | None] = mapped_column(nullable=True)
    rank_distribution: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    price_mention_count: Mapped[int] = mapped_column(Integer, default=0)
    price_accuracy_rate: Mapped[float | None] = mapped_column(nullable=True)
    # Full aggregate dict (per-engine breakdown, price match counts, ...).
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Provenance (invariant 4): the exact evidence set aggregated.
    source_analysis_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_artifact_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


from app.models.brand import Competitor  # noqa: E402
from app.models.project import Project  # noqa: E402
