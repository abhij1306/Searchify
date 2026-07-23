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
# A5 scope adds the referral chain's persisted rows (llm-analytics.md
# section 3): ``ReferralEvent`` — the immutable, sanitized-at-rest ingest
# artifact projected from ``IntegrationMetricRow`` (written once, deduped by
# ``(import_id, content_hash)``); ``ReferralClassification`` — the derived,
# provenance-stamped deterministic classification (exactly one per event);
# and ``AnalyticsSnapshot`` — the rebuildable projection snapshot for a
# ``(project, window, granularity)``.
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

# ``ANALYZER_VERSION`` / ``SCORING_RULE_VERSION`` are OWNED by
# config/analysis.py and reused here for the analytics provenance stamps
# (llm-analytics.md section 8, invariant 2) — never the same-named constant
# in config/site_health.py.
from app.core.config.analysis import ANALYZER_VERSION, SCORING_RULE_VERSION
from app.core.config.analytics import (
    AI_REFERRAL_RULE_VERSION,
    AI_SOURCE_OTHER,
    ANALYTICS_TASK_KIND_INGEST_REFERRALS,
    REFERRAL_SANITIZE_VERSION,
    analytics_settings,
)
from app.core.config.task_queue import TASK_STATUS_QUEUED
from app.core.database import Base

# FK target references + ondelete actions as named constants (site_health /
# integrations pattern): a typo in a ``table.column`` reference would
# otherwise silently bind the wrong parent.
_FK_WORKSPACE = "workspaces.id"
_FK_PROJECT = "projects.id"
_FK_IMPORT_ARTIFACT = "integration_import_artifacts.id"
_FK_METRIC_ROW = "integration_metric_rows.id"
_FK_REFERRAL_EVENT = "referral_events.id"
_ON_DELETE_CASCADE = "CASCADE"
_ON_DELETE_SET_NULL = "SET NULL"


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


