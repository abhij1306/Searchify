"""Component test: the app imports cleanly and /health returns 200."""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from app.main import app


@pytest.mark.asyncio
async def test_health_returns_200() -> None:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_echoes_request_id_header() -> None:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == "abc123"


def test_health_route_and_router_stubs_registered() -> None:
    # /health is registered, and all mounted routers are included so B2-B6
    # fill them in place. B4 adds the provider-catalog router alongside the six
    # original stubs (7); B6 adds the executions router (8); the Site Health
    # router adds the ninth (9 total).
    from app.main import _ROUTERS

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/health" in paths
    assert len(_ROUTERS) == 9
