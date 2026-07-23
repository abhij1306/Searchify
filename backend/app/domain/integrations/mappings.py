"""Property-mapping service: write-time validation + lifecycle (spec §3, I8).

An ``IntegrationPropertyMapping`` is the constrained bridge from a
connection's provider-side property to the owning project — derivation
(I9) resolves ``project_id`` through it and rejects unmapped properties
rather than guessing. Every write validates:

- **provider binding** — the mapping's provider must equal the referenced
  connection's provider (a ``gsc`` mapping never points at a ``ga4``
  connection);
- **workspace binding** — connection + project live in the SAME workspace
  (invariant 5); the connection lookup is workspace-scoped and the project
  lookup reuses the projects service, so cross-workspace references are
  404s, never data;
- **owned-domain validation** (traffic.md §2) — the property must resolve,
  as a bare host, to one of the project's ``OwnedDomain`` rows before any
  ingest may target it (non-matching ⇒ ``MappingPropertyNotOwnedError``,
  API 422);
- **one active owner** — the partial unique index on
  ``(workspace_id, provider, property_ref) WHERE status = active``
  guarantees a single active owner across ALL connections; an
  ``IntegrityError`` from it surfaces as
  ``MappingActiveOwnerConflictError`` (API 409).

Disable is a STATUS FLIP to ``disabled``, never a row delete — the model's
status vocabulary keeps the mapping's history traceable for derivation.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.normalization import normalize_domain
from app.core.config.integrations import (
    GSC_DOMAIN_PROPERTY_PREFIX,
    MAPPING_STATUS_ACTIVE,
    MAPPING_STATUS_DISABLED,
)
from app.domain.integrations.schemas import IntegrationPropertyMappingResponse
from app.domain.integrations.service import get_connection
from app.domain.integrations.sync import integrity_constraint_name
from app.domain.projects.service import get_project
from app.models.integrations import IntegrationPropertyMapping

# Schema-object name pinned in ``models/integrations.py`` — the partial
# one-active-owner unique index.
_ACTIVE_OWNER_INDEX = "ix_integration_property_mappings_active_owner"


class MappingNotFoundError(LookupError):
    """Raised when a mapping is missing or not in the caller's workspace."""


class MappingProviderMismatchError(ValueError):
    """The mapping's provider differs from its connection's provider."""


class MappingPropertyNotOwnedError(ValueError):
    """The property does not resolve to one of the project's owned domains."""


class MappingActiveOwnerConflictError(RuntimeError):
    """An ACTIVE mapping already owns this (workspace, provider, property)."""


def property_ref_host(property_ref: str) -> str:
    """Resolve a provider property ref to its bare host for comparison.

    GSC domain properties (``sc-domain:example.com`` — the config-owned
    provider literal) strip the prefix; URL-prefix properties
    (``https://example.com/path``) and bare hosts go through the shared
    ``normalize_domain`` helper (lowercase, scheme/path/``www.`` stripped —
    invariant 2, one owner for host normalization).
    """
    text = property_ref.strip()
    if text.lower().startswith(GSC_DOMAIN_PROPERTY_PREFIX):
        text = text[len(GSC_DOMAIN_PROPERTY_PREFIX) :]
    return normalize_domain(text)


def _to_mapping_response(
    mapping: IntegrationPropertyMapping,
) -> IntegrationPropertyMappingResponse:
    return IntegrationPropertyMappingResponse(
        id=mapping.id,
        workspace_id=mapping.workspace_id,
        connection_id=mapping.connection_id,
        provider=mapping.provider,
        property_ref=mapping.property_ref,
        project_id=mapping.project_id,
        status=mapping.status,
        created_at=mapping.created_at,
        updated_at=mapping.updated_at,
    )


async def list_mappings(
    session: AsyncSession, *, workspace_id: uuid.UUID, connection_id: uuid.UUID
) -> list[IntegrationPropertyMappingResponse]:
    """All mappings (any status) for one workspace-verified connection."""
    connection = await get_connection(
        session, workspace_id=workspace_id, connection_id=connection_id
    )
    result = await session.execute(
        select(IntegrationPropertyMapping)
        .where(IntegrationPropertyMapping.connection_id == connection.id)
        .order_by(
            IntegrationPropertyMapping.created_at.asc(),
            IntegrationPropertyMapping.id.asc(),
        )
    )
    return [_to_mapping_response(mapping) for mapping in result.scalars()]


async def create_mapping(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
    provider: str,
    property_ref: str,
    project_id: uuid.UUID,
) -> IntegrationPropertyMappingResponse:
    """Validate + insert one ACTIVE mapping (commits on success).

    Raises:
        IntegrationConnectionNotFoundError: missing/cross-workspace
            connection (API: 404).
        ProjectNotFoundError: missing/cross-workspace project (API: 404).
        MappingProviderMismatchError: provider != connection's (API: 422).
        MappingPropertyNotOwnedError: property not an owned domain (API: 422).
        MappingActiveOwnerConflictError: active-owner slot taken (API: 409).
    """
    connection = await get_connection(
        session, workspace_id=workspace_id, connection_id=connection_id
    )
    if provider != connection.provider:
        raise MappingProviderMismatchError(
            f"mapping provider {provider!r} does not match "
            f"connection provider {connection.provider!r}"
        )
    # Same-workspace project binding (the projects table predates the
    # composite-FK pattern, so the binding is service-layer — spec §3).
    project = await get_project(
        session, workspace_id=workspace_id, project_id=project_id
    )
    host = property_ref_host(property_ref)
    owned_hosts = {normalize_domain(owned.domain) for owned in project.owned_domains}
    owned_hosts.discard("")
    if not host or host not in owned_hosts:
        raise MappingPropertyNotOwnedError(
            f"property {property_ref!r} does not resolve to an owned domain "
            f"of project {project_id}"
        )
    mapping = IntegrationPropertyMapping(
        workspace_id=workspace_id,
        connection_id=connection.id,
        provider=provider,
        property_ref=property_ref.strip(),
        project_id=project.id,
        status=MAPPING_STATUS_ACTIVE,
    )
    session.add(mapping)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if integrity_constraint_name(exc) == _ACTIVE_OWNER_INDEX:
            raise MappingActiveOwnerConflictError(
                f"an active mapping already owns {property_ref!r}"
            ) from exc
        raise
    return _to_mapping_response(mapping)


async def disable_mapping(
    session: AsyncSession, *, workspace_id: uuid.UUID, mapping_id: uuid.UUID
) -> None:
    """Disable a mapping — a status flip, never a row delete (spec §3).

    Freeing the ``(workspace, provider, property_ref)`` slot is exactly what
    the flip does: the partial unique index only covers ACTIVE rows, so a
    disabled mapping's property can be re-owned by a later create.
    """
    result = await session.execute(
        select(IntegrationPropertyMapping).where(
            IntegrationPropertyMapping.id == mapping_id,
            IntegrationPropertyMapping.workspace_id == workspace_id,
        )
    )
    mapping = result.scalar_one_or_none()
    if mapping is None:
        raise MappingNotFoundError(str(mapping_id))
    mapping.status = MAPPING_STATUS_DISABLED
    await session.commit()
