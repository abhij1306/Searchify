# First-party data integrations persistence models (UUID PKs, workspace-scoped).
#
# The GSC / GA4 / Bing connect + sync graph (docs/roadmap/integrations.md
# section 3). ``IntegrationOAuthGrant`` is the credential-owning entity — one
# row per workspace per OAuth transport, owning the Fernet-encrypted tokens
# (NEVER serialized into any DTO or log, invariant 6) and the refresh/revoke
# lifecycle. ``IntegrationConnection`` binds a logical provider to a grant and
# carries NO credential columns. ``IntegrationSyncRun`` reuses the exact
# queue-row column contract of ``SiteCrawlTask`` so the one generic
# ``PostgresTaskQueue`` claims/leases/heartbeats/sweeps it unchanged
# (invariant 8). ``IntegrationImportArtifact`` is the immutable, written-once
# record of one fetched page of provider data (invariant 3);
# ``IntegrationMetricRow`` is the derived fact row carrying source-artifact +
# importer-version + resync_seq provenance (invariant 4).
# ``IntegrationPropertyMapping`` is the constrained bridge from a provider
# property to an owning project. ``IntegrationEvent`` is the append-only
# lifecycle log (AuditEvent shape) and ``IntegrationOAuthState`` the
# one-time-consumption OAuth state store (spec section 2).
#
# Same-workspace integrity between the NEW tables is enforced by composite
# foreign keys (the site_health pattern): a child row's
# ``(workspace_id, parent_id)`` must reference a parent row in the SAME
# workspace (invariant 5). References to pre-existing tables (``projects``,
# ``users``) stay plain FKs — same-workspace validation for those happens at
# the service layer on write.
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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

from app.core.config.integrations import (
    GRANT_STATUS_CONNECTED,
    INTEGRATION_IMPORTER_VERSION,
    MAPPING_STATUS_ACTIVE,
    SYNC_KIND_ON_DEMAND,
    integration_settings,
)
from app.core.config.task_queue import (
    TASK_CLAIMABLE_STATUSES,
    TASK_LEASED_STATUSES,
    TASK_STATUS_QUEUED,
)
from app.core.database import Base

# FK target references + ondelete actions as named constants (site_health
# pattern): a typo in a ``table.column`` reference would otherwise silently
# bind the wrong parent; naming them once also makes a rename a one-line
# change.
_FK_WORKSPACE = "workspaces.id"
_FK_PROJECT = "projects.id"
_FK_USER = "users.id"
_FK_GRANT = "integration_oauth_grants.id"
_FK_CONNECTION = "integration_connections.id"
_FK_SYNC_RUN = "integration_sync_runs.id"
_FK_IMPORT_ARTIFACT = "integration_import_artifacts.id"
_ON_DELETE_CASCADE = "CASCADE"
_ON_DELETE_SET_NULL = "SET NULL"

