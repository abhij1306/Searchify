# Site Health persistence graph (UUID PKs, workspace-scoped — invariant 5).
#
# The HTTP-level technical/AEO crawler's durable state. Everything here is
# UUID-keyed and scoped by ``workspace_id`` (directly on query-heavy/derived
# rows, or through the parent project/crawl). Immutable evidence
# (``SiteFetchArtifact`` / ``SiteFetchAttempt`` / ``SiteRuleEvaluation`` /
# ``SiteIssue`` / ``SiteHealthSnapshot`` / ``SiteUrlObservation`` /
# ``SiteCrawlEvent``) is append-only, written once by the claiming worker
# (invariant 3). Mutable projections (``SiteCrawl`` / ``SiteCrawlTask`` /
# ``SiteHealthProfile`` / ``SiteUrl`` / ``MonitoredSiteUrl``) are explicit
# state projections. There is NO raw HTML body column anywhere — only bounded,
# redacted, normalized facts (subplan Persistence contract).
#
# ``SiteCrawlTask`` reuses the exact queue-row column contract (status /
# lease_owner / lease_expires_at / heartbeat_at / attempt_count / max_attempts /
# available_at / idempotency_key / error_code / error_detail / completed_at /
# result_artifact_id) so the one generic ``PostgresTaskQueue`` serves it and
# ``AuditTask`` unchanged (invariant 8). It carries an integer ``generation``
# (default 0): initial work is generation 0; remove/re-add and explicit rerun
# allocate the next generation under lock so they always create a new
# task/artifact identity rather than colliding with a cancelled task.
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config.site_health import (
    ANALYSIS_STATUS_PENDING,
    CAPABILITY_FREE,
    CRAWL_STATUS_DRAFT,
    DISCOVERY_MODE_SAMPLE,
    DISCOVERY_STATUS_PENDING,
    FREE_MONITORED_URL_LIMIT,
    FREE_SAMPLE_URL_LIMIT,
    INITIAL_TASK_GENERATION,
    PAGE_ANALYSIS_STATUS_PENDING,
    SELECTION_SOURCE_USER,
    TASK_KIND_DISCOVER,
    site_health_settings,
)
from app.core.config.task_queue import TASK_STATUS_QUEUED
from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkspaceSiteHealthEntitlement(Base):
    """Workspace-level Site Health entitlement + the quota serialization lock.

    Exactly one row per workspace (unique ``workspace_id``). ``plan_key`` is a
    CAPABILITY key (``free`` / ``starter``), never a marketing display name.
    Freezes the capability's discovery mode, discovery cap, sample limit,
    monitored-URL limit, and the count-disclosure flag. This row is the row
    locked (``FOR UPDATE``) to serialize workspace-wide monitored-quota checks.
    No billing-provider fields (billing is out of scope).
    """

    __tablename__ = "workspace_site_health_entitlements"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", name="uq_ws_site_health_entitlement_workspace"
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
    # CAPABILITY key: "free" | "starter" (not a plan display name).
    plan_key: Mapped[str] = mapped_column(String(32), default=CAPABILITY_FREE)
    # Bumped when a workspace's capability profile changes.
    capability_revision: Mapped[int] = mapped_column(Integer, default=1)
    discovery_mode: Mapped[str] = mapped_column(
        String(16), default=DISCOVERY_MODE_SAMPLE
    )
    # Free caps discovery at the sample size; Starter has no hard cap (null).
    discovery_url_cap: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=FREE_SAMPLE_URL_LIMIT
    )
    sample_url_limit: Mapped[int] = mapped_column(
        Integer, default=FREE_SAMPLE_URL_LIMIT
    )
    monitored_url_limit: Mapped[int] = mapped_column(
        Integer, default=FREE_MONITORED_URL_LIMIT
    )
    # Whether total/frontier/overflow counts may be disclosed (Free = False).
    count_disclosure: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class SiteHealthProfile(Base):
    """Project-owned mutable Site Health configuration/projection (not evidence).

    One row per project (unique ``project_id``). Holds the canonical crawl root
    URL/host, the derived primary registrable domain, the narrowing include/
    exclude globs, and the monotonic ``selection_version`` used for optimistic
    monitored-set replacement.
    """

    __tablename__ = "site_health_profiles"
    __table_args__ = (
        UniqueConstraint(
            "project_id", name="uq_site_health_profile_project"
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
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    root_url: Mapped[str] = mapped_column(String(2048), default="")
    root_host: Mapped[str] = mapped_column(String(255), default="")
    registrable_domain: Mapped[str] = mapped_column(String(255), default="")
    include_globs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    exclude_globs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    selection_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class SiteCrawl(Base):
    """One crawl run with independent overall/discovery/analysis sub-states.

    Freezes the entitlement/config/rule/version snapshots into ``configuration``
    at creation so a live env change never alters an in-flight run (invariant
    9). Carries the deterministic seed, the sample flag, the visible admitted/
    discovered/analyzed/failed counters, and the latest score summary. It never
    stores or exposes a full-site total for a sample crawl.
    """

    __tablename__ = "site_crawls"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_health_profiles.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(24), default=CRAWL_STATUS_DRAFT, index=True
    )
    discovery_status: Mapped[str] = mapped_column(
        String(24), default=DISCOVERY_STATUS_PENDING
    )
    analysis_status: Mapped[str] = mapped_column(
        String(24), default=ANALYSIS_STATUS_PENDING
    )
    root_url: Mapped[str] = mapped_column(String(2048), default="")
    # 64-bit seed stored as text so the full unsigned range survives Postgres'
    # signed bigint and reproduces the deterministic frontier order.
    random_seed: Mapped[str] = mapped_column(String(32), default="")
    # Frozen entitlement/config/rule/version snapshot (never re-read live).
    configuration: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sample_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    # Visible counters (never a hidden full-site total for a sample crawl).
    admitted_url_count: Mapped[int] = mapped_column(Integer, default=0)
    discovered_url_count: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_url_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_url_count: Mapped[int] = mapped_column(Integer, default=0)
    inventory_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    score_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    extractor_version: Mapped[str] = mapped_column(String(32), default="")
    analyzer_version: Mapped[str] = mapped_column(String(32), default="")
    rule_catalog_version: Mapped[str] = mapped_column(String(32), default="")
    scoring_version: Mapped[str] = mapped_column(String(32), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tasks: Mapped[list[SiteCrawlTask]] = relationship(
        "SiteCrawlTask",
        back_populates="crawl",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    events: Mapped[list[SiteCrawlEvent]] = relationship(
        "SiteCrawlEvent",
        back_populates="crawl",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SiteCrawlEvent.created_at",
    )


class SiteUrl(Base):
    """Stable per-project URL identity (mutable lightweight discovery state).

    Unique ``(project_id, url_hash)``: one identity per normalized URL in a
    project across all crawls. Carries the normalized URL + hash, the display
    URL, first/last-seen crawl ids/timestamps, and the latest lightweight
    discovery status/title/content-type/depth/source. The keyset index
    ``(project_id, normalized_url, id)`` backs stable inventory cursors.
    """

    __tablename__ = "site_urls"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "url_hash", name="uq_site_url_project_hash"
        ),
        Index(
            "ix_site_urls_project_keyset",
            "project_id",
            "normalized_url",
            "id",
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
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    normalized_url: Mapped[str] = mapped_column(String(2048))
    url_hash: Mapped[str] = mapped_column(String(64))
    display_url: Mapped[str] = mapped_column(String(2048), default="")
    host: Mapped[str] = mapped_column(String(255), default="")
    depth: Mapped[int] = mapped_column(Integer, default=0)
    discovery_status: Mapped[str] = mapped_column(String(24), default="")
    latest_source_kind: Mapped[str] = mapped_column(String(16), default="")
    latest_title: Mapped[str] = mapped_column(String(1024), default="")
    latest_content_type: Mapped[str] = mapped_column(String(128), default="")
    first_seen_crawl_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawls.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_seen_crawl_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawls.id", ondelete="SET NULL"),
        nullable=True,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class SiteUrlObservation(Base):
    """Immutable per-crawl discovery provenance for one URL (append-only).

    Unique ``(crawl_id, site_url_id)``: one observation row per URL per crawl,
    recording exactly how the URL was discovered (root/link/sitemap/redirect),
    the parent URL, the source fetch artifact, the depth, and the observed
    URL/final URL/status/content-type/title at discovery time.
    """

    __tablename__ = "site_url_observations"
    __table_args__ = (
        UniqueConstraint(
            "crawl_id", "site_url_id", name="uq_site_url_observation"
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
    crawl_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawls.id", ondelete="CASCADE"),
        index=True,
    )
    site_url_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_urls.id", ondelete="CASCADE"),
        index=True,
    )
    source_kind: Mapped[str] = mapped_column(String(16))
    parent_site_url_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_urls.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_fetch_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    depth: Mapped[int] = mapped_column(Integer, default=0)
    observed_url: Mapped[str] = mapped_column(String(2048), default="")
    final_url: Mapped[str] = mapped_column(String(2048), default="")
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str] = mapped_column(String(128), default="")
    title: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class MonitoredSiteUrl(Base):
    """Persistent project monitored-set projection (mutable, not per-crawl).

    Unique ``(project_id, site_url_id)``: one monitored membership per URL per
    project. ``active`` + ``selection_source`` (``user`` | ``free_sample``)
    drive the workspace quota. The partial-friendly index on
    ``(workspace_id, active)`` supports the atomic workspace-wide active-count
    quota check. Rows are preserved (never deleted) on downgrade — deactivated,
    not removed — so evidence/history survives capability changes.
    """

    __tablename__ = "monitored_site_urls"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "site_url_id", name="uq_monitored_site_url"
        ),
        Index(
            "ix_monitored_site_urls_ws_active",
            "workspace_id",
            "active",
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
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_health_profiles.id", ondelete="CASCADE"),
        index=True,
    )
    site_url_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_urls.id", ondelete="CASCADE"),
        index=True,
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    selection_source: Mapped[str] = mapped_column(
        String(16), default=SELECTION_SOURCE_USER
    )
    # The selection revision at which this row was added (nullable membership).
    selecting_membership_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    deselected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class SiteCrawlTask(Base):
    """One queue+lease row for a ``discover`` | ``analyze`` | ``link_check`` unit.

    Reuses the exact queue-row column contract of ``AuditTask`` (status /
    lease_owner / lease_expires_at / heartbeat_at / attempt_count /
    max_attempts / available_at / priority / randomized_position /
    idempotency_key / error_code / error_detail / completed_at /
    result_artifact_id) so the single generic ``PostgresTaskQueue`` serves it
    unchanged (invariant 8). Double-claim is prevented by ``FOR UPDATE SKIP
    LOCKED`` plus the unique ``idempotency_key``.

    Carries an integer ``generation`` (default ``INITIAL_TASK_GENERATION`` = 0).
    Initial work is generation 0; a remove/re-add or explicit rerun of the same
    URL allocates the NEXT generation under lock, so the unique
    ``(crawl_id, task_kind, url_hash, generation)`` slot key never collides with
    a cancelled task and every rerun gets a fresh task/artifact identity.
    """

    __tablename__ = "site_crawl_tasks"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_site_crawl_task_idempotency_key"
        ),
        UniqueConstraint(
            "crawl_id",
            "task_kind",
            "url_hash",
            "generation",
            name="uq_site_crawl_task_slot",
        ),
        # Claimable-task index (queue claim path).
        Index(
            "ix_site_crawl_tasks_claim",
            "status",
            "available_at",
        ),
        # Expired-lease sweeper index.
        Index(
            "ix_site_crawl_tasks_lease",
            "status",
            "lease_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    crawl_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawls.id", ondelete="CASCADE"),
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    # Nullable: a discover task may enqueue before a SiteUrl identity exists.
    site_url_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_urls.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    task_kind: Mapped[str] = mapped_column(String(16), default=TASK_KIND_DISCOVER)
    requested_url: Mapped[str] = mapped_column(String(2048), default="")
    url_hash: Mapped[str] = mapped_column(String(64), default="")
    # Discovery provenance for deterministic frontier ordering.
    parent_site_url_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_urls.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_task_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawl_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    depth: Mapped[int] = mapped_column(Integer, default=0)
    # Task/artifact identity generation (0 = initial; rerun allocates next).
    generation: Mapped[int] = mapped_column(
        Integer, default=INITIAL_TASK_GENERATION
    )
    idempotency_key: Mapped[str] = mapped_column(String(160))

    # --- Queue + lease state (identical contract to AuditTask) -----------
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
        Integer, default=site_health_settings.max_attempts
    )

    # --- Execution result (single-writer = claiming worker, invariant 3) --
    result_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_fetch_artifacts.id", ondelete="SET NULL"),
        nullable=True,
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

    crawl: Mapped[SiteCrawl] = relationship(
        "SiteCrawl", back_populates="tasks"
    )


