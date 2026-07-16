# Audit execution persistence models (B5, UUID PKs, workspace-scoped).
#
# An ``Audit`` is a workspace-scoped benchmark run over a project. The planner
# freezes prompt/engine/scoring snapshots (``AuditPromptSnapshot`` /
# ``AuditEngineSnapshot``) and enqueues one ``AuditTask`` (queue+lease row) per
# (prompt x engine x repetition) slot. The worker claims tasks via
# ``FOR UPDATE SKIP LOCKED``, calls the answer engine, and writes an immutable
# ``RawResponseArtifact`` plus an append-only ``ProviderAttempt`` (invariant 3 —
# written once, single writer = the claiming worker). ``AuditEvent`` rows are an
# append-only lifecycle log (the SSE source).
#
# Everything is scoped by ``workspace_id`` (invariant 5). Provenance triples
# (logical_engine / transport_provider / transport_model) are recorded on the
# engine snapshot, the task, and every attempt (invariant 10).
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

from app.core.config.audits import (
    AUDIT_STATUS_DRAFT,
    TASK_STATUS_QUEUED,
)
from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Audit(Base):
    """A workspace-scoped benchmark run over a project.

    Carries the stored 64-bit ``random_seed`` (persisted as a string so the
    full unsigned 64-bit range survives Postgres' signed ``bigint``), the frozen
    ``configuration`` (scoring identity + operational overrides), the aggregate
    ``summary`` (populated at finalize by B6), the requested/completed/failed
    counts, the lifecycle ``status``, and timestamps.
    """

    __tablename__ = "audits"

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
    status: Mapped[str] = mapped_column(
        String(32), default=AUDIT_STATUS_DRAFT, index=True
    )
    benchmark_mode: Mapped[str] = mapped_column(String(32), default="")
    # Neutral, brand-free system instruction frozen at creation (invariant 6).
    system_instruction: Mapped[str] = mapped_column(Text, default="")
    repetitions: Mapped[int] = mapped_column(Integer, default=1)
    # 64-bit seed stored as text so the full unsigned range is preserved and
    # reproduces the slot shuffle exactly (invariant 9).
    random_seed: Mapped[str] = mapped_column(String(32), default="")
    # Frozen scoring identity + operational overrides (never re-read from live
    # config after creation — determinism, invariant 9).
    configuration: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Aggregate metrics projection, populated at finalize (B6).
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Analyzer version stamped on finalize (invariant 4); "" until B6 runs.
    analyzer_version: Mapped[str] = mapped_column(String(32), default="")
    requested_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
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

    prompt_snapshots: Mapped[list[AuditPromptSnapshot]] = relationship(
        "AuditPromptSnapshot",
        back_populates="audit",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AuditPromptSnapshot.prompt_index",
    )
    engine_snapshots: Mapped[list[AuditEngineSnapshot]] = relationship(
        "AuditEngineSnapshot",
        back_populates="audit",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AuditEngineSnapshot.created_at",
    )
    tasks: Mapped[list[AuditTask]] = relationship(
        "AuditTask",
        back_populates="audit",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AuditTask.randomized_position",
    )
    events: Mapped[list[AuditEvent]] = relationship(
        "AuditEvent",
        back_populates="audit",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AuditEvent.created_at",
    )


class AuditPromptSnapshot(Base):
    """Immutable frozen copy of one prompt at audit creation (invariant 3).

    Freezes the prompt text/theme/intent so a later edit to the source
    ``Prompt`` never changes what an already-created audit measured.
    """

    __tablename__ = "audit_prompt_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "audit_id", "prompt_index", name="uq_audit_prompt_snapshot_index"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        index=True,
    )
    # Optional back-reference to the source prompt (nullable so deleting the
    # prompt never breaks the frozen snapshot).
    prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("prompts.id", ondelete="SET NULL"),
        nullable=True,
    )
    prompt_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    theme: Mapped[str] = mapped_column(String(255), default="")
    intent: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    audit: Mapped[Audit] = relationship(
        "Audit", back_populates="prompt_snapshots"
    )


class AuditEngineSnapshot(Base):
    """Immutable frozen copy of one measured engine route (invariants 3 + 10).

    Records the logical engine + resolved transport provider + concrete model
    and the ``ProviderConnection`` the worker resolves the BYOK key from at
    execution time (never the key itself — invariant 6).
    """

    __tablename__ = "audit_engine_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "audit_id", "logical_engine", name="uq_audit_engine_snapshot_engine"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        index=True,
    )
    logical_engine: Mapped[str] = mapped_column(String(32))
    transport_provider: Mapped[str] = mapped_column(String(32))
    transport_model: Mapped[str] = mapped_column(String(255))
    # Connection the key is resolved from at execution time (SET NULL so a
    # deleted connection does not break the frozen snapshot).
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("provider_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    base_url: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    audit: Mapped[Audit] = relationship(
        "Audit", back_populates="engine_snapshots"
    )


