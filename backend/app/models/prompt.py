# Dedicated prompt-resource persistence models (Q3=A, UUID PKs).
#
# Decision Q3=A makes prompts a first-class resource (``PromptSet`` + ``Prompt``)
# rather than a JSON array on the project. Prompts are grouped in sets so an
# audit can reference a whole set; each prompt carries its intent, branded flag,
# enabled flag, origin, and (for future AI generation, B-4) the evidence that
# produced it.
#
# A ``PromptSet`` belongs to a ``Project`` (workspace-scoped), so access is
# enforced through the project's workspace (invariant 5).
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.config.projects import DEFAULT_PROMPT_ORIGIN
from app.core.config.prompts import DEFAULT_PROMPT_STATUS, TOPIC_ORIGIN_MANUAL
from app.core.database import Base
from app.domain.prompts.normalization import prompt_text_hash


class Topic(Base):
    """A topical category grouping prompts within a project.

    First-class (not just ``Prompt.theme``) so topics can be added manually,
    renamed, exist empty, and be targeted by AI generation. Workspace access is
    enforced through the project (invariant 5). ``origin`` records whether a
    human or the generation pipeline created it.
    """

    __tablename__ = "topics"
    __table_args__ = (
        # Case-insensitive uniqueness: generation matches topics with
        # ``casefold()``, so "Shoes" and "shoes" are one topic to the app and
        # the DB must agree (functional index; no plain-name constraint).
        Index(
            "uq_topic_project_name",
            "project_id",
            text("lower(name)"),
            unique=True,
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
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(String(1024), default="")
    # manual | generated (config/prompts.py TOPIC_ORIGIN_*).
    origin: Mapped[str] = mapped_column(String(32), default=TOPIC_ORIGIN_MANUAL)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    project: Mapped[Project] = relationship("Project", back_populates="topics")
    prompts: Mapped[list[Prompt]] = relationship(
        "Prompt", back_populates="topic", passive_deletes=True
    )


class PromptSet(Base):
    """A named collection of prompts belonging to a project."""

    __tablename__ = "prompt_sets"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    project: Mapped[Project] = relationship("Project", back_populates="prompt_sets")
    prompts: Mapped[list[Prompt]] = relationship(
        "Prompt",
        back_populates="prompt_set",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Prompt.created_at",
    )


class Prompt(Base):
    """A single benchmark prompt within a set.

    ``origin`` records provenance (manual / imported / generated). Generated
    prompts additionally carry ``generation_evidence`` (the model + reasoning
    that produced them). ``status`` is the review lifecycle: generated
    suggestions land ``proposed`` and only a human acceptance makes them
    ``active`` (audit-eligible); ``archived`` keeps history. The
    ``(prompt_set_id, normalized_text_hash)`` uniqueness makes dedupe
    conflict-safe under concurrent generation (DB-enforced, not app-checked).
    """

    __tablename__ = "prompts"
    __table_args__ = (
        UniqueConstraint(
            "prompt_set_id",
            "normalized_text_hash",
            name="uq_prompt_set_normalized_text",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    prompt_set_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("prompt_sets.id", ondelete="CASCADE"),
        index=True,
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("topics.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    text: Mapped[str] = mapped_column(Text)
    # sha256 hex of the normalized text (domain/prompts/normalization.py) —
    # the dedupe key backing the per-set uniqueness constraint.
    normalized_text_hash: Mapped[str] = mapped_column(String(64), default="")
    theme: Mapped[str] = mapped_column(String(255), default="")
    # Empty string means "unspecified"; otherwise one of PROMPT_INTENTS.
    intent: Mapped[str] = mapped_column(String(32), default="")
    branded: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # proposed | active | archived (config/prompts.py PROMPT_STATUS_*).
    status: Mapped[str] = mapped_column(
        String(16), default=DEFAULT_PROMPT_STATUS, server_default=DEFAULT_PROMPT_STATUS
    )
    origin: Mapped[str] = mapped_column(String(32), default=DEFAULT_PROMPT_ORIGIN)
    generation_evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    prompt_set: Mapped[PromptSet] = relationship("PromptSet", back_populates="prompts")
    topic: Mapped[Topic | None] = relationship("Topic", back_populates="prompts")

    @validates("text")
    def _sync_normalized_hash(self, _key: str, value: str) -> str:
        # Keeps the dedupe key correct on every ORM write path (create, edit,
        # test seeds) without each caller having to remember it.
        self.normalized_text_hash = prompt_text_hash(value)
        return value


from app.models.project import Project  # noqa: E402