# Queue-row statuses in which a sync run occupies its (connection, kind,
# window) slot: every non-terminal status (queued/leased/running/retry_wait).
# Built from the shared queue-neutral tokens so the partial unique index can
# never drift from the queue lifecycle vocabulary.
_ACTIVE_WINDOW_PREDICATE = "status IN ({})".format(
    ", ".join(f"'{s}'" for s in sorted(TASK_CLAIMABLE_STATUSES | TASK_LEASED_STATUSES))
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class IntegrationOAuthGrant(Base):
    """The credential-owning entity: one workspace grant per OAuth transport.

    Owns the Fernet-encrypted access/refresh tokens — stored ONCE per
    transport, never duplicated per connection (a single Google grant covers
    both GSC and GA4) — and the whole refresh/revoke lifecycle (spec
    section 2/3). The ``*_encrypted`` columns are NEVER serialized into any
    DTO or log line (invariant 6), exactly like
    ``ProviderConnection.api_key_encrypted``. ``pending_revocation``
    deliberately retains the encrypted tokens so a background retry can
    complete a failed remote revoke before credentials are destroyed
    (spec section 5).
    """

    __tablename__ = "integration_oauth_grants"
    __table_args__ = (
        # Find-or-create contract (spec section 2): one grant per
        # (workspace, transport).
        UniqueConstraint(
            "workspace_id", "transport", name="uq_integration_grant_ws_transport"
        ),
        # Backs the composite (workspace_id, grant_id) FK on connections.
        UniqueConstraint("workspace_id", "id", name="uq_integration_grants_ws_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_WORKSPACE, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    # google_oauth | microsoft_oauth — the physical OAuth surface.
    transport: Mapped[str] = mapped_column(String(24))
    # Fernet ciphertexts. NEVER returned in a DTO, NEVER logged (invariant 6).
    access_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, default="")
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    granted_scopes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # connected | needs_reauth | pending_revocation | revoked | error.
    status: Mapped[str] = mapped_column(
        String(24), default=GRANT_STATUS_CONNECTED, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class IntegrationConnection(Base):
    """One logical connected surface (a provider bound to a grant).

    Carries NO credential columns — tokens live solely on the parent
    ``IntegrationOAuthGrant``. GSC and GA4 are two connection rows pointing
    at the SAME Google grant, so one consent yields both surfaces while
    refresh/revoke stay grant-scoped. The composite FK pins the grant to the
    SAME workspace (invariant 5).
    """

    __tablename__ = "integration_connections"
    __table_args__ = (
        # One connection per provider per grant (one Google consent attaches
        # exactly one gsc + one ga4 row).
        UniqueConstraint(
            "grant_id", "provider", name="uq_integration_connection_grant_provider"
        ),
        # Backs composite (workspace_id, connection_id) FKs on child tables.
        UniqueConstraint("workspace_id", "id", name="uq_integration_connections_ws_id"),
        ForeignKeyConstraint(
            ["workspace_id", "grant_id"],
            ["integration_oauth_grants.workspace_id", _FK_GRANT],
            ondelete=_ON_DELETE_CASCADE,
            name="fk_integration_connection_grant_scoped",
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
    grant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    # gsc | ga4 | bing (must be compatible with the grant's transport).
    provider: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(255), default="")
    # Provider-side property/site id (GA4 property id, GSC site URL).
    account_ref: Mapped[str] = mapped_column(String(1024), default="")
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class IntegrationSyncRun(Base):
    """One sync execution — a queue+lease row for the integrations worker.

    Reuses the exact queue-row column contract of ``SiteCrawlTask`` (status /
    priority / randomized_position / available_at / lease_owner /
    lease_expires_at / heartbeat_at / attempt_count / max_attempts /
    idempotency_key / error_code / error_detail / created_at / updated_at /
    completed_at) so the single generic ``PostgresTaskQueue`` serves it
    unchanged (invariant 8). Double-claim is prevented by ``FOR UPDATE SKIP
    LOCKED`` plus the unique ``idempotency_key``.

    Window uniqueness is scoped so re-syncing a COMPLETED window stays
    possible (spec section 3/4): the partial unique index dedupes ACTIVE rows
    over ``(connection_id, sync_kind, window_start, window_end)`` while the
    full unique constraint on that tuple + ``resync_seq`` gives every re-sync
    a distinct, monotonically allocated run identity (and, downstream, new
    immutable artifacts + metric rows rather than overwrites).
    """

    __tablename__ = "integration_sync_runs"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_integration_sync_run_idempotency_key"
        ),
        # Full re-sync identity (…, resync_seq); ``resync_seq`` is allocated
        # atomically by the enqueue service (spec section 3).
        UniqueConstraint(
            "connection_id",
            "sync_kind",
            "window_start",
            "window_end",
            "resync_seq",
            name="uq_integration_sync_run_window_seq",
        ),
        # Backs the composite (workspace_id, sync_run_id) FK on artifacts.
        UniqueConstraint("workspace_id", "id", name="uq_integration_sync_runs_ws_id"),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["integration_connections.workspace_id", _FK_CONNECTION],
            ondelete=_ON_DELETE_CASCADE,
            name="fk_integration_sync_run_connection_scoped",
        ),
        # One ACTIVE run per (connection, kind, window); terminal rows leave
        # the window free to be re-synced.
        Index(
            "ix_integration_sync_runs_active_window",
            "connection_id",
            "sync_kind",
            "window_start",
            "window_end",
            unique=True,
            postgresql_where=text(_ACTIVE_WINDOW_PREDICATE),
        ),
        # Claimable-task index (queue claim path).
        Index("ix_integration_sync_runs_claim", "status", "available_at"),
        # Expired-lease sweeper index.
        Index("ix_integration_sync_runs_lease", "status", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_WORKSPACE, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    # scheduled | on_demand | backfill.
    sync_kind: Mapped[str] = mapped_column(String(16), default=SYNC_KIND_ON_DEMAND)
    # The requested date window (provider data is date-grained).
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    # Monotonic per-window re-sync revision (0 = first run of the window).
    resync_seq: Mapped[int] = mapped_column(Integer, default=0)
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
        Integer, default=integration_settings.sync_max_attempts
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


class IntegrationImportArtifact(Base):
    """Immutable, written-once record of one fetched page of provider data.

    Written once by the claiming worker; a re-sync produces a NEW artifact
    identity, never an overwrite (invariant 3). ``query_snapshot`` is the
    exact credential-free API query (dimensions/metrics/date range) — NEVER a
    credential (invariant 6). Small payloads are inline JSONB (bounded by the
    config ``max_inline_payload_bytes``); S3 offload keyed by ``payload_hash``
    is roadmap.
    """

    __tablename__ = "integration_import_artifacts"
    __table_args__ = (
        # Backs the composite (workspace_id, source_artifact_id) FK on metric
        # rows.
        UniqueConstraint(
            "workspace_id", "id", name="uq_integration_import_artifacts_ws_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "sync_run_id"],
            ["integration_sync_runs.workspace_id", _FK_SYNC_RUN],
            ondelete=_ON_DELETE_CASCADE,
            name="fk_integration_artifact_sync_run_scoped",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["integration_connections.workspace_id", _FK_CONNECTION],
            ondelete=_ON_DELETE_CASCADE,
            name="fk_integration_artifact_connection_scoped",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sync_run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_WORKSPACE, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(16))
    # Logical dataset id from INTEGRATION_DATASET_TEMPLATES (C1).
    dataset: Mapped[str] = mapped_column(String(48))
    # Credential-free query parameters (dimensions, metrics, date range).
    query_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # sha256 hex of the raw payload.
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    # RAW provider rows in this page (BEFORE client normalization dropped
    # any malformed rows) — the resume path's paging-termination measure.
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class IntegrationPropertyMapping(Base):
    """The constrained bridge from a provider property to an owning project.

    One ACTIVE owner per ``(workspace_id, provider, property_ref)`` across
    ALL connections (partial unique index), so a synced property derives to
    exactly one project and two OAuth connections cannot both claim it
    (invariant 2). The mapping's provider must match its connection's
    provider and the project must be in the same workspace — both validated
    on write by the mappings service (the project FK targets the pre-existing
    ``projects`` table, so its same-workspace binding is service-layer).
    """

    __tablename__ = "integration_property_mappings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "connection_id"],
            ["integration_connections.workspace_id", _FK_CONNECTION],
            ondelete=_ON_DELETE_CASCADE,
            name="fk_integration_mapping_connection_scoped",
        ),
        Index(
            "ix_integration_property_mappings_active_owner",
            "workspace_id",
            "provider",
            "property_ref",
            unique=True,
            postgresql_where=text(f"status = '{MAPPING_STATUS_ACTIVE}'"),
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
    connection_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    # Provider property id (GSC site URL / GA4 property id).
    property_ref: Mapped[str] = mapped_column(String(512))
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_PROJECT, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    # active | disabled.
    status: Mapped[str] = mapped_column(String(16), default=MAPPING_STATUS_ACTIVE)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class IntegrationMetricRow(Base):
    """The derived, normalized fact row the consumer surfaces read.

    Provenance on every row (invariant 4): ``source_artifact_id`` +
    ``importer_version`` (transform-code version) + ``resync_seq`` (the source
    run's data-run revision). Consumers read the LATEST ``resync_seq`` per
    identity tuple; old revisions are retained, never overwritten
    (invariant 3). ``project_id``/``property_ref`` are resolved via
    ``IntegrationPropertyMapping``, never from client input.
    """

    __tablename__ = "integration_metric_rows"
    __table_args__ = (
        # Row identity: one row per
        # (project, property, provider, dataset, date, dimension_key)
        # per re-sync revision.
        UniqueConstraint(
            "project_id",
            "property_ref",
            "provider",
            "dataset",
            "date",
            "dimension_key",
            "resync_seq",
            name="uq_integration_metric_row_identity",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_artifact_id"],
            ["integration_import_artifacts.workspace_id", _FK_IMPORT_ARTIFACT],
            ondelete=_ON_DELETE_CASCADE,
            name="fk_integration_metric_row_artifact_scoped",
        ),
        # Window scans by project+date (traffic/analytics projections).
        Index("ix_integration_metric_rows_project_date", "project_id", "date"),
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
    property_ref: Mapped[str] = mapped_column(String(512))
    provider: Mapped[str] = mapped_column(String(16))
    dataset: Mapped[str] = mapped_column(String(48))
    date: Mapped[date] = mapped_column(Date)
    # Packed dimension values in the dataset template's declared order
    # (" | "-joined — contract C1). Bounded so the identity unique index
    # always fits a btree row.
    dimension_key: Mapped[str] = mapped_column(String(1024))
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), index=True
    )
    resync_seq: Mapped[int] = mapped_column(Integer, default=0)
    importer_version: Mapped[str] = mapped_column(
        String(64), default=INTEGRATION_IMPORTER_VERSION
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class IntegrationEvent(Base):
    """Append-only integration lifecycle event (AuditEvent shape, invariant 3).

    Written once per lifecycle action (connect, test, sync start/finish,
    reauth, revoke) and never mutated. The connection/grant FKs null out on
    parent removal so the audit record survives a disconnect (spec
    section 5); workspace removal cascades. Payloads never carry tokens.
    """

    __tablename__ = "integration_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_WORKSPACE, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_CONNECTION, ondelete=_ON_DELETE_SET_NULL),
        nullable=True,
        index=True,
    )
    grant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_GRANT, ondelete=_ON_DELETE_SET_NULL),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text, default="")
    # Structured payload (provider, counts, run id) — never tokens/secrets.
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )


class IntegrationOAuthState(Base):
    """Persisted OAuth state nonce: atomic one-time consumption (spec §2).

    Binds the callback to the initiating user + workspace without trusting a
    client-supplied id (invariant 5). The state is consumed atomically
    (``UPDATE ... SET consumed_at ... WHERE consumed_at IS NULL``) BEFORE the
    code exchange; a replayed or expired state is rejected.
    """

    __tablename__ = "integration_oauth_states"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Signed-state JWT id — unique so a minted state is consumable once.
    jti: Mapped[str] = mapped_column(String(64), unique=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_WORKSPACE, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    # The initiating member (state <-> user binding, spec section 2 — not a
    # data-scoping column).
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(_FK_USER, ondelete=_ON_DELETE_CASCADE),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
