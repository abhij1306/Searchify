# Prompts router: prompt-set + prompt CRUD, CSV import, and /generate stub.
#
# Workspace-scoped through the parent project (invariant 5); the active
# workspace is resolved by ``require_active_workspace``. The MVP surface:
#   - GET/POST /prompt-sets, GET/PATCH/DELETE /prompt-sets/{id}
#   - GET/POST /prompt-sets/{id}/prompts, PATCH/DELETE /prompts/{id}
#   - POST /prompt-sets/{id}/import  -> MVP CSV bulk-create
#   - POST /prompt-sets/{id}/generate -> roadmap stub (501, B-4)
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import WorkspaceContext, get_db, require_active_workspace
from app.domain.prompts.csv_import import parse_prompt_csv
from app.domain.prompts.mappers import prompt_set_to_response, prompt_to_response
from app.domain.prompts.schemas import (
    PromptCreate,
    PromptImport,
    PromptInput,
    PromptResponse,
    PromptSetCreate,
    PromptSetResponse,
    PromptSetUpdate,
    PromptUpdate,
)
from app.domain.prompts.service import (
    PromptNotFoundError,
    PromptSetNotFoundError,
    create_prompt,
    create_prompt_set,
    delete_prompt,
    delete_prompt_set,
    get_prompt_set,
    import_prompts,
    list_prompt_sets,
    list_prompts,
    update_prompt,
    update_prompt_set,
)

