# Prompts router: prompt-set + prompt CRUD, CSV import, topics, /generate.
#
# Workspace-scoped through the parent project (invariant 5); the active
# workspace is resolved by ``require_active_workspace``. The surface:
#   - GET/POST /prompt-sets, GET/PATCH/DELETE /prompt-sets/{id}
#   - GET/POST /prompt-sets/{id}/prompts, PATCH/DELETE /prompts/{id}
#   - POST /prompt-sets/{id}/import  -> MVP CSV bulk-create
#   - POST /prompt-sets/{id}/generate -> AI topic/prompt generation
#     (default agent, config/agent.py; suggestions land as status='proposed')
#   - POST /prompt-sets/{id}/prompts/bulk-status -> review transitions
#   - GET/POST /projects/{id}/topics, PATCH/DELETE /topics/{id}
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
from app.connectors.agent.client import AgentNotConfiguredError, DefaultAgentClient
from app.connectors.answer_engines.errors import ProviderError
from app.domain.prompts.csv_import import parse_prompt_csv
from app.domain.prompts.generation import (
    GenerationOutputError,
    GenerationValidationError,
    generate_prompts,
    validate_generation_request,
)
from app.domain.prompts.mappers import (
    prompt_set_to_response,
    prompt_to_response,
    topic_to_response,
)
from app.domain.prompts.schemas import (
    PromptBulkStatusRequest,
    PromptCreate,
    PromptGenerateRequest,
    PromptGenerateResponse,
    PromptImport,
    PromptInput,
    PromptResponse,
    PromptSetCreate,
    PromptSetResponse,
    PromptSetUpdate,
    PromptUpdate,
    TopicCreate,
    TopicResponse,
    TopicUpdate,
)
from app.domain.prompts.service import (
    DuplicatePromptError,
    PromptNotFoundError,
    PromptSetNotFoundError,
    bulk_set_status,
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
from app.domain.prompts.topics import (
    DuplicateTopicError,
    TopicNotFoundError,
    create_topic,
    delete_topic,
    list_topics,
    topic_status_counts,
    update_topic,
)

router = APIRouter(tags=["prompts"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


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


@router.delete("/prompt-sets/{prompt_set_id}", status_code=status.HTTP_204_NO_CONTENT)
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
    except DuplicatePromptError as exc:
        raise _conflict(str(exc)) from exc
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
    except DuplicatePromptError as exc:
        raise _conflict(str(exc)) from exc
    return prompt_to_response(prompt)


@router.delete("/prompts/{prompt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_prompt_endpoint(
    prompt_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> None:
    try:
        await delete_prompt(session, workspace_id=ctx.workspace_id, prompt_id=prompt_id)
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


@router.post(
    "/prompt-sets/{prompt_set_id}/generate",
    response_model=PromptGenerateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_prompts_endpoint(
    prompt_set_id: uuid.UUID,
    payload: PromptGenerateRequest,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> PromptGenerateResponse:
    """AI topic/prompt generation via the app-level default agent.

    Guard order: workspace scope (foreign set -> 404) before anything runs,
    then confirmation/bounds/topic ownership (422), then agent configuration
    (503) — an invalid payload is rejected as invalid even when no agent is
    configured, and the backend enforces ``confirm_send_evidence``, never
    just the UI. Suggestions land as ``status='proposed'`` and are
    audit-ineligible until a human accepts them.
    """
    try:
        prompt_set = await validate_generation_request(
            session,
            workspace_id=ctx.workspace_id,
            prompt_set_id=prompt_set_id,
            payload=payload,
        )
    except PromptSetNotFoundError as exc:
        raise _not_found("Prompt set not found") from exc
    except GenerationValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "generation_invalid", "message": str(exc)},
        ) from exc
    try:
        agent = DefaultAgentClient()
    except AgentNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "agent_not_configured",
                "message": (
                    "No default agent is configured. Set DEFAULT_AGENT_API_KEY "
                    "(or MISTRALAI_API_KEY) in the backend environment."
                ),
            },
        ) from exc
    try:
        generated, topics, dropped = await generate_prompts(
            session,
            workspace_id=ctx.workspace_id,
            prompt_set_id=prompt_set_id,
            payload=payload,
            agent=agent,
            prompt_set=prompt_set,
        )
    except PromptSetNotFoundError as exc:
        raise _not_found("Prompt set not found") from exc
    except GenerationValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "generation_invalid", "message": str(exc)},
        ) from exc
    except GenerationOutputError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "generation_unparseable", "message": str(exc)},
        ) from exc
    except ProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "agent_call_failed", "message": str(exc)},
        ) from exc
    counts = (
        await topic_status_counts(session, project_id=topics[0].project_id)
        if topics
        else {}
    )
    return PromptGenerateResponse(
        generated=[prompt_to_response(p) for p in generated],
        topics=[topic_to_response(t, counts) for t in topics],
        dropped_duplicates=dropped,
    )


@router.post(
    "/prompt-sets/{prompt_set_id}/prompts/bulk-status",
    response_model=PromptSetResponse,
)
async def bulk_status_endpoint(
    prompt_set_id: uuid.UUID,
    payload: PromptBulkStatusRequest,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> PromptSetResponse:
    """Bulk review transition (accept-all / archive-selected)."""
    try:
        prompt_set = await bulk_set_status(
            session,
            workspace_id=ctx.workspace_id,
            prompt_set_id=prompt_set_id,
            prompt_ids=payload.prompt_ids,
            status=payload.status,
        )
    except PromptSetNotFoundError as exc:
        raise _not_found("Prompt set not found") from exc
    except PromptNotFoundError as exc:
        raise _not_found(str(exc)) from exc
    return prompt_set_to_response(prompt_set)


# --------------------------------------------------------------------------
# Topics
# --------------------------------------------------------------------------
@router.get("/projects/{project_id}/topics", response_model=list[TopicResponse])
async def list_topics_endpoint(
    project_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> list[TopicResponse]:
    try:
        topics = await list_topics(
            session, workspace_id=ctx.workspace_id, project_id=project_id
        )
    except TopicNotFoundError as exc:
        raise _not_found("Project not found") from exc
    counts = await topic_status_counts(session, project_id=project_id)
    return [topic_to_response(t, counts) for t in topics]


@router.post(
    "/projects/{project_id}/topics",
    response_model=TopicResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_topic_endpoint(
    project_id: uuid.UUID,
    payload: TopicCreate,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> TopicResponse:
    try:
        topic = await create_topic(
            session,
            workspace_id=ctx.workspace_id,
            project_id=project_id,
            payload=payload,
        )
    except TopicNotFoundError as exc:
        raise _not_found("Project not found") from exc
    except DuplicateTopicError as exc:
        raise _conflict(str(exc)) from exc
    return topic_to_response(topic)


@router.patch("/topics/{topic_id}", response_model=TopicResponse)
async def update_topic_endpoint(
    topic_id: uuid.UUID,
    payload: TopicUpdate,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> TopicResponse:
    try:
        topic = await update_topic(
            session,
            workspace_id=ctx.workspace_id,
            topic_id=topic_id,
            payload=payload,
        )
    except TopicNotFoundError as exc:
        raise _not_found("Topic not found") from exc
    except DuplicateTopicError as exc:
        raise _conflict(str(exc)) from exc
    counts = await topic_status_counts(session, project_id=topic.project_id)
    return topic_to_response(topic, counts)


@router.delete("/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic_endpoint(
    topic_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> None:
    try:
        await delete_topic(session, workspace_id=ctx.workspace_id, topic_id=topic_id)
    except TopicNotFoundError as exc:
        raise _not_found("Topic not found") from exc
