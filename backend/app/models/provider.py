# BYOK provider settings persistence models (B4, UUID PKs, workspace-scoped).
#
# ``ProviderConnection`` holds a Fernet-encrypted BYOK secret (invariant 6): the
# ciphertext lives in ``api_key_encrypted`` and is NEVER serialized into any
# response DTO or log line. ``ProviderRoute`` records the logical -> transport
# identity resolution (invariant 10). ``ProviderConnectionTest`` is an
# append-only history of connectivity checks. ``DiscoveryModelConfig`` is
# plumbing-only per decision B-4 — stored, not invoked at MVP.
#
# Everything is scoped by ``workspace_id`` (invariant 5).
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.constants import CASCADE_ALL_DELETE_ORPHAN, ON_DELETE_SET_NULL


class ProviderConnection(Base):
    """A workspace-owned BYOK credential for one transport provider.

    The API key is stored Fernet-encrypted in ``api_key_encrypted`` and is
    decrypted only at execution time to build an adapter (invariant 6). No code
    path places the decrypted key — or the ciphertext — into a response DTO.
    """

    __tablename__ = "provider_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    # Human label for the connection (e.g. "Prod OpenRouter key").
    label: Mapped[str] = mapped_column(String(255), default="")
    # An active transport (openai|anthropic|google) on new rows; may hold the
    # historical ``openrouter`` token on legacy rows (read-only, v2).
    transport_provider: Mapped[str] = mapped_column(String(32))
    # Optional endpoint override (self-hosted gateway / proxy); "" = catalog URL.
    base_url: Mapped[str] = mapped_column(String(1024), default="")
    # Fernet ciphertext of the BYOK secret. NEVER returned in a DTO.
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Non-empty marker naming why an active row was retired (e.g. the v2
    # ``openrouter_retired_v2`` migration). "" for rows never auto-deactivated.
    deactivation_reason: Mapped[str] = mapped_column(
        String(64), default="", server_default=""
    )
    # Result of the most recent connectivity test (denormalized for listing).
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_test_status: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    routes: Mapped[list[ProviderRoute]] = relationship(
        "ProviderRoute",
        back_populates="connection",
        cascade=CASCADE_ALL_DELETE_ORPHAN,
        passive_deletes=True,
        order_by="ProviderRoute.created_at",
    )
    tests: Mapped[list[ProviderConnectionTest]] = relationship(
        "ProviderConnectionTest",
        back_populates="connection",
        cascade=CASCADE_ALL_DELETE_ORPHAN,
        passive_deletes=True,
        order_by="ProviderConnectionTest.created_at",
    )


class ProviderRoute(Base):
    """Resolves a logical engine to a transport + model on a connection.

    Records the logical vs transport identity (invariant 10): ``logical_engine``
    is what the user asked for (chatgpt|gemini|claude), ``transport_provider``
    is how it is reached (openai|anthropic|google, or the historical
    openrouter token on legacy rows), and ``transport_model``
    is the concrete model. ``is_default`` marks the preferred route for an
    engine within the workspace.
    """

    __tablename__ = "provider_routes"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("provider_connections.id", ondelete="CASCADE"),
        index=True,
    )
    logical_engine: Mapped[str] = mapped_column(String(32))
    transport_provider: Mapped[str] = mapped_column(String(32))
    transport_model: Mapped[str] = mapped_column(String(255))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # False marks a retired route (e.g. a legacy openrouter route the v2
    # migration deactivated) so read clients skip it without deleting history.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Non-empty marker naming why an active route was retired. "" otherwise.
    deactivation_reason: Mapped[str] = mapped_column(
        String(64), default="", server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    connection: Mapped[ProviderConnection] = relationship(
        "ProviderConnection", back_populates="routes"
    )


class ProviderConnectionTest(Base):
    """Append-only history of connectivity checks for a connection.

    Immutable per invariant 3: one row is written per ``/test`` invocation and
    never mutated. The decrypted key is never stored here — only the outcome.
    """

    __tablename__ = "provider_connection_tests"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("provider_connections.id", ondelete="CASCADE"),
        index=True,
    )
    # ok | failed (provider_catalog.TEST_STATUS_*).
    status: Mapped[str] = mapped_column(String(16))
    # Classification token on failure (provider_catalog.ERROR_*); "" on success.
    error_code: Mapped[str] = mapped_column(String(32), default="")
    # Short, credential-free human message (never echoes the key).
    detail: Mapped[str] = mapped_column(String(1024), default="")
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    # Provenance of what was probed (logical_engine / transport / model).
    logical_engine: Mapped[str] = mapped_column(String(32), default="")
    transport_provider: Mapped[str] = mapped_column(String(32), default="")
    transport_model: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    connection: Mapped[ProviderConnection] = relationship(
        "ProviderConnection", back_populates="tests"
    )


class DiscoveryModelConfig(Base):
    """Plumbing-only prompt-discovery model config (decision B-4).

    Stored so the schema + settings surface is complete, but NOT invoked at MVP
    (the ``/prompt-sets/{id}/generate`` endpoint is a stub). Records which
    logical engine / transport / model would drive AI prompt suggestion.
    """

    __tablename__ = "discovery_model_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("provider_connections.id", ondelete=ON_DELETE_SET_NULL),
        nullable=True,
        index=True,
    )
    logical_engine: Mapped[str] = mapped_column(String(32), default="")
    transport_provider: Mapped[str] = mapped_column(String(32), default="")
    transport_model: Mapped[str] = mapped_column(String(255), default="")
    # Free-form tunables (temperature, max prompts, etc.) — roadmap plumbing.
    parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
