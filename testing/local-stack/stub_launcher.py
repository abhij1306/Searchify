#!/usr/bin/env python3
"""Stub-patched process launcher for Searchify integration testing.

Monkeypatches the integrations config Finals (OAuth authorize/token/revoke
URLs, the SSRF approved-host allow-list, and the GSC/GA4/Bing API base URLs)
to point at the local stub provider BEFORE any app module imports them, then
runs the requested process in-process:

  cd <repo-root>/backend
  uv run python testing/local-stack/stub_launcher.py api
  uv run python testing/local-stack/stub_launcher.py integration-worker
  uv run python testing/local-stack/stub_launcher.py analytics-worker
  uv run python testing/local-stack/stub_launcher.py dispatcher

TEST-ONLY: nothing is written to the repo; the patch lives for the lifetime
of the launched process only. Env: STUB_PROVIDER_ORIGIN (default
http://127.0.0.1:9876), API_PORT (default 8000).
"""
from __future__ import annotations

import os
import sys

STUB = os.environ.get("STUB_PROVIDER_ORIGIN", "http://127.0.0.1:9876")
STUB_HOST = "127.0.0.1"


def patch_integrations_config() -> None:
    from app.core.config import integrations as icfg

    for transport in (
        icfg.INTEGRATION_TRANSPORT_GOOGLE,
        icfg.INTEGRATION_TRANSPORT_MICROSOFT,
    ):
        icfg.INTEGRATION_OAUTH_AUTHORIZE_URLS[transport] = f"{STUB}/authorize"
        icfg.INTEGRATION_OAUTH_TOKEN_URLS[transport] = f"{STUB}/token"
    icfg.INTEGRATION_OAUTH_REVOKE_URLS[icfg.INTEGRATION_TRANSPORT_GOOGLE] = (
        f"{STUB}/revoke"
    )
    icfg.INTEGRATION_APPROVED_ENDPOINT_HOSTS = frozenset(
        set(icfg.INTEGRATION_APPROVED_ENDPOINT_HOSTS) | {STUB_HOST, "localhost"}
    )
    icfg.GSC_API_BASE_URL = STUB
    icfg.GA4_API_BASE_URL = STUB
    icfg.BING_API_BASE_URL = STUB


class _PublicHostMiddleware:
    """ASGI shim: make ``request.base_url`` reflect the PUBLIC origin.

    uvicorn 0.51's ProxyHeadersMiddleware ignores ``x-forwarded-host``, so
    through the Next same-origin proxy the backend would build the OAuth
    ``redirect_uri`` as http://localhost:8000/... and the provider (stub)
    would send the browser to a dead :8000 page. Production front proxies
    preserve the public Host; this shim emulates that for local testing by
    preferring ``x-forwarded-host`` and otherwise forcing FORWARD_HOST
    (default localhost:3000). TEST-ONLY — lives in this launcher process.
    """

    def __init__(self, app, forced_host: str) -> None:
        self.app = app
        self.forced_host = forced_host.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = list(scope.get("headers") or [])
            by_name = {}
            for key, value in headers:
                by_name.setdefault(key.lower(), value)
            public = by_name.get(b"x-forwarded-host", self.forced_host)
            scope = dict(
                scope,
                headers=[
                    (key, public if key.lower() == b"host" else value)
                    for key, value in headers
                ],
            )
        await self.app(scope, receive, send)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    mode = sys.argv[1]
    patch_integrations_config()
    if mode == "api":
        import uvicorn

        from app.main import app as fastapi_app

        uvicorn.run(
            _PublicHostMiddleware(
                fastapi_app, os.environ.get("FORWARD_HOST", "localhost:3000")
            ),
            host="0.0.0.0",
            port=int(os.environ.get("API_PORT", "8000")),
            proxy_headers=True,
        )
    elif mode == "integration-worker":
        from app.workers.integration_worker import main as worker_main

        worker_main()
    elif mode == "analytics-worker":
        from app.workers.analytics_worker import main as worker_main

        worker_main()
    elif mode == "dispatcher":
        from app.workers.integration_dispatcher import main as dispatcher_main

        dispatcher_main()
    else:
        raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main()
