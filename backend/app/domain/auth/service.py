# Authentication + registration service.
#
# WORKSPACE-scoped auth model: registration and first login both ensure the account has a personal
# workspace and a membership row (invariant 5 — access is via membership, not
# a user-id shortcut).
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, hash_password, verify_password
from app.domain.workspaces.service import ensure_personal_workspace
from app.models.user import User

logger = logging.getLogger("app.auth")


class EmailAlreadyRegisteredError(ValueError):
    """Raised when registering an email that already exists."""


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def register_user(
    session: AsyncSession, email: str, password: str, role: str = "user"
) -> User:
    """Create a user, then auto-create a workspace + membership for them.

    Commits once so the user, workspace, and membership land atomically.
    """
    if await get_user_by_email(session, email) is not None:
        raise EmailAlreadyRegisteredError("Email already registered")
    user = User(
        email=email.lower(),
        hashed_password=hash_password(password),
        role=role,
    )
    session.add(user)
    await session.flush()
    await ensure_personal_workspace(session, user)
    await session.commit()
    await session.refresh(user)
    logger.info("auth.registered", extra={"user_id": str(user.id)})
    return user


async def authenticate_user(
    session: AsyncSession, email: str, password: str
) -> tuple[str, User] | None:
    """Verify credentials and mint an access token.

    Also auto-creates a workspace + membership on first login for any account
    that does not yet have one (per the B2 acceptance: "workspace auto-created
    on first login").
    """
    user = await get_user_by_email(session, email)
    if (
        user is None
        or not user.is_active
        or not verify_password(password, user.hashed_password)
    ):
        return None
    created = await ensure_personal_workspace(session, user)
    if created is not None:
        await session.commit()
        logger.info(
            "auth.workspace_autocreated",
            extra={"user_id": str(user.id), "workspace_id": str(created.id)},
        )
    return create_access_token(str(user.id)), user
