# Audits router: workspace-scoped audit lifecycle + executions + SSE (invariant 5).
#
# Flat API surface (no workspace_id in the path); the active workspace is
# resolved by ``require_active_workspace`` from the ``X-Workspace-Id`` header
# (or the caller's default workspace). Every query filters by that workspace.
#
#   POST /audits                -> create + enqueue an audit (deterministic)
#   GET  /audits                -> list the workspace's audits
#   GET  /audits/{id}           -> one audit (with engine provenance)
#   POST /audits/{id}/cancel    -> cooperative cancel
#   GET  /audits/{id}/executions-> the audit's execution/queue rows
#   GET  /audits/{id}/events    -> lifecycle events (SSE stream)
#
# Provider keys are NEVER carried here — the worker resolves the decrypted key
# from the workspace's ``ProviderConnection`` at execution time (invariant 6).
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.exports import audit_to_csv, audit_to_markdown
from app.api.deps import WorkspaceContext, get_db, require_active_workspace
from app.core.config.audits import AUDIT_TERMINAL_STATUSES
from app.core.database import SessionLocal
from app.core.http_errors import raise_not_found
from app.domain.analysis.schemas import MetricsResponse
from app.domain.analysis.service import (
    AnalysisNotFoundError,
    get_metrics,
    load_export_bundle,
)
from app.domain.audits.planner import (
    AuditNotFoundError,
    AuditValidationError,
    cancel_audit,
    create_audit,
    get_audit,
    list_audits,
    list_tasks,
)
from app.domain.audits.schemas import (
    AuditCreate,
    AuditEventResponse,
    AuditResponse,
    AuditTaskResponse,
)
from app.models.audit import Audit, AuditEvent

