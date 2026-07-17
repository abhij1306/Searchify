# Deterministic-analysis persistence models (B6, UUID PKs, workspace-scoped).
#
# When an execution completes, the worker/analysis stage scores the immutable
# ``RawResponseArtifact`` and persists the derived rows here: one
# ``ResponseAnalysis`` per execution, plus its ``BrandMention`` /
# ``CompetitorMention`` / ``Citation`` child rows. At finalize a single
# ``MetricSnapshot`` per audit aggregates them.
#
# Every derived row carries provenance (invariant 4): the ``artifact_id`` of the
# ``RawResponseArtifact`` it was computed from AND the ``analyzer_version``
# (+ ``scoring_rule_version`` where a formula applies). A derived row with no
# traceable source + version is invalid. Everything is workspace-scoped
# (invariant 5) and read-only for reports/metrics (invariant 7 — projections
# never re-call providers).
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ResponseAnalysis(Base):
    """Deterministic per-execution analysis of one raw response (invariant 4).

    Computed from the immutable ``RawResponseArtifact`` (referenced by
    ``artifact_id``) and stamped with the ``analyzer_version`` +
    ``scoring_rule_version`` that produced it. Carries the flat headline signals
    (brand mention, owned/unintended citation, prompt class, search fanout) plus
    the full ``score`` dict; the mention/citation child rows hang off it.

    Sentiment + average position are deliberately NOT computed at MVP (decision
    B-2, invariant 9 — no LLM for headline metrics): the columns are present and
    nullable so the projection shape is stable until the roadmap fills them.
    """

    __tablename__ = "response_analyses"
    __table_args__ = (
        # Exactly one analysis per execution (single deterministic writer).
        UniqueConstraint("task_id", name="uq_response_analysis_task"),
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
        ForeignKey("audits.id", ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit_tasks.id", ondelete="CASCADE"),
        index=True,
    )
    # Provenance: the immutable raw artifact this analysis was computed from
    # (invariant 4). SET NULL keeps the analysis if the artifact is ever pruned.
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("raw_response_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    analyzer_version: Mapped[str] = mapped_column(String(32))
    scoring_rule_version: Mapped[str] = mapped_column(String(32))

    # Denormalized provenance triple + slot identity (invariant 10) for reports.
    logical_engine: Mapped[str] = mapped_column(String(32), default="")
    transport_provider: Mapped[str] = mapped_column(String(32), default="")
    transport_model: Mapped[str] = mapped_column(String(255), default="")
    prompt_index: Mapped[int] = mapped_column(Integer, default=0)
    repetition: Mapped[int] = mapped_column(Integer, default=0)
    prompt_class: Mapped[str] = mapped_column(String(32), default="")

    # Flat headline signals (ported reference metrics, per-execution).
    brand_mentioned: Mapped[bool] = mapped_column(Boolean, default=False)
    brand_first_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owned_domain_cited: Mapped[bool] = mapped_column(Boolean, default=False)
    owned_citation_count: Mapped[int] = mapped_column(Integer, default=0)
    unintended_domain_cited: Mapped[bool] = mapped_column(Boolean, default=False)
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    search_used: Mapped[bool] = mapped_column(Boolean, default=False)
    search_query_count: Mapped[int] = mapped_column(Integer, default=0)

    # Roadmap (B-2): nullable/absent until an LLM stage is added.
    sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    avg_position: Mapped[float | None] = mapped_column(nullable=True)

    # Full deterministic score dict (the source of truth for aggregation).
    score: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    brand_mentions: Mapped[list[BrandMention]] = relationship(
        "BrandMention",
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    competitor_mentions: Mapped[list[CompetitorMention]] = relationship(
        "CompetitorMention",
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    citations: Mapped[list[Citation]] = relationship(
        "Citation",
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class BrandMention(Base):
    """One recorded brand mention in a response (invariant 4).

    Emitted when the deterministic scorer matches a brand alias in the answer
    text. Carries the matched offset + the raw-artifact provenance so every
    mention traces to evidence.
    """

    __tablename__ = "brand_mentions"

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
        ForeignKey("audits.id", ondelete="CASCADE"),
        index=True,
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("response_analyses.id", ondelete="CASCADE"),
        index=True,
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("raw_response_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    analyzer_version: Mapped[str] = mapped_column(String(32))
    brand_name: Mapped[str] = mapped_column(String(255), default="")
    first_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    analysis: Mapped[ResponseAnalysis] = relationship(
        "ResponseAnalysis", back_populates="brand_mentions"
    )


class CompetitorMention(Base):
    """One recorded competitor mention in a response (invariant 4)."""

    __tablename__ = "competitor_mentions"

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
        ForeignKey("audits.id", ondelete="CASCADE"),
        index=True,
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("response_analyses.id", ondelete="CASCADE"),
        index=True,
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("raw_response_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    analyzer_version: Mapped[str] = mapped_column(String(32))
    competitor_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    analysis: Mapped[ResponseAnalysis] = relationship(
        "ResponseAnalysis", back_populates="competitor_mentions"
    )


class Citation(Base):
    """One classified source citation from a response (invariant 4).

    Classification is deterministic (owned / unintended / competitor /
    third-party) from the resolved publisher domain. Records the raw-artifact
    provenance + analyzer version so every classification traces to evidence.
    """

    __tablename__ = "citations"

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
        ForeignKey("audits.id", ondelete="CASCADE"),
        index=True,
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("response_analyses.id", ondelete="CASCADE"),
        index=True,
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("raw_response_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    analyzer_version: Mapped[str] = mapped_column(String(32))
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    url: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    domain: Mapped[str] = mapped_column(String(255), default="")
    # Deterministic classification (owned/unintended/competitor/third_party).
    classification: Mapped[str] = mapped_column(String(24), default="third_party")
    is_owned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_unintended: Mapped[bool] = mapped_column(Boolean, default=False)
    matched_competitor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    analysis: Mapped[ResponseAnalysis] = relationship(
        "ResponseAnalysis", back_populates="citations"
    )


class MetricSnapshot(Base):
    """Aggregate run-level metrics projection for one audit (invariants 4 + 7).

    Computed once at finalize from the persisted ``ResponseAnalysis`` rows (never
    from a provider call). Carries the full aggregate ``metrics`` dict plus the
    headline Visibility Score and stamped provenance versions. One snapshot per
    audit.
    """

    __tablename__ = "metric_snapshots"
    __table_args__ = (UniqueConstraint("audit_id", name="uq_metric_snapshot_audit"),)

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
        ForeignKey("audits.id", ondelete="CASCADE"),
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    analyzer_version: Mapped[str] = mapped_column(String(32))
    scoring_rule_version: Mapped[str] = mapped_column(String(32))
    total_completed: Mapped[int] = mapped_column(Integer, default=0)
    total_failed: Mapped[int] = mapped_column(Integer, default=0)
    # Headline Visibility Score (0-100), = brand_mention_rate * 100 (deterministic).
    visibility_score: Mapped[float] = mapped_column(default=0.0)
    # Full aggregate metrics dict (headline rates, SOV, per-prompt stability,
    # citation shares, per-engine, cost). The source of truth for projections.
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Provenance (invariant 4): the exact evidence set this aggregate was
    # computed from — the ``ResponseAnalysis`` ids and their source raw
    # ``artifact_id``s. Every derived row, including this aggregate, must be
    # traceable to the raw evidence + analyzer/rule versions.
    source_analysis_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_artifact_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
