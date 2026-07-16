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
    SELECTION_SOURCE_FREE_SAMPLE,
    SELECTION_SOURCE_USER,
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


async def lock_entitlement(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    default_capability: str = DEFAULT_SITE_HEALTH_CAPABILITY,
) -> WorkspaceSiteHealthEntitlement:
    """Resolve then lock the workspace entitlement row ``FOR UPDATE``.

    This is THE quota serialization point (subplan Persistence contract): every
    monitored-set replacement across every project in the workspace must lock
    this single row before counting active rows, so two concurrent updates are
    ordered and neither can push the workspace above its ``monitored_url_limit``
    (subplan Acceptance criteria 2). Seeds a Free row first if the workspace has
    none, then re-selects it ``with_for_update`` so the lock is held for the
    caller's transaction.
    """
    await resolve_entitlement(
        session, workspace_id, default_capability=default_capability
    )
    result = await session.execute(
        select(WorkspaceSiteHealthEntitlement)
        .where(WorkspaceSiteHealthEntitlement.workspace_id == workspace_id)
        .with_for_update()
    )
    return result.scalar_one()


def entitlement_allows_monitored_analysis(
    entitlement: WorkspaceSiteHealthEntitlement | None,
    *,
    selection_source: str = SELECTION_SOURCE_USER,
) -> bool:
    """Pure guard: may this entitlement analyze a row of ``selection_source``?

    Used both by the selection mutation (block Free from user selection) and by
    the worker guard before I/O / before evidence persistence (a downgrade must
    block NEW user-managed analysis work while preserving existing evidence).

    - A capability that allows user selection (Starter) may analyze any row.
    - A capability that does not (Free) may still analyze its own system-managed
      ``free_sample`` rows, but never a ``user`` row — so a Starter->Free
      downgrade stops new user-source work without deleting anything.
    - A missing entitlement fails closed (no analysis).
    """
    if entitlement is None:
        return False
    profile = capability_profile(entitlement.plan_key)
    if profile.allows_user_selection:
        return True
    return selection_source == SELECTION_SOURCE_FREE_SAMPLE
