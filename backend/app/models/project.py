# Project persistence model (UUID PK, workspace-scoped per invariant 5).
#
# A ``Project`` is a brand-measurement workspace-owned resource. The brand
# identity is stored **normalized** across ``brand.py`` (B-1) rather than as
# JSON blobs; a serialization shim rebuilds the plain dict the scorer expects.
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config.projects import DEFAULT_BENCHMARK_MODE, DEFAULT_REPETITIONS
from app.core.database import Base


class Project(Base):
    """A workspace-owned brand-visibility project.

    Scoped by ``workspace_id`` (invariant 5). The brand identity lives in the
    normalized ``Brand``/``BrandAlias``/``Competitor``/``OwnedDomain``/
    ``UnintendedDomain`` rows related to this project.
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255))
    brand_name: Mapped[str] = mapped_column(String(255), default="")
    website_url: Mapped[str] = mapped_column(String(1024), default="")
    country_code: Mapped[str] = mapped_column(String(8), default="")
    language_code: Mapped[str] = mapped_column(String(16), default="")
    benchmark_mode: Mapped[str] = mapped_column(
        String(32), default=DEFAULT_BENCHMARK_MODE
    )
    default_repetitions: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_REPETITIONS
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Normalized brand identity (B-1). Cascade-delete keeps the child rows in
    # sync with the project lifecycle.
    brand: Mapped[Brand | None] = relationship(
        "Brand",
        back_populates="project",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    competitors: Mapped[list[Competitor]] = relationship(
        "Competitor",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Competitor.created_at",
    )
    owned_domains: Mapped[list[OwnedDomain]] = relationship(
        "OwnedDomain",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="OwnedDomain.created_at",
    )
    unintended_domains: Mapped[list[UnintendedDomain]] = relationship(
        "UnintendedDomain",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="UnintendedDomain.created_at",
    )
    prompt_sets: Mapped[list[PromptSet]] = relationship(
        "PromptSet",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PromptSet.created_at",
    )
    topics: Mapped[list[Topic]] = relationship(
        "Topic",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Topic.created_at",
    )


# Imported at module end to avoid circular imports at definition time; the
# relationships above reference them by string name so ordering is fine.
from app.models.brand import (  # noqa: E402
    Brand,
    Competitor,
    OwnedDomain,
    UnintendedDomain,
)
from app.models.prompt import PromptSet, Topic  # noqa: E402