class AuditTask(Base):
    """One queue+lease row: a single (prompt x engine x repetition) slot.

    Claimed via ``FOR UPDATE SKIP LOCKED``. The lease fields (``lease_owner`` +
    ``lease_expires_at`` + ``heartbeat_at``) plus ``attempt_count`` /
    ``max_attempts`` implement the Postgres queue (invariant 8). Double-claim is
    prevented by ``SKIP LOCKED`` plus the unique ``idempotency_key`` and the
    unique ``(audit_id, prompt_index, repetition, logical_engine)`` slot key.
    Also serves as the per-execution row (answer/citations/score/snapshot).
    """

    __tablename__ = "audit_tasks"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_audit_task_idempotency_key"
        ),
        UniqueConstraint(
            "audit_id",
            "prompt_index",
            "repetition",
            "logical_engine",
            name="uq_audit_task_slot",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    prompt_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit_prompt_snapshots.id", ondelete="CASCADE"),
    )
    engine_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit_engine_snapshots.id", ondelete="CASCADE"),
    )
    # Slot identity.
    prompt_index: Mapped[int] = mapped_column(Integer)
    repetition: Mapped[int] = mapped_column(Integer)
    randomized_position: Mapped[int] = mapped_column(Integer, default=0)
    # Provenance triple (invariant 10), denormalized for claim ordering + logs.
    logical_engine: Mapped[str] = mapped_column(String(32))
    transport_provider: Mapped[str] = mapped_column(String(32))
    transport_model: Mapped[str] = mapped_column(String(255))
    # Frozen prompt text (denormalized from the snapshot for the worker).
    prompt_text: Mapped[str] = mapped_column(Text, default="")
    # Frozen route resolution for this slot (never contains the key).
    provider_route_snapshot: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))

    # --- Queue + lease state ---------------------------------------------
    status: Mapped[str] = mapped_column(
        String(24), default=TASK_STATUS_QUEUED, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    lease_owner: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)

    # --- Execution result (single-writer = claiming worker, invariant 3) --
    result_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("raw_response_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    answer_text: Mapped[str] = mapped_column(Text, default="")
    search_used: Mapped[bool] = mapped_column(Boolean, default=False)
    search_events: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    citations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    score: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Snapshot of exactly what determined the request. NEVER the key/brand list.
    request_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provider_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
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

    audit: Mapped[Audit] = relationship("Audit", back_populates="tasks")
    attempts: Mapped[list[ProviderAttempt]] = relationship(
        "ProviderAttempt",
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ProviderAttempt.attempt_number",
    )


class RawResponseArtifact(Base):
    """Immutable raw provider payload for one successful attempt (invariant 3).

    Written exactly once by the claiming worker and never mutated. Downstream
    analysis (B6) references this row + an ``analyzer_version`` (invariant 4).
    """

    __tablename__ = "raw_response_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
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
    logical_engine: Mapped[str] = mapped_column(String(32))
    transport_provider: Mapped[str] = mapped_column(String(32))
    transport_model: Mapped[str] = mapped_column(String(255))
    answer_text: Mapped[str] = mapped_column(Text, default="")
    search_used: Mapped[bool] = mapped_column(Boolean, default=False)
    search_events: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    citations: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    provider_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class ProviderAttempt(Base):
    """Append-only record of one provider call attempt (invariant 3 + 10).

    One row per attempt (including retries + failures). Records the provenance
    triple, the outcome status, error classification, and latency. Never the
    API key or the brand list.
    """

    __tablename__ = "provider_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audit_tasks.id", ondelete="CASCADE"),
        index=True,
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    logical_engine: Mapped[str] = mapped_column(String(32))
    transport_provider: Mapped[str] = mapped_column(String(32))
    transport_model: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16))
    error_code: Mapped[str] = mapped_column(String(32), default="")
    error_detail: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Set on the succeeding attempt.
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("raw_response_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    task: Mapped[AuditTask] = relationship(
        "AuditTask", back_populates="attempts"
    )


class AuditEvent(Base):
    """Append-only audit lifecycle event (the SSE source, invariant 3)."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("audits.id", ondelete="CASCADE"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text, default="")
    # Structured payload (status, counts, task id) — never secrets/brand list.
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    audit: Mapped[Audit] = relationship("Audit", back_populates="events")