router = APIRouter(prefix="/audits", tags=["audits"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]

# How often the SSE loop polls for new events, and the idle cutoff after which
# it stops streaming a terminal audit.
_SSE_POLL_SECONDS = 1.0
_SSE_TERMINAL_GRACE_POLLS = 2


@router.post("", response_model=AuditResponse, status_code=status.HTTP_201_CREATED)
async def create_audit_endpoint(
    payload: AuditCreate, ctx: _WorkspaceDep, session: _SessionDep
) -> AuditResponse:
    try:
        audit = await create_audit(
            session,
            workspace_id=ctx.workspace_id,
            project_id=payload.project_id,
            engines=payload.engines,
            prompt_set_id=payload.prompt_set_id,
            prompt_ids=payload.prompt_ids,
            repetitions=payload.repetitions,
            benchmark_mode=payload.benchmark_mode,
            random_seed=payload.random_seed,
        )
    except AuditValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return AuditResponse.model_validate(audit)


@router.get("", response_model=list[AuditResponse])
async def list_audits_endpoint(
    ctx: _WorkspaceDep,
    session: _SessionDep,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditResponse]:
    audits = await list_audits(
        session,
        workspace_id=ctx.workspace_id,
        project_id=project_id,
        limit=limit,
    )
    return [AuditResponse.model_validate(a) for a in audits]


@router.get("/{audit_id}", response_model=AuditResponse)
async def get_audit_endpoint(
    audit_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> AuditResponse:
    audit = await _get_or_404(session, ctx.workspace_id, audit_id)
    return AuditResponse.model_validate(audit)


@router.post("/{audit_id}/cancel", response_model=AuditResponse)
async def cancel_audit_endpoint(
    audit_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> AuditResponse:
    try:
        audit = await cancel_audit(
            session, workspace_id=ctx.workspace_id, audit_id=audit_id
        )
    except AuditNotFoundError as exc:
        raise_not_found("Audit", cause=exc)
    except AuditValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return AuditResponse.model_validate(audit)


@router.get("/{audit_id}/executions", response_model=list[AuditTaskResponse])
async def list_executions_endpoint(
    audit_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> list[AuditTaskResponse]:
    try:
        tasks = await list_tasks(
            session, workspace_id=ctx.workspace_id, audit_id=audit_id
        )
    except AuditNotFoundError as exc:
        raise_not_found("Audit", cause=exc)
    return [AuditTaskResponse.model_validate(t) for t in tasks]


@router.get("/{audit_id}/metrics", response_model=MetricsResponse)
async def get_metrics_endpoint(
    audit_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> MetricsResponse:
    """Single-run ``MetricSnapshot`` projection (invariant 7 — no provider).

    Reads only the persisted aggregate; 404 until the audit has been analyzed
    or for a cross-workspace/missing audit (invariant 5).
    """
    # Authorize the audit first so a cross-workspace id is a 404, not a leak.
    await _get_or_404(session, ctx.workspace_id, audit_id)
    try:
        return await get_metrics(
            session, workspace_id=ctx.workspace_id, audit_id=audit_id
        )
    except AnalysisNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metrics not available for audit",
        ) from exc


@router.get("/{audit_id}/export.csv")
async def export_csv_endpoint(
    audit_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> Response:
    """Download the per-execution evidence as CSV (renders persisted rows)."""
    try:
        audit, tasks = await load_export_bundle(
            session, workspace_id=ctx.workspace_id, audit_id=audit_id
        )
    except AnalysisNotFoundError as exc:
        raise_not_found("Audit", cause=exc)
    body = audit_to_csv(audit, tasks)
    return Response(
        content=body,
        media_type="text/csv",
        headers={
            "Content-Disposition": (f'attachment; filename="audit-{audit_id}.csv"')
        },
    )


@router.get("/{audit_id}/export.md")
async def export_markdown_endpoint(
    audit_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> PlainTextResponse:
    """Download the benchmark report as Markdown (renders persisted summary)."""
    try:
        audit, tasks = await load_export_bundle(
            session, workspace_id=ctx.workspace_id, audit_id=audit_id
        )
    except AnalysisNotFoundError as exc:
        raise_not_found("Audit", cause=exc)
    body = audit_to_markdown(audit, tasks)
    return PlainTextResponse(
        content=body,
        media_type="text/markdown",
        headers={
            "Content-Disposition": (f'attachment; filename="audit-{audit_id}.md"')
        },
    )


@router.get("/{audit_id}/events", response_model=list[AuditEventResponse])
async def list_events_endpoint(
    audit_id: uuid.UUID,
    ctx: _WorkspaceDep,
    session: _SessionDep,
    stream: Annotated[bool, Query()] = False,
) -> list[AuditEventResponse] | StreamingResponse:
    """Return the audit's lifecycle events.

    With ``?stream=true`` returns a ``text/event-stream`` (SSE) that replays the
    existing events and then tails new ones until the audit reaches a terminal
    status. Otherwise returns the full event list as JSON.
    """
    # Authorize first (404 for a cross-workspace / missing audit — invariant 5).
    await _get_or_404(session, ctx.workspace_id, audit_id)
    if not stream:
        events = await _load_events(session, audit_id, after=None)
        return [AuditEventResponse.model_validate(e) for e in events]
    return StreamingResponse(
        _event_stream(audit_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _get_or_404(
    session: AsyncSession, workspace_id: uuid.UUID, audit_id: uuid.UUID
) -> Audit:
    try:
        return await get_audit(session, workspace_id=workspace_id, audit_id=audit_id)
    except AuditNotFoundError as exc:
        raise_not_found("Audit", cause=exc)


async def _load_events(
    session: AsyncSession, audit_id: uuid.UUID, *, after: uuid.UUID | None
) -> list[AuditEvent]:
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.audit_id == audit_id)
        .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
    )
    events = list((await session.scalars(stmt)).all())
    if after is None:
        return events
    seen = False
    tail: list[AuditEvent] = []
    for event in events:
        if seen:
            tail.append(event)
        elif event.id == after:
            seen = True
    return tail if seen else events


def _sse_payload(event: AuditEvent) -> str:
    body = {
        "id": str(event.id),
        "audit_id": str(event.audit_id),
        "event_type": event.event_type,
        "message": event.message,
        "payload": event.payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
    return f"event: {event.event_type}\nid: {event.id}\ndata: {json.dumps(body)}\n\n"


async def _event_stream(
    audit_id: uuid.UUID,
):  # pragma: no cover - streaming loop
    """Tail an audit's events until it terminalizes.

    Opens its own short-lived sessions (the request session is closed once the
    handler returns the ``StreamingResponse``). Stops shortly after the audit
    reaches a terminal status so the connection does not hang forever.
    """
    last_id: uuid.UUID | None = None
    terminal_polls = 0
    while True:
        async with SessionLocal() as session:
            new_events = await _load_events(session, audit_id, after=last_id)
            for event in new_events:
                last_id = event.id
                yield _sse_payload(event)
            audit = await session.get(Audit, audit_id)
        if audit is None:
            break
        if audit.status in AUDIT_TERMINAL_STATUSES:
            terminal_polls += 1
            if terminal_polls >= _SSE_TERMINAL_GRACE_POLLS:
                break
        await asyncio.sleep(_SSE_POLL_SECONDS)
