# Integrations router: OAuth connect (real 302 flow) + connection management
# (docs/roadmap/integrations.md section 5; invariant 5 + 6 + 12).
#
# Flat surface under /api/v1/integrations; the active workspace comes from
# ``require_active_workspace`` EXCEPT at the OAuth callback, where the
# workspace comes only from the verified, consumed state and the user from
# ``get_current_user`` (spec section 2). The connect endpoints are full-page
# 302 navigations through the same-origin proxy (never fetch/XHR). No
# endpoint ever returns a token — the DTOs carry no token fields (invariant
# 6).
from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    WorkspaceContext,
    get_current_user,
    get_db,
    require_active_workspace,
)
from app.core.config.integrations import (
    ERROR_OAUTH_EXCHANGE_FAILED,
    ERROR_OAUTH_NOT_CONFIGURED,
    ERROR_OAUTH_STATE_INVALID,
    INTEGRATION_OAUTH_CALLBACK_PATH,
    INTEGRATION_OAUTH_LANDING_PATH,
    INTEGRATION_PROVIDERS,
)
from app.core.http_errors import raise_not_found
from app.domain.integrations.schemas import (
    IntegrationConnectionResponse,
    IntegrationTestResponse,
)
from app.domain.integrations.service import (
    IntegrationConnectionNotFoundError,
    IntegrationExchangeError,
    IntegrationNotConfiguredError,
    IntegrationStateError,
    complete_connect,
    delete_connection,
    list_connections,
    run_connection_test,
    start_connect,
)
from app.models.user import User

router = APIRouter(prefix="/integrations", tags=["integrations"])

_WorkspaceDep = Annotated[WorkspaceContext, Depends(require_active_workspace)]
_UserDep = Annotated[User, Depends(get_current_user)]
_SessionDep = Annotated[AsyncSession, Depends(get_db)]

_RES_PROVIDER = "Integration provider"
_RES_CONNECTION = "Integration connection"


def _require_known_provider(provider: str) -> None:
    """404 when ``provider`` is not a cataloged integration provider."""
    if provider not in INTEGRATION_PROVIDERS:
        raise_not_found(_RES_PROVIDER)


def _redirect_uri(request: Request, provider: str) -> str:
    """Absolute callback URL for the provider-registered redirect target."""
    base = str(request.base_url).rstrip("/")
    return f"{base}{INTEGRATION_OAUTH_CALLBACK_PATH.format(provider=provider)}"


def _landing_redirect(params: dict[str, str]) -> RedirectResponse:
    """302 back to Settings → Integrations with the result query (contract C2)."""
    url = f"{INTEGRATION_OAUTH_LANDING_PATH}&{urlencode(params)}"
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


@router.get("/oauth/{provider}/start", status_code=status.HTTP_302_FOUND)
async def integration_oauth_start(
    provider: str,
    request: Request,
    ctx: _WorkspaceDep,
    session: _SessionDep,
) -> RedirectResponse:
    """Begin the OAuth connect flow: 302 to the provider consent screen."""
    _require_known_provider(provider)
    try:
        authorize_url = await start_connect(
            session,
            workspace_id=ctx.workspace_id,
            user_id=ctx.user.id,
            provider=provider,
            redirect_uri=_redirect_uri(request, provider),
        )
    except IntegrationNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ERROR_OAUTH_NOT_CONFIGURED,
        ) from exc
    return RedirectResponse(authorize_url, status_code=status.HTTP_302_FOUND)


@router.get("/oauth/{provider}/callback", status_code=status.HTTP_302_FOUND)
async def integration_oauth_callback(
    provider: str,
    request: Request,
    session: _SessionDep,
    user: _UserDep,
    code: Annotated[str, Query()] = "",
    state: Annotated[str, Query()] = "",
    error: Annotated[str, Query()] = "",
) -> RedirectResponse:
    """Handle the provider redirect: verify + consume state, exchange, persist.

    Always 302s back to Settings → Integrations (contract C2) — success and
    failure alike — because the browser is mid full-page navigation.
    """
    _require_known_provider(provider)
    if error:
        # The provider reported a consent/authorization failure.
        return _landing_redirect({"error": ERROR_OAUTH_EXCHANGE_FAILED})
    if not code or not state:
        return _landing_redirect({"error": ERROR_OAUTH_STATE_INVALID})
    try:
        await complete_connect(
            session,
            provider=provider,
            code=code,
            state=state,
            user=user,
            redirect_uri=_redirect_uri(request, provider),
        )
    except IntegrationStateError:
        return _landing_redirect({"error": ERROR_OAUTH_STATE_INVALID})
    except IntegrationNotConfiguredError:
        return _landing_redirect({"error": ERROR_OAUTH_NOT_CONFIGURED})
    except IntegrationExchangeError:
        return _landing_redirect({"error": ERROR_OAUTH_EXCHANGE_FAILED})
    return _landing_redirect({"connected": provider})


@router.get("", response_model=list[IntegrationConnectionResponse])
async def list_integrations_endpoint(
    ctx: _WorkspaceDep, session: _SessionDep
) -> list[IntegrationConnectionResponse]:
    """List this workspace's connections joined to grant status + scopes."""
    return await list_connections(session, workspace_id=ctx.workspace_id)


@router.post(
    "/{connection_id}/test",
    response_model=IntegrationTestResponse,
)
async def test_integration_endpoint(
    connection_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> IntegrationTestResponse:
    """Cheap authenticated probe of the connection's grant (never the token)."""
    try:
        return await run_connection_test(
            session, workspace_id=ctx.workspace_id, connection_id=connection_id
        )
    except IntegrationConnectionNotFoundError as exc:
        raise_not_found(_RES_CONNECTION, cause=exc)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration_endpoint(
    connection_id: uuid.UUID, ctx: _WorkspaceDep, session: _SessionDep
) -> None:
    """Disconnect a connection (last one on a grant also revokes the grant)."""
    try:
        await delete_connection(
            session, workspace_id=ctx.workspace_id, connection_id=connection_id
        )
    except IntegrationConnectionNotFoundError as exc:
        raise_not_found(_RES_CONNECTION, cause=exc)
