# FastAPI dependencies: current user + workspace membership (invariant 5).
#
# EVERY project-owned read/write must resolve access through
# ``require_workspace_member`` so it is scoped by ``workspace_id`` and a
# verified membership row — never by ``user_id`` alone and never via an
# "admin" shortcut. This module establishes that pattern for B3–B6.
from __future__ import annotations

import uuid

from fastapi import Cookie, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.security import TokenDecodeError, decode_access_token
from app.domain.workspaces.service import get_membership
from app.models.user import User
from app.models.workspace import WorkspaceMember


async def get_db(
    session: AsyncSession = Depends(get_session),  # noqa: B008 - FastAPI injects via defaults.
) -> AsyncSession:
    return session


def _session_cookie(
    # Cookie name comes from config (invariant 1), resolved via alias.
    session_token: str | None = Cookie(
        default=None, alias=settings.session_cookie_name
    ),
) -> str | None:
    return session_token


async def get_current_user(
    session_token: str | None = Depends(_session_cookie),
    session: AsyncSession = Depends(get_db),  # noqa: B008 - FastAPI injects via defaults.
) -> User:
    """Resolve the authenticated user from the HttpOnly session cookie."""
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    try:
        payload = decode_access_token(session_token)
        user_id = uuid.UUID(str(payload["sub"]))
    except (TokenDecodeError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user"
        )
    return user


class WorkspaceContext:
    """The resolved (user, membership) pair for a workspace-scoped request.

    Handlers read ``workspace_id`` from here to filter every downstream query.
    """

    __slots__ = ("user", "member")

    def __init__(self, user: User, member: WorkspaceMember) -> None:
        self.user = user
        self.member = member

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.member.workspace_id


async def require_workspace_member(
    workspace_id: uuid.UUID = Path(...),  # noqa: B008 - FastAPI injects via defaults.
    user: User = Depends(get_current_user),  # noqa: B008 - FastAPI injects via defaults.
    session: AsyncSession = Depends(get_db),  # noqa: B008 - FastAPI injects via defaults.
) -> WorkspaceContext:
    """Authorize the current user for the path ``workspace_id``.

    Returns a ``WorkspaceContext`` when the user is a member; otherwise 404 so
    a non-member cannot even distinguish an existing workspace from a missing
    one (invariant 5 — cross-workspace access returns 403/404, not data).
    """
    member = await get_membership(session, workspace_id, user.id)
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found"
        )
    return WorkspaceContext(user=user, member=member)
