# LLM Analytics persistence models (UUID PKs, workspace-scoped).
#
# A3 scope: ``AnalyticsTask`` — the queue+lease row driving every analytics
# projection job (referral ingest/classification, the traffic + analytics
# snapshot refreshes, and the referral retention sweep). It reuses the exact
# queue-row column contract of ``SiteCrawlTask`` (status / priority /
# randomized_position / available_at / lease_owner / lease_expires_at /
# heartbeat_at / attempt_count / max_attempts / idempotency_key / error_code /
# error_detail / created_at / updated_at / completed_at) so the single
# generic ``PostgresTaskQueue`` claims/leases/heartbeats/sweeps it unchanged
# (invariant 8). Double-claim is prevented by ``FOR UPDATE SKIP LOCKED`` plus
# the unique ``idempotency_key``.
#
# ``ReferralEvent`` / ``ReferralClassification`` / ``AnalyticsSnapshot`` land
# in A5 — do NOT add them here ahead of that task.
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config.analytics import (
    ANALYTICS_TASK_KIND_INGEST_REFERRALS,
    analytics_settings,
)
from app.core.config.task_queue import TASK_STATUS_QUEUED
from app.core.database import Base

# FK target references + ondelete actions as named constants (site_health /
# integrations pattern): a typo in a ``table.column`` reference would
# otherwise silently bind the wrong parent.
_FK_WORKSPACE = "workspaces.id"
_FK_PROJECT = "projects.id"
_ON_DELETE_CASCADE = "CASCADE"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AnalyticsTask(Base):
    """One queue+lease row for one analytics projection job.

    Carries the exact queue-row column contract of ``SiteCrawlTask`` so the
    one generic ``PostgresTaskQueue`` serves it unchanged (invariant 8),
    parameterized by ``ANALYTICS_QUEUE_SPEC``. The kind-specific frozen
    inputs live in ``payload`` (small, credential-free JSONB — e.g.
    ``{"import_artifact_id": ...}`` for the referral chain, a date window for
    the snapshot refreshes). ``project_id`` is nullable because the
    workspace-scoped ``referral_retention_sweep`` has no single project.
    """

    __tablename__ = "analytics_tasks"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_analytics_task_idempotency_key"),
        # Claimable-task index (queue claim path).
        Index("ix_analytics_tasks_claim", "status", "available_at"),
        # Expired-lease sweeper index.
        Index("ix_analytics_tasks_lease", "status", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_WORKSPACE, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    # Nullable: the referral retention sweep is workspace-scoped.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_PROJECT, ondelete=_ON_DELETE_CASCADE),
        nullable=True,
        index=True,
    )
    # ingest_referrals | classify_referrals | traffic_snapshot_refresh |
    # analytics_snapshot_refresh | referral_retention_sweep
    # (ANALYTICS_TASK_KINDS).
    task_kind: Mapped[str] = mapped_column(
        String(32), default=ANALYTICS_TASK_KIND_INGEST_REFERRALS
    )
    # Kind-specific frozen inputs (small, credential-free).
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))

    # --- Queue + lease state (identical contract to SiteCrawlTask) --------
    status: Mapped[str] = mapped_column(
        String(24), default=TASK_STATUS_QUEUED, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    randomized_position: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=analytics_settings.task_max_attempts
    )
    error_code: Mapped[str] = mapped_column(String(32), default="")
    error_detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
