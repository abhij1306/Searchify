# Projects router: workspace-scoped CRUD (invariant 5).
#
# The MVP API surface is flat (no workspace_id in the path); the active
# workspace is resolved by ``require_active_workspace`` from the
# ``X-Workspace-Id`` header (or the caller's default workspace). Every query
# filters by that workspace. ``/projects/{id}/visibility`` is added in B6.
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import WorkspaceContext, get_db, require_active_workspace
from app.domain.projects.schemas import (
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
)
from app.domain.projects.service import (
    ProjectNotFoundError,
    create_project,
    delete_project,
    get_project,
    list_projects,
    project_to_response,
    update_project,
)

router = APIRouter(prefix="/projects", tags=["projects"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=list[ProjectResponse])
async def list_projects_endpoint(
    ctx: _WorkspaceDep, session: _SessionDep
) -> list[ProjectResponse]:
    projects = await list_projects(session, workspace_id=ctx.workspace_id)
    return [project_to_response(p) for p in projects]


@router.post(
    "", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED
)
async def create_project_endpoint(
    payload: ProjectCreate, ctx: _WorkspaceDep, session: _SessionDep
) -> ProjectResponse:
    project = await create_project(
        session, workspace_id=ctx.workspace_id, payload=payload
    )
    return project_to_response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project_endpoint(
    project_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> ProjectResponse:
    try:
        project = await get_project(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    return project_to_response(project)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project_endpoint(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> ProjectResponse:
    try:
        project = await update_project(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            payload=payload,
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
    return project_to_response(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_endpoint(
    project_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> None:
    try:
        await delete_project(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        ) from exc
