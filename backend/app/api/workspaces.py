# Workspaces router: list the caller's workspaces + create a new one.
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.domain.workspaces.schemas import WorkspaceCreate, WorkspaceResponse
from app.domain.workspaces.service import create_workspace, list_workspaces_for_user
from app.models.user import User

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[WorkspaceResponse]:
    rows = await list_workspaces_for_user(session, user)
    return [
        WorkspaceResponse(
            id=workspace.id,
            name=workspace.name,
            role=member.role,
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
        )
        for workspace, member in rows
    ]


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace_endpoint(
    payload: WorkspaceCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> WorkspaceResponse:
    workspace, member = await create_workspace(session, user, payload.name)
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        role=member.role,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
    )
