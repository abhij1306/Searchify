"""Unit tests for the workspace-auth dependency (invariant 5).

A member is authorized; a non-member and an unauthenticated caller are
rejected. This is the single gate every downstream query relies on.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_workspace_member
from app.core.security import create_access_token
from app.domain.auth.service import register_user
from app.domain.workspaces.service import (
    create_workspace,
    get_membership,
    list_workspaces_for_user,
)


async def _register(session: AsyncSession, email: str):
    return await register_user(session, email, "password123")


@pytest.mark.asyncio
async def test_member_is_authorized(db_session: AsyncSession) -> None:
    user = await _register(db_session, "member@example.com")
    workspaces = await list_workspaces_for_user(db_session, user)
    workspace, _member = workspaces[0]

    ctx = await require_workspace_member(
        workspace_id=workspace.id, user=user, session=db_session
    )
    assert ctx.user.id == user.id
    assert ctx.workspace_id == workspace.id
    assert ctx.member.role == "owner"


@pytest.mark.asyncio
async def test_non_member_is_rejected_with_404(db_session: AsyncSession) -> None:
    owner = await _register(db_session, "owner@example.com")
    outsider = await _register(db_session, "outsider@example.com")
    owner_ws, _ = (await list_workspaces_for_user(db_session, owner))[0]

    with pytest.raises(HTTPException) as exc:
        await require_workspace_member(
            workspace_id=owner_ws.id, user=outsider, session=db_session
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_workspace_is_rejected(db_session: AsyncSession) -> None:
    user = await _register(db_session, "ghost@example.com")
    with pytest.raises(HTTPException) as exc:
        await require_workspace_member(
            workspace_id=uuid.uuid4(), user=user, session=db_session
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_membership_returns_none_for_non_member(
    db_session: AsyncSession,
) -> None:
    owner = await _register(db_session, "o2@example.com")
    other = await _register(db_session, "u2@example.com")
    ws, _ = await create_workspace(db_session, owner, "Team")

    assert await get_membership(db_session, ws.id, owner.id) is not None
    assert await get_membership(db_session, ws.id, other.id) is None


def test_unauthenticated_current_user_rejected() -> None:
    """A missing session cookie yields 401 from get_current_user."""
    import asyncio

    from app.api.deps import get_current_user

    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user(session_token=None, session=None))
    assert exc.value.status_code == 401


def test_valid_token_shape() -> None:
    token = create_access_token(str(uuid.uuid4()))
    assert isinstance(token, str) and token.count(".") == 2
