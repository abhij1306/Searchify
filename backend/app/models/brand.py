# Normalized brand-identity persistence models (B-1, UUID PKs).
#
# Decision B-1 replaces the reference's JSON-blob brand identity with explicit,
# normalized rows so each alias / domain / competitor is queryable and
# individually editable. A serialization shim (``domain/projects/shim.py``)
# rebuilds the plain dict ``ScoringConfig.from_project`` expects from these rows
# so downstream scoring (B5/B6) works unchanged.
#
# Everything here is scoped to a ``Project`` (which is itself workspace-scoped),
# so access is enforced through the project's workspace (invariant 5).
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Brand(Base):
    """The brand a project measures. One brand per project.

    ``brand_name`` is duplicated onto ``Project.brand_name`` for convenience,
    but the aliases live in normalized ``BrandAlias`` rows.
    """

    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("project_id", name="uq_brand_project"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    project: Mapped[Project] = relationship("Project", back_populates="brand")
    aliases: Mapped[list[BrandAlias]] = relationship(
        "BrandAlias",
        back_populates="brand",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="BrandAlias.created_at",
    )


class BrandAlias(Base):
    """An alternate spelling / trade name for a project's brand."""

    __tablename__ = "brand_aliases"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    brand_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("brands.id", ondelete="CASCADE"),
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    brand: Mapped[Brand] = relationship("Brand", back_populates="aliases")


class Competitor(Base):
    """A competitor brand tracked for share-of-voice.

    ``aliases`` and ``domains`` are stored as JSONB string arrays on the row —
    the competitor itself is a first-class normalized entity, but its alias /
    domain lists are value-object arrays consumed wholesale by the scorer.
    """

    __tablename__ = "competitors"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255))
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    domains: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    project: Mapped[Project] = relationship("Project", back_populates="competitors")


class OwnedDomain(Base):
    """A domain the brand owns (its answers/citations count as owned)."""

    __tablename__ = "owned_domains"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    domain: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    project: Mapped[Project] = relationship("Project", back_populates="owned_domains")


class UnintendedDomain(Base):
    """A domain that must NOT be credited as owned (e.g. a support portal)."""

    __tablename__ = "unintended_domains"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    domain: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    project: Mapped[Project] = relationship(
        "Project", back_populates="unintended_domains"
    )


from app.models.project import Project  # noqa: E402
