# Opportunities persistence models (UUID PKs, workspace-scoped — invariant 5).
#
# Derived rows projected deterministically from already-persisted visibility
# analysis + Site Health issue rows (invariants 7 + 9). Every row carries
# provenance: the validated catalog ``rule_id`` + ``analyzer_version`` /
# ``rule_version`` / ``formula_version`` + the source row id lists it was
# computed from (invariant 4).
#
# Supersede-not-mutate (invariant 3): a recompute never edits evidence, score,
# or provenance on an existing row. A fresh hit for the same
# ``(rule_id, target_key)`` inserts a NEW identity and closes the prior live
# row (``superseded_by_id`` + ``superseded_at``); a live row whose evidence no
# longer fires is closed with no successor. The human workflow ``status`` is
# the ONLY mutable field. ``OpportunitySnapshot`` is immutable per run — a
# re-run inserts a new snapshot identity, never an overwrite.
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config.opportunities import STATUS_OPEN
from app.core.database import Base
from app.models.constants import FK_AUDITS_ID, ON_DELETE_SET_NULL

_FK_WORKSPACE = "workspaces.id"
_FK_PROJECT = "projects.id"
_FK_PROMPT = "prompts.id"
_FK_SITE_CRAWL = "site_crawls.id"
_FK_OPPORTUNITY = "opportunities.id"
_ON_DELETE_CASCADE = "CASCADE"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Opportunity(Base):
    """One derived opportunity instance (a rule hit on a concrete target).

    One LIVE row per ``(project_id, rule_id, target_key)`` — DB-enforced by a
    partial unique index over rows where ``superseded_at IS NULL``. ``rule_id``
    is a validated config-catalog string (never a DB FK — the catalog is code,
    not a table). ``title`` + ``remediation`` are snapshotted from the catalog
    at write time so a later relabel never rewrites history (mirrors
    ``SiteIssue.remediation`` semantics). Evidence/score/provenance are
    written once and never mutated; superseded rows keep their bytes intact.
    """

    __tablename__ = "opportunities"
    __table_args__ = (
        # One live row per (rule, target) per project (D4).
        Index(
            "uq_opportunities_live_target",
            "project_id",
            "rule_id",
            "target_key",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        # Filter index: triage queue + chip filters.
        Index(
            "ix_opportunities_filter",
            "project_id",
            "status",
            "severity",
            "opportunity_type",
        ),
        # List index: priority-sorted keyset order.
        Index(
            "ix_opportunities_list",
            "project_id",
            "priority_score",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_WORKSPACE, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_PROJECT, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    # Validated against the config catalog at write time (never a FK).
    rule_id: Mapped[str] = mapped_column(String(64))
    opportunity_type: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(16))
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)
    # Catalog title/remediation snapshotted at write time.
    title: Mapped[str] = mapped_column(String(255), default="")
    remediation: Mapped[str] = mapped_column(Text, default="")
    # Deterministic target identity (D4): ``prompt:{prompt_id}`` /
    # ``prompt-index:{audit_id}:{prompt_index}`` fallback / ``url:{url}``.
    target_key: Mapped[str] = mapped_column(String(512))
    target_prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_PROMPT, ondelete=ON_DELETE_SET_NULL),
        nullable=True,
    )
    target_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_theme: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The concrete offending values + context (written once, never mutated).
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Provenance (invariant 4): the source rows this was computed from.
    source_analysis_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_issue_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_metric_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_traffic_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    analyzer_version: Mapped[str] = mapped_column(String(32), default="")
    rule_version: Mapped[str] = mapped_column(String(32), default="")
    formula_version: Mapped[str] = mapped_column(String(32), default="")
    # Human workflow status — the ONLY mutable field.
    status: Mapped[str] = mapped_column(String(16), default=STATUS_OPEN)
    # Supersede bookkeeping (system-owned; never touches evidence).
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_OPPORTUNITY, ondelete=ON_DELETE_SET_NULL),
        nullable=True,
    )
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class OpportunitySnapshot(Base):
    """Immutable per-recompute aggregate projection (mirrors MetricSnapshot).

    One row per recompute run (``run_id`` is the run identity). Records the
    resolved source identities (audit / site crawl), the counts by
    type/severity/status over the new live set, the total + median priority,
    the aggregated source row ids, and the analyzer/rule/formula versions.
    Never mutated after insert (invariant 3).
    """

    __tablename__ = "opportunity_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_opportunity_snapshot_run"),
        Index(
            "ix_opportunity_snapshots_project_created",
            "project_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_WORKSPACE, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_PROJECT, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    # Per-recompute run identity.
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), default=uuid.uuid4
    )
    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(FK_AUDITS_ID, ondelete=ON_DELETE_SET_NULL),
        nullable=True,
    )
    site_crawl_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_SITE_CRAWL, ondelete=ON_DELETE_SET_NULL),
        nullable=True,
    )
    counts_by_type: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    counts_by_severity: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    counts_by_status: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    median_priority: Mapped[float | None] = mapped_column(Float, nullable=True)
    analyzer_version: Mapped[str] = mapped_column(String(32), default="")
    rule_version: Mapped[str] = mapped_column(String(32), default="")
    formula_version: Mapped[str] = mapped_column(String(32), default="")
    # Provenance (invariant 4): the evidence set this run read.
    source_analysis_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_issue_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
