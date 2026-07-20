# Content router: workspace-scoped AI content generation (invariant 5).
#
# Flat surface under /api/v1/content; the active workspace comes from
# ``require_active_workspace``. Every handler filters by that workspace, so a
# record in another workspace is a 404, never a 403.
#
#   GET  /content/generations?project_id=&limit=  -> bounded history list
#   POST /content/generations                     -> enqueue (Idempotency-Key)
#   GET  /content/generations/{id}                -> full detail
#   POST /content/generations/{id}/regenerate     -> new record, fresh context
#   POST /content/generations/{id}/try-again      -> new record, frozen context
#   POST /content/generations/{id}/cancel         -> cooperative cancel
#
# The provider API key is env-driven and worker-resolved; it never enters
# this surface (invariant 6).
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import WorkspaceContext, get_db, require_active_workspace
from app.core.config.content import (
    CONTENT_LIST_DEFAULT_LIMIT,
    CONTENT_LIST_MAX_LIMIT,
    ERROR_CANCEL_NOT_ALLOWED,
    ERROR_IDEMPOTENCY_CONFLICT,
    ERROR_PROVIDER_NOT_CONFIGURED,
)
from app.domain.content.schemas import (
    ContentGenerationCreate,
    ContentGenerationDetail,
    ContentGenerationListItem,
)
from app.domain.content.service import (
    CancelNotAllowedError,
    ContentGenerationNotFoundError,
    IdempotencyConflictError,
    ProviderNotConfiguredError,
    cancel_generation,
    enqueue_generation,
    get_generation,
    list_generations,
    regenerate,
    to_detail,
    to_list_item,
    try_again,
)

router = APIRouter(prefix="/content", tags=["content"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/generations", response_model=list[ContentGenerationListItem])
async def list_generations_endpoint(
    ctx: _WorkspaceDep,
    session: _SessionDep,
    project_id: Annotated[uuid.UUID, Query()],
    limit: Annotated[
        int, Query(ge=1, le=CONTENT_LIST_MAX_LIMIT)
    ] = CONTENT_LIST_DEFAULT_LIMIT,
) -> list[ContentGenerationListItem]:
    try:
        rows = await list_generations(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            limit=limit,
        )
    except ContentGenerationNotFoundError as exc:
        raise _not_found(exc) from exc
    return [to_list_item(row) for row in rows]


@router.post(
    "/generations",
    response_model=ContentGenerationDetail,
    status_code=status.HTTP_201_CREATED,
)
async def enqueue_generation_endpoint(
    payload: ContentGenerationCreate,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ContentGenerationDetail:
    try:
        row, _created = await enqueue_generation(
            session,
            workspace_id=ctx.workspace_id,
            project_id=payload.project_id,
            prompt=payload.prompt,
            output_type=payload.output_type,
            website_context_enabled=payload.website_context_enabled,
            idempotency_key=(idempotency_key or "").strip(),
        )
    except ContentGenerationNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProviderNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ERROR_PROVIDER_NOT_CONFIGURED,
        ) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ERROR_IDEMPOTENCY_CONFLICT,
        ) from exc
    return to_detail(row)


@router.get("/generations/{generation_id}", response_model=ContentGenerationDetail)
async def get_generation_endpoint(
    generation_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> ContentGenerationDetail:
    try:
        row = await get_generation(
            session,
            workspace_id=ctx.workspace_id,
            generation_id=generation_id,
        )
    except ContentGenerationNotFoundError as exc:
        raise _not_found(exc) from exc
    return to_detail(row)


@router.post(
    "/generations/{generation_id}/regenerate",
    response_model=ContentGenerationDetail,
    status_code=status.HTTP_201_CREATED,
)
async def regenerate_endpoint(
    generation_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> ContentGenerationDetail:
    try:
        row = await regenerate(
            session,
            workspace_id=ctx.workspace_id,
            generation_id=generation_id,
        )
    except ContentGenerationNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProviderNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ERROR_PROVIDER_NOT_CONFIGURED,
        ) from exc
    return to_detail(row)


@router.post(
    "/generations/{generation_id}/try-again",
    response_model=ContentGenerationDetail,
    status_code=status.HTTP_201_CREATED,
)
async def try_again_endpoint(
    generation_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> ContentGenerationDetail:
    try:
        row = await try_again(
            session,
            workspace_id=ctx.workspace_id,
            generation_id=generation_id,
        )
    except ContentGenerationNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProviderNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ERROR_PROVIDER_NOT_CONFIGURED,
        ) from exc
    return to_detail(row)


@router.post(
    "/generations/{generation_id}/cancel",
    response_model=ContentGenerationDetail,
)
async def cancel_generation_endpoint(
    generation_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> ContentGenerationDetail:
    try:
        row = await cancel_generation(
            session,
            workspace_id=ctx.workspace_id,
            generation_id=generation_id,
        )
    except ContentGenerationNotFoundError as exc:
        raise _not_found(exc) from exc
    except CancelNotAllowedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ERROR_CANCEL_NOT_ALLOWED,
        ) from exc
    return to_detail(row)
