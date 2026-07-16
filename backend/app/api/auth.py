# Auth router: register / login / logout / me.
#
# The JWT session is delivered in a **secure HttpOnly cookie** so browser JS
# can never read it. Cookie policy (documented choice):
#   - HttpOnly: yes — the token is inaccessible to JS (XSS hardening).
#   - SameSite=Lax: the browser reaches the backend same-origin via the
#     Next.js rewrites() proxy (gotcha 2), so the cookie is first-party and
#     Lax is sufficient; no cross-site POST flow needs None.
#   - Secure: enabled outside local dev so the cookie only rides HTTPS. Local
#     http dev keeps it off so the cookie is usable without TLS.
#   - Path=/: sent to the whole same-origin API surface.
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.domain.auth.schemas import AuthResponse, Credentials, SessionUser
from app.domain.auth.service import (
    EmailAlreadyRegisteredError,
    authenticate_user,
    register_user,
)
from app.models.user import User

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("app.auth")

# Local, non-secure transports where the cookie must work without TLS.
_INSECURE_ENVS = {"", "development", "dev", "local", "test", "testing"}


def _cookie_secure() -> bool:
    return str(settings.app_env or "").strip().lower() not in _INSECURE_ENVS


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
        max_age=int(settings.jwt_expire_hours * 3600),
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: Credentials,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AuthResponse:
    try:
        await register_user(session, payload.email, payload.password)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    # Log the user straight in: mint a token + workspace exists from register.
    authenticated = await authenticate_user(session, payload.email, payload.password)
    if authenticated is None:  # pragma: no cover - impossible post-register
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    token, user = authenticated
    _set_session_cookie(response, token)
    return AuthResponse(user=SessionUser.model_validate(user))


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: Credentials,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AuthResponse:
    authenticated = await authenticate_user(session, payload.email, payload.password)
    if authenticated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    token, user = authenticated
    _set_session_cookie(response, token)
    logger.info("auth.login_success", extra={"user_id": str(user.id)})
    return AuthResponse(user=SessionUser.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=AuthResponse)
async def me(user: Annotated[User, Depends(get_current_user)]) -> AuthResponse:
    return AuthResponse(user=SessionUser.model_validate(user))
