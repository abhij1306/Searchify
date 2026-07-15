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
    # /health is registered, and all six stub routers are included so B2-B6
    # fill them in place (they carry no endpoints yet).
    from app.main import _ROUTERS

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/health" in paths
    assert len(_ROUTERS) == 6
