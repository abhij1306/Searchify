# Site Health workspace entitlement domain service (capability-based).
#
# A workspace's Site Health entitlement is a single row
# (``WorkspaceSiteHealthEntitlement``) keyed by capability (``free`` /
# ``starter``), never by a marketing plan display name. This module owns the
# two operations Task 1 needs:
#
#   - ``resolve_entitlement`` — read the workspace's entitlement, seeding a Free
#     row on first use (fail-closed to the most restrictive capability). This is
#     the row later locked ``FOR UPDATE`` to serialize the workspace-wide
#     monitored-URL quota.
#   - ``set_entitlement`` — assign a capability to a workspace, freezing the
#     resolved capability profile's discovery mode / caps / limits /
#     count-disclosure flag onto the row and bumping the capability revision.
#
# Billing-provider integration is intentionally out of scope: production billing
# may call ``set_entitlement`` later, but this domain never knows about it.
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.site_health import (
    DEFAULT_SITE_HEALTH_CAPABILITY,
    capability_profile,
    normalize_capability,
)
from app.models.site_health import WorkspaceSiteHealthEntitlement


def _apply_profile(
    row: WorkspaceSiteHealthEntitlement, capability: str
) -> WorkspaceSiteHealthEntitlement:
    """Freeze the resolved capability profile onto an entitlement row."""
    profile = capability_profile(capability)
    row.plan_key = profile.capability
    row.discovery_mode = profile.discovery_mode
    row.discovery_url_cap = profile.discovery_url_cap
    row.sample_url_limit = profile.sample_url_limit
    row.monitored_url_limit = profile.monitored_url_limit
    row.count_disclosure = profile.count_disclosure
    return row


async def _load_entitlement(
    session: AsyncSession, workspace_id: uuid.UUID
) -> WorkspaceSiteHealthEntitlement | None:
    result = await session.execute(
        select(WorkspaceSiteHealthEntitlement).where(
            WorkspaceSiteHealthEntitlement.workspace_id == workspace_id
        )
    )
    return result.scalar_one_or_none()


async def resolve_entitlement(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    default_capability: str = DEFAULT_SITE_HEALTH_CAPABILITY,
) -> WorkspaceSiteHealthEntitlement:
    """Return the workspace's entitlement, seeding a default (Free) row if none.

    Fail-closed: a workspace with no explicit entitlement resolves to the most
    restrictive capability (Free). The seeded row is flushed so it can be locked
    ``FOR UPDATE`` for a subsequent quota check in the same transaction.
    """
    existing = await _load_entitlement(session, workspace_id)
    if existing is not None:
        return existing

    row = WorkspaceSiteHealthEntitlement(workspace_id=workspace_id)
    _apply_profile(row, normalize_capability(default_capability))
    session.add(row)
    await session.flush()
    return row


async def set_entitlement(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    capability: str,
) -> WorkspaceSiteHealthEntitlement:
    """Assign ``capability`` to the workspace, freezing its capability profile.

    Creates the entitlement row if missing, otherwise updates it in place and
    bumps ``capability_revision``. The value is normalized to a known capability
    key (unknown/missing coerces to Free). Returns the flushed row.
    """
    normalized = normalize_capability(capability)
    row = await _load_entitlement(session, workspace_id)
    if row is None:
        row = WorkspaceSiteHealthEntitlement(workspace_id=workspace_id)
        _apply_profile(row, normalized)
        session.add(row)
    else:
        _apply_profile(row, normalized)
        row.capability_revision = row.capability_revision + 1
    await session.flush()
    return row
