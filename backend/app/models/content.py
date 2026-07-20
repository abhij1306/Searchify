# AI content-generation persistence models (UUID PKs, workspace-scoped).
#
# ``ContentGeneration`` is the ``AuditTask`` pattern applied to content: an
# immutable request record that doubles as the shared-queue row (claimed via
# ``FOR UPDATE SKIP LOCKED`` through the generic ``PostgresTaskQueue``) plus
# single-writer result fields (the claiming worker is the only writer —
# invariant 3). ``ContentGenerationAttempt`` is the append-only per-provider-
# call log (one row per actual HTTP call, unique attempt number per record).
#
# Everything is scoped by ``workspace_id`` (invariant 5). Neither table ever
# stores the provider API key (invariant 6).
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

from app.core.config.content import CONTENT_MAX_ATTEMPTS
from app.core.config.task_queue import TASK_STATUS_QUEUED
from app.core.database import Base
from app.models.constants import CASCADE_ALL_DELETE_ORPHAN


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ContentGeneration(Base):
    """One immutable content-generation request + queue row + result.

    The frozen inputs (prompt, output type, website-context snapshot, message
    digest/snapshot) are written at enqueue and never mutated. The queue-lease
    columns mirror ``AuditTask`` exactly so the generic queue serves this row.
    The result fields are single-writer: only the claiming worker's atomic
    ``finalize_attempt`` transaction writes them (invariant 3).

    Idempotency is workspace-scoped: the composite
    ``(workspace_id, idempotency_key)`` unique constraint lets two workspaces
    reuse the same client key while keeping replays race-safe within one.
    """

    __tablename__ = "content_generations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_content_generation_ws_idem",
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

    # --- Frozen inputs (written at enqueue, never mutated) ----------------
    prompt: Mapped[str] = mapped_column(Text)
    output_type: Mapped[str] = mapped_column(String(32))
    website_context_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # included | unavailable | disabled (config CONTEXT_STATUS_*).
    website_context_status: Mapped[str] = mapped_column(String(16), default="")
    # Allowlisted page facts + provenance ids/counts. Never the key.
    website_context_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Stable hash over (project_id, prompt, output_type, context flag): the
    # idempotency replay/conflict comparator.
    request_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    message_digest: Mapped[str] = mapped_column(String(64), default="")
    # Safe truncated copy of the provider messages (provenance). Never the key.
    message_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # --- Queue + lease state (shared column contract with AuditTask) ------
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(
        String(24), default=TASK_STATUS_QUEUED, index=True
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
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
    max_attempts: Mapped[int] = mapped_column(Integer, default=CONTENT_MAX_ATTEMPTS)
    randomized_position: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str] = mapped_column(String(32), default="")
    error_detail: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Result (single-writer = claiming worker, invariant 3) ------------
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provider + requested model are frozen from config at enqueue — always
    # known up front, so both are required (no empty-string/NULL sentinel).
    provider: Mapped[str] = mapped_column(String(32))
    requested_model: Mapped[str] = mapped_column(String(255))
    returned_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    output_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What determined the provider request. NEVER the key (invariant 6).
    request_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generator_version: Mapped[str] = mapped_column(String(32), default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    attempts: Mapped[list[ContentGenerationAttempt]] = relationship(
        "ContentGenerationAttempt",
        back_populates="generation",
        cascade=CASCADE_ALL_DELETE_ORPHAN,
        passive_deletes=True,
        order_by="ContentGenerationAttempt.attempt_number",
    )


class ContentGenerationAttempt(Base):
    """Append-only record of one actual provider HTTP call (invariant 3).

    One row per real call (retries + failures + a call whose result was
    discarded because the record was cancelled mid-flight). Never the key.
    """

    __tablename__ = "content_generation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "content_generation_id",
            "attempt_number",
            name="uq_content_generation_attempt_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    content_generation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("content_generations.id", ondelete="CASCADE"),
        index=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    requested_model: Mapped[str] = mapped_column(String(255), default="")
    returned_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str] = mapped_column(String(32), default="")
    error_detail: Mapped[str] = mapped_column(Text, default="")
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    generation: Mapped[ContentGeneration] = relationship(
        "ContentGeneration", back_populates="attempts"
    )
