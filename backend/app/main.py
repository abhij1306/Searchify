# FastAPI application factory, middleware, lifespan, and router registration.
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.api.analytics import router as analytics_router
from app.api.audits import router as audits_router
from app.api.auth import router as auth_router
from app.api.brand_suggestions import router as brand_suggestions_router
from app.api.content import router as content_router
from app.api.executions import router as executions_router
from app.api.integrations import router as integrations_router
from app.api.oauth import router as oauth_router
from app.api.projects import router as projects_router
from app.api.prompts import router as prompts_router
from app.api.provider_connections import (
    catalog_router as provider_catalog_router,
)
from app.api.provider_connections import router as provider_connections_router
from app.api.site_health import router as site_health_router
from app.api.workspaces import router as workspaces_router
from app.core.config import get_frontend_origins, settings
from app.core.database import dispose_engine
from app.core.telemetry import (
    configure_logging,
    generate_correlation_id,
    instrument_fastapi,
    reset_correlation_id,
    set_correlation_id,
)

logger = logging.getLogger("app")

# All application routes live under /api/v1 (workspace-scoped per invariant 5).
API_V1_PREFIX = "/api/v1"


def _sanitize_correlation_id(value: str) -> str:
    """Reject a client-supplied correlation id that is unsafe to echo back.

    The id is reflected into a response header, so any control character
    (notably CR/LF) could split the response (header injection). Accept only a
    bounded run of unreserved token characters; anything else is treated as
    absent so a fresh server-generated id is used instead.
    """
    candidate = value.strip()
    if 0 < len(candidate) <= 128 and all(c.isalnum() or c in "-_." for c in candidate):
        return candidate
    return ""


# Explicit router stubs registered now so B2–B6 fill them in place. Each router
# owns its own paths; the prefix keeps the whole surface under /api/v1.
_ROUTERS = (
    auth_router,
    oauth_router,
    workspaces_router,
    projects_router,
    brand_suggestions_router,
    prompts_router,
    provider_connections_router,
    provider_catalog_router,
    audits_router,
    executions_router,
    site_health_router,
    content_router,
    integrations_router,
    analytics_router,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    logger.info("searchify backend starting", extra={"app_env": settings.app_env})
    try:
        yield
    finally:
        await dispose_engine()


def create_app() -> FastAPI:
    """Application factory: build and configure the FastAPI app."""
    configure_logging()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_frontend_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next) -> Response:
        header_name = settings.request_id_header
        supplied = request.headers.get(header_name) or ""
        correlation_id = _sanitize_correlation_id(supplied) or generate_correlation_id()
        request.state.correlation_id = correlation_id
        token = set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(token)
        response.headers[header_name] = correlation_id
        return response

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    for router in _ROUTERS:
        app.include_router(router, prefix=API_V1_PREFIX)

    instrument_fastapi(app)
    return app


app = create_app()
