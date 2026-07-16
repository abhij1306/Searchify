# Workspace + membership service (workspace-scoped, invariant 5).
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember

# Roles a member can hold within a workspace. The creator is the owner.
WORKSPACE_ROLE_OWNER = "owner"
WORKSPACE_ROLE_MEMBER = "member"


def _default_workspace_name(user: User) -> str:
    local_part = (user.email or "").split("@", 1)[0].strip()
    label = local_part or "My"
    return f"{label}'s Workspace"


async def get_membership(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> WorkspaceMember | None:
    """Resolve the caller's membership row for a workspace, or None.

    This is the single source of truth used by ``require_workspace_member``;
    a missing row means no access (403/404), never a user-id fallback.
    """
    result = await session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def list_workspaces_for_user(
    session: AsyncSession, user: User
) -> list[tuple[Workspace, WorkspaceMember]]:
    """Return the workspaces the user is a member of, with their membership."""
    result = await session.execute(
        select(Workspace, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at.asc())
    )
    return [tuple(row) for row in result.all()]


async def create_workspace(
    session: AsyncSession, user: User, name: str
) -> tuple[Workspace, WorkspaceMember]:
    """Create a workspace and add ``user`` as its owner."""
    workspace = Workspace(name=name)
    session.add(workspace)
    await session.flush()
    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WORKSPACE_ROLE_OWNER,
    )
    session.add(member)
    await session.commit()
    await session.refresh(workspace)
    await session.refresh(member)
    return workspace, member


async def ensure_personal_workspace(
    session: AsyncSession, user: User
) -> Workspace | None:
    """Auto-create a personal workspace + owner membership if the user has none.

    Returns the newly created workspace, or ``None`` if the user was already a
    member of at least one workspace. Flushes but does not commit — the caller
    owns the transaction boundary.
    """
    existing = await session.execute(
        select(WorkspaceMember.id).where(WorkspaceMember.user_id == user.id).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return None
    workspace = Workspace(name=_default_workspace_name(user))
    session.add(workspace)
    await session.flush()
    member = WorkspaceMember(
        workspace_id=workspace.id,
        user_id=user.id,
        role=WORKSPACE_ROLE_OWNER,
    )
    session.add(member)
    await session.flush()
    return workspace