class ReferralEvent(Base):
    """One immutable, sanitized-at-rest referral ingest artifact.

    Projected from a latest-``resync_seq`` ``IntegrationMetricRow`` by the
    ``ingest_referrals`` executor (A5) — a pure projection, never a provider
    fetch (invariant 7). Written ONCE and never mutated (invariant 3): the
    unique ``(import_id, content_hash)`` backs the executor's
    ``INSERT ... ON CONFLICT DO NOTHING`` so a re-run is a dedup no-op, never
    an overwrite. The deterministic redaction pass runs BEFORE the write, so
    persisted columns carry no PII (allowlisted ``raw``, fragment/userinfo/
    non-marketing params stripped from URLs, UA reduced to a family token,
    session only as the salted ``session_id_hash`` — invariant 6, stamped
    with ``sanitize_version``).

    Provenance (invariant 4): ``import_id`` is the immutable
    ``IntegrationImportArtifact`` whose rows produced the event (deleting the
    import batch deletes its events) and ``source_metric_row_id`` the exact
    derived fact row (optional join — survives the metric row's deletion as
    NULL). ``content_hash`` is the deterministic dedupe key over the event's
    sanitized signals.
    """

    __tablename__ = "referral_events"
    __table_args__ = (
        # Re-ingesting the same artifact never double-inserts the same event.
        UniqueConstraint(
            "import_id", "content_hash", name="uq_referral_event_import_content"
        ),
        # Backs the composite (workspace_id, event_id) FK on classifications.
        UniqueConstraint("workspace_id", "id", name="uq_referral_events_ws_id"),
        # Same-workspace import-artifact parent (composite FK, invariant 5);
        # deleting the source ingest batch deletes its events.
        ForeignKeyConstraint(
            ["workspace_id", "import_id"],
            ["integration_import_artifacts.workspace_id", _FK_IMPORT_ARTIFACT],
            ondelete=_ON_DELETE_CASCADE,
            name="fk_referral_event_import_scoped",
        ),
        # Drill-down / snapshot scans by project + occurrence time.
        Index("ix_referral_events_project_occurred", "project_id", "occurred_at"),
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
    # gsc | ga4 | server_log — the ingest channel that produced the event.
    source: Mapped[str] = mapped_column(String(16))
    # The immutable import batch (provenance, invariant 4).
    import_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    # The exact IntegrationMetricRow the event was projected from (optional
    # join: the metric row's deletion must not delete the event).
    source_metric_row_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_METRIC_ROW, ondelete=_ON_DELETE_SET_NULL),
        nullable=True,
    )
    # Provider data is date-grained: the UTC instant of the row's date.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Sanitized landing/referrer URLs (fragments, embedded credentials and
    # non-allowlisted query params stripped before the write).
    landing_url: Mapped[str] = mapped_column(String(2048), default="")
    referrer_host: Mapped[str] = mapped_column(String(512), default="")
    referrer_url: Mapped[str] = mapped_column(String(2048), default="")
    utm_source: Mapped[str] = mapped_column(String(512), default="")
    utm_medium: Mapped[str] = mapped_column(String(512), default="")
    utm_campaign: Mapped[str] = mapped_column(String(512), default="")
    # UA family token only — full fingerprintable UA strings never persist.
    user_agent: Mapped[str] = mapped_column(String(128), default="")
    # Opaque salted HMAC token — the ONLY persisted session marker.
    session_id_hash: Mapped[str] = mapped_column(String(64), default="")
    # The allowlisted, redacted source payload for traceability (never the
    # verbatim source row).
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Deterministic dedupe key over the sanitized signals (sha256 hex).
    content_hash: Mapped[str] = mapped_column(String(64))
    # Version of the redaction pass that produced the row (invariant 4).
    sanitize_version: Mapped[str] = mapped_column(
        String(64), default=REFERRAL_SANITIZE_VERSION
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class ReferralClassification(Base):
    """The derived, deterministic classification of one ``ReferralEvent``.

    Exactly ONE classification per event (single writer — the
    ``classify_referrals`` executor, A6 — backed by the unique constraint +
    ``ON CONFLICT DO NOTHING``; re-running never mutates, invariant 3).
    Provenance (invariant 4): ``referral_event_id`` is the immutable source
    and ``rule_version`` + ``analyzer_version`` trace the row to the exact
    config rule table + analysis code that produced it. Deterministic rules
    only — no LLM (invariant 9). Unmatched events record
    ``is_ai_referral=false, ai_source=other`` with empty match fields — the
    classifier never guesses a source.
    """

    __tablename__ = "referral_classifications"
    __table_args__ = (
        # One classification per referral event.
        UniqueConstraint(
            "referral_event_id", name="uq_referral_classification_event"
        ),
        # Same-workspace event parent (composite FK, invariant 5); event
        # deletion (retention sweep / import-batch delete) removes its
        # classification.
        ForeignKeyConstraint(
            ["workspace_id", "referral_event_id"],
            ["referral_events.workspace_id", _FK_REFERRAL_EVENT],
            ondelete=_ON_DELETE_CASCADE,
            name="fk_referral_classification_event_scoped",
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
    referral_event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), index=True
    )
    is_ai_referral: Mapped[bool] = mapped_column(Boolean, default=False)
    # chatgpt | gemini | claude | perplexity | copilot |
    # google_ai_overview | other (AI_SOURCES).
    ai_source: Mapped[str] = mapped_column(String(32), default=AI_SOURCE_OTHER)
    # The audited logical engine join key when the source maps to one
    # (invariant 10); null for sources outside the audited three.
    logical_engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The config rule that fired ("" when unmatched).
    matched_rule_id: Mapped[str] = mapped_column(String(64), default="")
    # referrer | utm | user_agent ("" when unmatched).
    match_signal: Mapped[str] = mapped_column(String(16), default="")
    # exact | heuristic ("" when unmatched).
    confidence: Mapped[str] = mapped_column(String(16), default="")
    # Version stamps (invariant 4): the rule table + analyzer that produced
    # the row.
    rule_version: Mapped[str] = mapped_column(
        String(64), default=AI_REFERRAL_RULE_VERSION
    )
    analyzer_version: Mapped[str] = mapped_column(
        String(64), default=ANALYZER_VERSION
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class AnalyticsSnapshot(Base):
    """The LLM-Analytics projection for one (project, window, granularity).

    Exactly ONE current snapshot per tuple — the unique constraint backs the
    refresh job's transactional upsert (A8). Computed from persisted
    ``ReferralClassification`` + ``MetricSnapshot`` rows only and rebuildable
    at any time from them; holds nothing not traceable to that evidence
    (invariants 4 + 7). Provenance ids stay JSONB arrays (no cross-subsystem
    FK compile dependency), and the analyzer/formula version stamps reuse the
    config/analysis.py constants (llm-analytics.md section 8, invariant 2).
    """

    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        # One current snapshot per (project, window, granularity).
        UniqueConstraint(
            "project_id",
            "window_start",
            "window_end",
            "granularity",
            name="uq_analytics_snapshot_window",
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
    # The projected date window (provider data is date-grained).
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    # day | week | month (ANALYTICS_SNAPSHOT_GRANULARITIES).
    granularity: Mapped[str] = mapped_column(String(8))
    # Headline projection: AI-referral sessions by ai_source, referral
    # share, visibility series, theme rollup, correlation summary.
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Provenance (invariant 4): the ReferralClassification ids folded in and
    # the MetricSnapshot ids folded in (JSONB id arrays).
    source_classification_ids: Mapped[list | None] = mapped_column(
        JSONB, nullable=True
    )
    source_snapshot_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # Version stamps (invariant 4) — reused from config/analysis.py.
    analyzer_version: Mapped[str] = mapped_column(
        String(64), default=ANALYZER_VERSION
    )
    formula_version: Mapped[str] = mapped_column(
        String(64), default=SCORING_RULE_VERSION
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
