# BYOK provider-connections router (B4): workspace-scoped CRUD + /test +
# provider-catalog (invariant 5 + 6).
#
# The MVP API surface is flat (no workspace_id in the path); the active
# workspace is resolved by ``require_active_workspace`` from the
# ``X-Workspace-Id`` header (or the caller's default workspace). Every query
# filters by that workspace. No endpoint here returns the BYOK secret — the
# response DTOs carry only an ``api_key_set`` presence flag (invariant 6).
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import WorkspaceContext, get_db, require_active_workspace
from app.core.config.provider_catalog import (
    ACTIVE_TRANSPORTS,
    APPROVED_ROUTES,
)
from app.domain.providers.schemas import (
    ProviderCatalogEngine,
    ProviderCatalogResponse,
    ProviderCatalogRoute,
    ProviderConnectionCreate,
    ProviderConnectionResponse,
    ProviderConnectionTestResponse,
    ProviderConnectionUpdate,
)
from app.domain.providers.service import (
    InvalidRouteError,
    LegacyConnectionReadOnlyError,
    ProviderConnectionNotFoundError,
    connection_to_response,
    create_connection,
    delete_connection,
    get_connection,
    list_connections,
    run_connection_test,
    update_connection,
)

router = APIRouter(prefix="/provider-connections", tags=["providers"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]

_NOT_FOUND = "Provider connection not found"


@router.get("", response_model=list[ProviderConnectionResponse])
async def list_connections_endpoint(
    ctx: _WorkspaceDep, session: _SessionDep
) -> list[ProviderConnectionResponse]:
    connections = await list_connections(
        session, workspace_id=ctx.workspace_id
    )
    return [connection_to_response(c) for c in connections]


@router.post(
    "",
    response_model=ProviderConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection_endpoint(
    payload: ProviderConnectionCreate,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> ProviderConnectionResponse:
    try:
        connection = await create_connection(
            session, workspace_id=ctx.workspace_id, payload=payload
        )
    except InvalidRouteError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return connection_to_response(connection)


@router.patch(
    "/{connection_id}", response_model=ProviderConnectionResponse
)
async def update_connection_endpoint(
    connection_id: uuid.UUID,
    payload: ProviderConnectionUpdate,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> ProviderConnectionResponse:
    try:
        connection = await update_connection(
            session,
            workspace_id=ctx.workspace_id,
            connection_id=connection_id,
            payload=payload,
        )
    except ProviderConnectionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND
        ) from exc
    except LegacyConnectionReadOnlyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except InvalidRouteError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return connection_to_response(connection)


@router.delete(
    "/{connection_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_connection_endpoint(
    connection_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> None:
    try:
        await delete_connection(
            session,
            workspace_id=ctx.workspace_id,
            connection_id=connection_id,
        )
    except ProviderConnectionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND
        ) from exc


@router.post(
    "/{connection_id}/test",
    response_model=ProviderConnectionTestResponse,
)
async def test_connection_endpoint(
    connection_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> ProviderConnectionTestResponse:
    """Live-ish connectivity check through the adapter (mirrors llm.py)."""
    try:
        # Ensure the connection exists in this workspace before probing.
        await get_connection(
            session,
            workspace_id=ctx.workspace_id,
            connection_id=connection_id,
        )
    except ProviderConnectionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND
        ) from exc
    try:
        return await run_connection_test(
            session, workspace_id=ctx.workspace_id, connection_id=connection_id
        )
    except LegacyConnectionReadOnlyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


# --- Provider catalog (read-only reference data) --------------------------
catalog_router = APIRouter(prefix="/provider-catalog", tags=["providers"])


@catalog_router.get("", response_model=ProviderCatalogResponse)
async def get_provider_catalog() -> ProviderCatalogResponse:
    """Approved active transports and per-engine routes with default models."""
    engines = [
        ProviderCatalogEngine(
            logical_engine=engine,
            routes=[
                ProviderCatalogRoute(
                    transport_provider=transport, default_model=model
                )
                for transport, model in routes.items()
            ],
        )
        for engine, routes in APPROVED_ROUTES.items()
    ]
    return ProviderCatalogResponse(
        transports=sorted(ACTIVE_TRANSPORTS), engines=engines
    )
