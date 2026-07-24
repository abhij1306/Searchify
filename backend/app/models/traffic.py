# Traffic projection persistence models (UUID PKs, workspace-scoped).
#
# The Traffic surface (docs/roadmap/traffic.md section 3) is a pure
# PROJECTION over the integrations-owned ``IntegrationMetricRow`` fact rows
# (invariant 7): it owns NO import/metric tables and performs NO provider
# fetch (invariant 2). ``TrafficSnapshot`` is the headline projection for a
# ``(project, window, granularity)`` — exactly one current row per tuple,
# rewritten by the refresh job as a transactional upsert. ``TrafficPageStat``
# / ``TrafficQueryStat`` are the persisted per-page / per-query projection
# rows so the paged endpoints sort/page against stored aggregates instead of
# recomputing at read time.
#
# Provenance on every row (invariant 4): ``source_metric_row_ids`` +
# ``source_artifact_ids`` trace the projection back to the
# ``IntegrationMetricRow``s aggregated and their upstream immutable
# artifacts, and the snapshot stamps the formula/normalization versions.
# Provenance ids stay JSONB arrays — deliberately NOT foreign keys, so this
# model module has no cross-workstream FK compile dependency on the
# integrations tables.
#
# Same-workspace integrity between the NEW tables is enforced by the
# composite-FK pattern (execution notes / site_health precedent): a stat
# row's ``(workspace_id, snapshot_id)`` must reference a snapshot in the SAME
# workspace (invariant 5). References to pre-existing tables (``projects``,
# ``site_urls``) stay plain FKs.
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config.traffic import (
    TRAFFIC_FORMULA_VERSION,
    TRAFFIC_NORMALIZATION_VERSION,
)
from app.core.database import Base

# FK target references + ondelete actions as named constants (site_health /
# integrations pattern): a typo in a ``table.column`` reference would
# otherwise silently bind the wrong parent.
_FK_WORKSPACE = "workspaces.id"
_FK_PROJECT = "projects.id"
_FK_SNAPSHOT = "traffic_snapshots.id"
_FK_SITE_URL = "site_urls.id"
_ON_DELETE_CASCADE = "CASCADE"
_ON_DELETE_SET_NULL = "SET NULL"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TrafficSnapshot(Base):
    """The headline Traffic projection for one (project, window, granularity).

    Exactly ONE current snapshot per ``(project_id, window_start,
    window_end, granularity)`` — the unique constraint backs the refresh
    job's transactional upsert (``INSERT ... ON CONFLICT DO UPDATE``) so
    concurrent refreshes can never create duplicate "current" rows
    (traffic.md section 3). Rebuildable at any time from the persisted
    ``IntegrationMetricRow`` rows; holds nothing not traceable to them
    (invariants 4 + 7).
    """

    __tablename__ = "traffic_snapshots"
    __table_args__ = (
        # One current snapshot per (project, window, granularity).
        UniqueConstraint(
            "project_id",
            "window_start",
            "window_end",
            "granularity",
            name="uq_traffic_snapshot_window",
        ),
        # Backs the composite (workspace_id, snapshot_id) FK on the stat
        # tables (same-workspace enforcement, invariant 5).
        UniqueConstraint("workspace_id", "id", name="uq_traffic_snapshots_ws_id"),
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
    # The projected date window (provider data is date-grained).
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    # day | week | month (TRAFFIC_SNAPSHOT_GRANULARITIES).
    granularity: Mapped[str] = mapped_column(String(8))
    # Headline projection: totals, CTR/position distributions, trend series.
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Provenance (invariant 4): the IntegrationMetricRow ids aggregated and
    # their upstream immutable IntegrationImportArtifact ids (JSONB id
    # arrays — no cross-workstream FK compile dependency).
    source_metric_row_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_artifact_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Version stamps (invariant 4) — kept separate so a normalization change
    # is distinguishable from a formula change (traffic.md section 8).
    formula_version: Mapped[str] = mapped_column(
        String(64), default=TRAFFIC_FORMULA_VERSION
    )
    normalization_version: Mapped[str] = mapped_column(
        String(64), default=TRAFFIC_NORMALIZATION_VERSION
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class TrafficPageStat(Base):
    """One persisted per-page projection row of a ``TrafficSnapshot``.

    Lets the ``/traffic/pages`` endpoint page and sort against stored
    aggregates — no read-time recomputation from ``IntegrationMetricRow``
    (invariant 7). ``site_url_id`` is the OPTIONAL join to the crawled
    ``SiteUrl`` identity: unmatched pages resolve to null and are still
    valid measured pages (traffic.md section 5), so the FK is ``SET NULL``.
    Written by the snapshot-refresh job in the same transaction as the
    parent snapshot; rebuildable from the persisted metric rows.
    """

    __tablename__ = "traffic_page_stats"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "canonical_url", name="uq_traffic_page_stat_url"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "snapshot_id"],
            ["traffic_snapshots.workspace_id", _FK_SNAPSHOT],
            ondelete=_ON_DELETE_CASCADE,
            name="fk_traffic_page_stat_snapshot_scoped",
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
    snapshot_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    # Optional join to the crawled SiteUrl identity (unmatched -> null).
    site_url_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_SITE_URL, ondelete=_ON_DELETE_SET_NULL),
        nullable=True,
    )
    # The canonicalized page URL key (canonical_identity at projection time).
    canonical_url: Mapped[str] = mapped_column(String(2048))
    # Aggregated page metrics: impressions/clicks/ctr/position (GSC) +
    # sessions/conversions (GA4).
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Provenance to raw evidence (invariant 4), JSONB id arrays.
    source_metric_row_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_artifact_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class TrafficQueryStat(Base):
    """One persisted per-query projection row of a ``TrafficSnapshot``.

    Same projection contract as ``TrafficPageStat``: the ``/traffic/queries``
    endpoint pages/sorts these stored aggregates (invariant 7). The key is
    the normalized query string (NFKC / casefold / whitespace collapse at
    projection time).
    """

    __tablename__ = "traffic_query_stats"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "normalized_query", name="uq_traffic_query_stat_query"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "snapshot_id"],
            ["traffic_snapshots.workspace_id", _FK_SNAPSHOT],
            ondelete=_ON_DELETE_CASCADE,
            name="fk_traffic_query_stat_snapshot_scoped",
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
    snapshot_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    # The normalized GSC query string key.
    normalized_query: Mapped[str] = mapped_column(String(1024))
    # Aggregated query metrics: impressions/clicks/ctr/position (GSC).
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Provenance to raw evidence (invariant 4), JSONB id arrays.
    source_metric_row_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_artifact_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