class SiteFetchAttempt(Base):
    """Append-only diagnostic record of one actual HTTP attempt (invariant 3).

    One row per real network call (including retries). Records the target host
    (never credentials or query secrets), the method, the safe outcome/error
    token, the status, latency, and byte counts. Never stores a raw body or a
    sensitive header.
    """

    __tablename__ = "site_fetch_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawl_tasks.id", ondelete="CASCADE"),
        index=True,
    )
    crawl_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawls.id", ondelete="CASCADE"),
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    method: Mapped[str] = mapped_column(String(8), default="")
    # Host only — no credentials, no query string secrets.
    target_host: Mapped[str] = mapped_column(String(255), default="")
    outcome: Mapped[str] = mapped_column(String(16), default="")
    error_code: Mapped[str] = mapped_column(String(32), default="")
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wire_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decoded_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set on the succeeding attempt (SET NULL if the artifact is later removed).
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_fetch_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class SiteFetchArtifact(Base):
    """Immutable evidence: one successful fetch's delivery facts (invariant 3).

    Written exactly once by the claiming worker (unique ``task_id``). Stores the
    requested/final URL, the redirect chain, the status, the redacted response
    headers (allowlist only), the content type/hash, timing/byte facts, the HTTP
    version, the extractor version, and bounded normalized parsed facts for
    analyze tasks. There is NO raw HTML body column — only bounded, redacted
    normalized facts (subplan Persistence contract).
    """

    __tablename__ = "site_fetch_artifacts"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_site_fetch_artifact_task"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawl_tasks.id", ondelete="CASCADE"),
        index=True,
    )
    crawl_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawls.id", ondelete="CASCADE"),
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    fetch_purpose: Mapped[str] = mapped_column(String(16), default="")
    requested_url: Mapped[str] = mapped_column(String(2048), default="")
    final_url: Mapped[str] = mapped_column(String(2048), default="")
    # Ordered redirect hops (safe: URLs only, no credentials).
    redirect_chain: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Response headers, redacted to the config-owned allowlist.
    redacted_headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    content_type: Mapped[str] = mapped_column(String(128), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    http_version: Mapped[str] = mapped_column(String(16), default="")
    ttfb_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wire_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decoded_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extractor_version: Mapped[str] = mapped_column(String(32), default="")
    # Bounded normalized parsed facts (analyze tasks). Never a raw body.
    normalized_facts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class SitePageAnalysis(Base):
    """Artifact-derived per-URL analysis projection (unique ``artifact_id``).

    Carries the Technical/AEO/overall scores, the analysis status, the
    analyzer/scoring versions, and the source evaluation/artifact ID arrays for
    full provenance. One analysis per immutable artifact.
    """

    __tablename__ = "site_page_analyses"
    __table_args__ = (
        UniqueConstraint("artifact_id", name="uq_site_page_analysis_artifact"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    crawl_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawls.id", ondelete="CASCADE"),
        index=True,
    )
    site_url_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_urls.id", ondelete="CASCADE"),
        index=True,
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_fetch_artifacts.id", ondelete="CASCADE"),
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(24), default=PAGE_ANALYSIS_STATUS_PENDING
    )
    technical_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    aeo_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    analyzer_version: Mapped[str] = mapped_column(String(32), default="")
    scoring_version: Mapped[str] = mapped_column(String(32), default="")
    # Source provenance arrays (evaluation + artifact IDs).
    source_evaluation_ids: Mapped[list | None] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=True
    )
    source_artifact_ids: Mapped[list | None] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=True
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class SiteLinkReference(Base):
    """Deduplicated link/asset reference discovered during page analysis.

    Deduplicated by ``(source_artifact_id, kind, target_hash,
    evidence_fingerprint)`` so re-parsing the same artifact never doubles rows.
    Records the normalized target URL/hash, the kind (anchor|image|script|
    stylesheet), internal/external classification, rel/anchor evidence, and the
    resolved target task/artifact IDs (link-check provenance).
    """

    __tablename__ = "site_link_references"
    __table_args__ = (
        UniqueConstraint(
            "source_artifact_id",
            "kind",
            "target_hash",
            "evidence_fingerprint",
            name="uq_site_link_reference_dedupe",
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
    source_analysis_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_page_analyses.id", ondelete="CASCADE"),
        index=True,
    )
    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_fetch_artifacts.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16), default="")
    target_url: Mapped[str] = mapped_column(String(2048), default="")
    target_hash: Mapped[str] = mapped_column(String(64), default="")
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    rel: Mapped[str] = mapped_column(String(128), default="")
    anchor_text: Mapped[str] = mapped_column(String(1024), default="")
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    target_task_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawl_tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    target_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_fetch_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    analyzer_version: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class SiteRuleEvaluation(Base):
    """Immutable per-rule evaluation for one analysis (unique per rule).

    Unique ``(analysis_id, rule_id)``: exactly one evaluation per configured
    rule per analysis. Records the outcome (pass|fail|not_applicable|error),
    bounded exact evidence, the rule's dimension/category/severity/weight, the
    supporting artifact IDs, and the extractor/analyzer/rule versions for full
    reproducibility (invariant 4).
    """

    __tablename__ = "site_rule_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "analysis_id", "rule_id", name="uq_site_rule_evaluation"
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
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_page_analyses.id", ondelete="CASCADE"),
        index=True,
    )
    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_fetch_artifacts.id", ondelete="CASCADE"),
        index=True,
    )
    rule_id: Mapped[str] = mapped_column(String(64))
    dimension: Mapped[str] = mapped_column(String(16), default="")
    category: Mapped[str] = mapped_column(String(32), default="")
    severity: Mapped[str] = mapped_column(String(16), default="")
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str] = mapped_column(String(16), default="")
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    supporting_artifact_ids: Mapped[list | None] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=True
    )
    extractor_version: Mapped[str] = mapped_column(String(32), default="")
    analyzer_version: Mapped[str] = mapped_column(String(32), default="")
    rule_version: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class SiteIssue(Base):
    """Failure projection of one failed rule evaluation (unique per evaluation).

    Unique ``evaluation_id``: one issue per ``fail`` evaluation. Snapshots the
    rule's dimension/category/severity, the exact evidence, and the remediation
    text at evaluation time so a later rule-catalog change never rewrites
    history. Indexed for issue filtering (``crawl_id, severity, category,
    rule_id``) and per-URL history (``site_url_id, created_at``).
    """

    __tablename__ = "site_issues"
    __table_args__ = (
        UniqueConstraint("evaluation_id", name="uq_site_issue_evaluation"),
        Index(
            "ix_site_issues_filter",
            "crawl_id",
            "severity",
            "category",
            "rule_id",
        ),
        Index(
            "ix_site_issues_url_created",
            "site_url_id",
            "created_at",
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
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    crawl_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawls.id", ondelete="CASCADE"),
        index=True,
    )
    site_url_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_urls.id", ondelete="CASCADE"),
        index=True,
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_page_analyses.id", ondelete="CASCADE"),
        index=True,
    )
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_rule_evaluations.id", ondelete="CASCADE"),
        index=True,
    )
    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_fetch_artifacts.id", ondelete="CASCADE"),
    )
    rule_id: Mapped[str] = mapped_column(String(64))
    dimension: Mapped[str] = mapped_column(String(16), default="")
    category: Mapped[str] = mapped_column(String(32), default="")
    severity: Mapped[str] = mapped_column(String(16), default="")
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    remediation: Mapped[str] = mapped_column(Text, default="")
    analyzer_version: Mapped[str] = mapped_column(String(32), default="")
    rule_version: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class SiteHealthSnapshot(Base):
    """Immutable crawl-level aggregate score/coverage snapshot (unique crawl).

    Unique ``crawl_id``: one aggregate snapshot per crawl. Records the
    selected/analyzed URL coverage counts, the Technical/AEO/overall scores, the
    issue/severity/category rollups, the source analysis/artifact/evaluation ID
    arrays, and the analyzer/scoring versions.
    """

    __tablename__ = "site_health_snapshots"
    __table_args__ = (
        UniqueConstraint("crawl_id", name="uq_site_health_snapshot_crawl"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    crawl_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawls.id", ondelete="CASCADE"),
        index=True,
    )
    selected_url_count: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_url_count: Mapped[int] = mapped_column(Integer, default=0)
    technical_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    aeo_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    issue_count: Mapped[int] = mapped_column(Integer, default=0)
    # Severity/category rollups (safe aggregate maps).
    severity_counts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    category_counts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_analysis_ids: Mapped[list | None] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=True
    )
    source_artifact_ids: Mapped[list | None] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=True
    )
    source_evaluation_ids: Mapped[list | None] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)), nullable=True
    )
    analyzer_version: Mapped[str] = mapped_column(String(32), default="")
    scoring_version: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class SiteCrawlEvent(Base):
    """Append-only safe crawl lifecycle event (the SSE source, invariant 3).

    Payloads for sample (Free) crawls never include frontier, discarded-
    candidate, or total-site counts (product contract — no total disclosure).
    Indexed by ``created_at`` for ordered polling/streaming.
    """

    __tablename__ = "site_crawl_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    crawl_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("site_crawls.id", ondelete="CASCADE"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text, default="")
    # Safe payload — never frontier/overflow/total counts for sample crawls.
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    crawl: Mapped[SiteCrawl] = relationship(
        "SiteCrawl", back_populates="events"
    )
