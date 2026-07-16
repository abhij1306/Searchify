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

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config.projects import DEFAULT_PROMPT_ORIGIN
from app.core.database import Base


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

    project: Mapped[Project] = relationship(
        "Project", back_populates="prompt_sets"
    )
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
    that produced them) — plumbing for the roadmap ``/generate`` feature (B-4);
    at MVP prompts arrive manually or via CSV import.
    """

    __tablename__ = "prompts"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    prompt_set_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("prompt_sets.id", ondelete="CASCADE"),
        index=True,
    )
    text: Mapped[str] = mapped_column(Text)
    theme: Mapped[str] = mapped_column(String(255), default="")
    # Empty string means "unspecified"; otherwise one of PROMPT_INTENTS.
    intent: Mapped[str] = mapped_column(String(32), default="")
    branded: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[str] = mapped_column(
        String(32), default=DEFAULT_PROMPT_ORIGIN
    )
    generation_evidence: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    prompt_set: Mapped[PromptSet] = relationship(
        "PromptSet", back_populates="prompts"
    )


from app.models.project import Project  # noqa: E402