router = APIRouter(tags=["prompts"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


# --------------------------------------------------------------------------
# Prompt sets
# --------------------------------------------------------------------------
@router.get("/prompt-sets", response_model=list[PromptSetResponse])
async def list_prompt_sets_endpoint(
    ctx: _WorkspaceDep,
    session: _SessionDep,
    project_id: uuid.UUID | None = None,
) -> list[PromptSetResponse]:
    sets = await list_prompt_sets(
        session, workspace_id=ctx.workspace_id, project_id=project_id
    )
    return [prompt_set_to_response(s) for s in sets]


@router.post(
    "/prompt-sets",
    response_model=PromptSetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt_set_endpoint(
    payload: PromptSetCreate, ctx: _WorkspaceDep, session: _SessionDep
) -> PromptSetResponse:
    try:
        prompt_set = await create_prompt_set(
            session, workspace_id=ctx.workspace_id, payload=payload
        )
    except PromptSetNotFoundError as exc:
        raise _not_found("Project not found") from exc
    return prompt_set_to_response(prompt_set)


@router.get("/prompt-sets/{prompt_set_id}", response_model=PromptSetResponse)
async def get_prompt_set_endpoint(
    prompt_set_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> PromptSetResponse:
    try:
        prompt_set = await get_prompt_set(
            session, workspace_id=ctx.workspace_id, prompt_set_id=prompt_set_id
        )
    except PromptSetNotFoundError as exc:
        raise _not_found("Prompt set not found") from exc
    return prompt_set_to_response(prompt_set)


@router.patch("/prompt-sets/{prompt_set_id}", response_model=PromptSetResponse)
async def update_prompt_set_endpoint(
    prompt_set_id: uuid.UUID,
    payload: PromptSetUpdate,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> PromptSetResponse:
    try:
        prompt_set = await update_prompt_set(
            session,
            workspace_id=ctx.workspace_id,
            prompt_set_id=prompt_set_id,
            payload=payload,
        )
    except PromptSetNotFoundError as exc:
        raise _not_found("Prompt set not found") from exc
    return prompt_set_to_response(prompt_set)


@router.delete(
    "/prompt-sets/{prompt_set_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_prompt_set_endpoint(
    prompt_set_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> None:
    try:
        await delete_prompt_set(
            session, workspace_id=ctx.workspace_id, prompt_set_id=prompt_set_id
        )
    except PromptSetNotFoundError as exc:
        raise _not_found("Prompt set not found") from exc


# --------------------------------------------------------------------------
# Prompts within a set
# --------------------------------------------------------------------------
@router.get(
    "/prompt-sets/{prompt_set_id}/prompts",
    response_model=list[PromptResponse],
)
async def list_prompts_endpoint(
    prompt_set_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> list[PromptResponse]:
    try:
        prompts = await list_prompts(
            session, workspace_id=ctx.workspace_id, prompt_set_id=prompt_set_id
        )
    except PromptSetNotFoundError as exc:
        raise _not_found("Prompt set not found") from exc
    return [prompt_to_response(p) for p in prompts]


@router.post(
    "/prompt-sets/{prompt_set_id}/prompts",
    response_model=PromptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt_endpoint(
    prompt_set_id: uuid.UUID,
    payload: PromptInput,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> PromptResponse:
    create = PromptCreate(prompt_set_id=prompt_set_id, **payload.model_dump())
    try:
        prompt = await create_prompt(
            session, workspace_id=ctx.workspace_id, payload=create
        )
    except PromptSetNotFoundError as exc:
        raise _not_found("Prompt set not found") from exc
    return prompt_to_response(prompt)


@router.patch("/prompts/{prompt_id}", response_model=PromptResponse)
async def update_prompt_endpoint(
    prompt_id: uuid.UUID,
    payload: PromptUpdate,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> PromptResponse:
    try:
        prompt = await update_prompt(
            session,
            workspace_id=ctx.workspace_id,
            prompt_id=prompt_id,
            payload=payload,
        )
    except PromptNotFoundError as exc:
        raise _not_found("Prompt not found") from exc
    return prompt_to_response(prompt)


@router.delete("/prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt_endpoint(
    prompt_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> None:
    try:
        await delete_prompt(
            session, workspace_id=ctx.workspace_id, prompt_id=prompt_id
        )
    except PromptNotFoundError as exc:
        raise _not_found("Prompt not found") from exc


# --------------------------------------------------------------------------
# CSV import (MVP bulk-create) + /generate stub (roadmap, B-4)
# --------------------------------------------------------------------------
async def _resolve_import_rows(
    request: Request, file: UploadFile | None
) -> list[PromptInput]:
    """Accept either a multipart CSV upload or a JSON body of parsed rows.

    The committed frontend contract posts a CSV ``File`` (multipart); a future
    browser-parsed path may post ``{"prompts": [...]}`` JSON instead. Both
    converge to a list of ``PromptInput`` for the service.
    """
    if file is not None:
        raw = (await file.read()).decode("utf-8-sig", errors="replace")
        return parse_prompt_csv(raw)

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        return PromptImport.model_validate(body).prompts

    # Raw CSV posted as text/csv (no multipart wrapper).
    raw_body = (await request.body()).decode("utf-8-sig", errors="replace")
    return parse_prompt_csv(raw_body)


@router.post(
    "/prompt-sets/{prompt_set_id}/import",
    response_model=PromptSetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_prompts_endpoint(
    prompt_set_id: uuid.UUID,
    request: Request,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    file: UploadFile | None = None,
) -> PromptSetResponse:
    rows = await _resolve_import_rows(request, file)
    try:
        prompt_set = await import_prompts(
            session,
            workspace_id=ctx.workspace_id,
            prompt_set_id=prompt_set_id,
            rows=rows,
        )
    except PromptSetNotFoundError as exc:
        raise _not_found("Prompt set not found") from exc
    return prompt_set_to_response(prompt_set)


@router.post("/prompt-sets/{prompt_set_id}/generate")
async def generate_prompts_endpoint(
    prompt_set_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> None:
    """AI-suggested prompt generation — roadmap (B-4).

    Returns 501 with a structured ``not_implemented`` code so the UI can show a
    coming-soon state. The prompt-set is validated for workspace scope first so
    an unauthorized caller still gets a 404, not a 501.
    """
    try:
        await get_prompt_set(
            session, workspace_id=ctx.workspace_id, prompt_set_id=prompt_set_id
        )
    except PromptSetNotFoundError as exc:
        raise _not_found("Prompt set not found") from exc
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "not_implemented",
            "message": (
                "AI-suggested prompt generation is on the roadmap and not "
                "available at MVP."
            ),
        },
    )
