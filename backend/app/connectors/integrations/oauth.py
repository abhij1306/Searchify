"""Integration OAuth transport client (spec: docs/roadmap/integrations.md §2).

Performs the authorization-code exchange, refresh, remote revoke, and the
cheap authenticated grant probe behind ``POST /integrations/{id}/test`` — per
OAuth transport (``google_oauth`` covering the shared GSC+GA4 grant;
``microsoft_oauth`` covering Bing) over httpx with an injected transport
(test seam, mirroring ``connectors/discovery_models/factory.py``).

Invariant 6: access/refresh tokens and the env-injected client secret pass
through this module but are NEVER logged — error surfaces carry only HTTP
status codes and config-owned error tokens. Authorization headers are set
per-request and never logged. Endpoints come from
``app.core.config.integrations`` and every URL is checked against the
approved-host allow-list before a request is issued (SSRF policy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.core.config.integrations import (
    ERROR_GRANT_AUTH_FAILED,
    ERROR_PROVIDER_API,
    ERROR_RATE_LIMITED,
    ERROR_UNAPPROVED_ENDPOINT,
    GSC_API_BASE_URL,
    INTEGRATION_APPROVED_ENDPOINT_HOSTS,
    INTEGRATION_OAUTH_REVOKE_URLS,
    INTEGRATION_OAUTH_TOKEN_URLS,
    INTEGRATION_TRANSPORT_GOOGLE,
    INTEGRATION_TRANSPORT_MICROSOFT,
    INTEGRATION_TRANSPORTS,
    integration_settings,
)

# Cap on provider-supplied error text surfaced in exceptions (defensive:
# keeps messages short even if a provider returns a huge error body).
_ERROR_DETAIL_MAX_LEN = 240

# Cheap, read-only, scope-minimal probe path validating a Google grant's
# access token (the one shared Google grant carries ``webmasters.readonly``
# for both the GSC and the GA4 connection, so the site list validates the
# grant behind either connection). The host is config-owned
# (``GSC_API_BASE_URL``) and allow-listed. The Bing data-API probe host/path
# literal is pinned from Microsoft docs at I12 (plan R3); until then a
# Microsoft grant is probed via a non-persisting refresh round-trip (see the
# domain service).
_GSC_SITES_PROBE_PATH = "/webmasters/v3/sites"


class IntegrationOAuthError(RuntimeError):
    """An OAuth transport call failed; carries a config-owned error token."""

    def __init__(
        self, message: str, *, error_code: str, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


@dataclass(frozen=True)
class OAuthTokenBundle:
    """Tokens + metadata from an exchange/refresh. NEVER logged (invariant 6)."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int | None = None
    granted_scopes: tuple[str, ...] = ()


def oauth_client_credentials(transport: str) -> tuple[str, str]:
    """Resolve the transport's env-injected client id/secret (never logged)."""
    if transport == INTEGRATION_TRANSPORT_GOOGLE:
        return (
            settings.integration_google_client_id,
            settings.integration_google_client_secret,
        )
    if transport == INTEGRATION_TRANSPORT_MICROSOFT:
        return (
            settings.integration_microsoft_client_id,
            settings.integration_microsoft_client_secret,
        )
    raise IntegrationOAuthError(
        f"unknown OAuth transport: {transport!r}", error_code=ERROR_PROVIDER_API
    )


def oauth_client_configured(transport: str) -> bool:
    """True when the transport's client id + secret are env-configured.

    Never logs the underlying values (invariant 6).
    """
    client_id, client_secret = oauth_client_credentials(transport)
    return bool(client_id and client_secret)


def _assert_approved_url(url: str) -> None:
    """SSRF guard: integration clients only call allow-listed hosts (config)."""
    host = (urlsplit(url).hostname or "").lower()
    if host not in INTEGRATION_APPROVED_ENDPOINT_HOSTS:
        raise IntegrationOAuthError(
            f"OAuth endpoint host is not approved: {host or '<none>'}",
            error_code=ERROR_UNAPPROVED_ENDPOINT,
        )


def _safe_error_detail(payload: object) -> str:
    """Extract ``error`` + ``error_description`` from an OAuth error body.

    Only these two known fields are read (never the full body) and both are
    length-capped; non-dict payloads degrade to an empty string.
    """
    if not isinstance(payload, dict):
        return ""
    parts = []
    error = str(payload.get("error") or "").strip()
    if error:
        parts.append(error[:_ERROR_DETAIL_MAX_LEN])
    description = str(payload.get("error_description") or "").strip()
    if description:
        parts.append(description[:_ERROR_DETAIL_MAX_LEN])
    return ": ".join(parts)


def _classify_status(status_code: int) -> tuple[str, bool]:
    """Map an HTTP status to a config-owned (error_code, retryable) pair."""
    if status_code == 429:
        return ERROR_RATE_LIMITED, True
    if status_code in (401, 403):
        return ERROR_GRANT_AUTH_FAILED, False
    return ERROR_PROVIDER_API, status_code in (500, 502, 503, 504)


def _split_scopes(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    return tuple(scope for scope in value.split(" ") if scope)


def _coerce_expires_in(value: object) -> int | None:
    try:
        seconds = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


class IntegrationOAuthClient:
    """OAuth client for one integration transport.

    ``transport`` is a test seam (``httpx.MockTransport``); production passes
    nothing and the client uses the real network.
    """

    def __init__(
        self,
        transport_kind: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if transport_kind not in INTEGRATION_TRANSPORTS:
            raise IntegrationOAuthError(
                f"unknown OAuth transport: {transport_kind!r}",
                error_code=ERROR_PROVIDER_API,
            )
        self._transport_kind = transport_kind
        self._transport = transport

    def _http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=integration_settings.sync_request_timeout_seconds,
        )

    async def _post_form(self, url: str, data: dict[str, str], *, action: str) -> dict:
        """POST a form body and return the JSON object, raising on failure.

        The request carries credentials (client secret, codes, tokens) in
        ``data`` — none of it is ever logged; raised errors carry only the
        HTTP status and the provider's capped error code/description.
        """
        _assert_approved_url(url)
        try:
            async with self._http_client() as client:
                response = await client.post(url, data=data)
        except httpx.HTTPError as exc:
            raise IntegrationOAuthError(
                f"OAuth {action} request failed: {type(exc).__name__}",
                error_code=ERROR_PROVIDER_API,
                retryable=True,
            ) from exc
        if response.status_code != 200:
            error_code, retryable = _classify_status(response.status_code)
            try:
                detail = _safe_error_detail(response.json())
            except ValueError:
                detail = ""
            suffix = f" ({detail})" if detail else ""
            raise IntegrationOAuthError(
                f"OAuth {action} returned HTTP {response.status_code}{suffix}",
                error_code=error_code,
                retryable=retryable,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise IntegrationOAuthError(
                f"OAuth {action} returned a non-JSON body",
                error_code=ERROR_PROVIDER_API,
            ) from exc
        if not isinstance(payload, dict):
            raise IntegrationOAuthError(
                f"OAuth {action} returned an unexpected body",
                error_code=ERROR_PROVIDER_API,
            )
        return payload

    async def exchange_code(self, *, code: str, redirect_uri: str) -> OAuthTokenBundle:
        """Exchange an authorization code for tokens at the token endpoint."""
        client_id, client_secret = oauth_client_credentials(self._transport_kind)
        payload = await self._post_form(
            INTEGRATION_OAUTH_TOKEN_URLS[self._transport_kind],
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            action="code exchange",
        )
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise IntegrationOAuthError(
                "OAuth code exchange returned no access_token",
                error_code=ERROR_PROVIDER_API,
            )
        return OAuthTokenBundle(
            access_token=access_token,
            refresh_token=str(payload.get("refresh_token") or ""),
            expires_in=_coerce_expires_in(payload.get("expires_in")),
            granted_scopes=_split_scopes(payload.get("scope")),
        )

    async def refresh(self, *, refresh_token: str) -> OAuthTokenBundle:
        """Exchange a refresh token for a fresh access token.

        A provider may omit ``refresh_token`` from the response (Google keeps
        the original grant); the passed token is carried over in that case.
        """
        client_id, client_secret = oauth_client_credentials(self._transport_kind)
        payload = await self._post_form(
            INTEGRATION_OAUTH_TOKEN_URLS[self._transport_kind],
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            action="token refresh",
        )
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise IntegrationOAuthError(
                "OAuth token refresh returned no access_token",
                error_code=ERROR_PROVIDER_API,
            )
        return OAuthTokenBundle(
            access_token=access_token,
            refresh_token=str(payload.get("refresh_token") or "") or refresh_token,
            expires_in=_coerce_expires_in(payload.get("expires_in")),
            granted_scopes=_split_scopes(payload.get("scope")),
        )

    async def revoke(self, *, token: str) -> None:
        """Remotely revoke a grant token (RFC 7009; Google-only).

        The Microsoft identity platform exposes no grant-revocation endpoint —
        its config URL is intentionally empty and the caller must take the
        documented local-only path instead of calling this.
        """
        url = INTEGRATION_OAUTH_REVOKE_URLS[self._transport_kind]
        if not url:
            raise IntegrationOAuthError(
                f"transport {self._transport_kind} has no remote revoke endpoint",
                error_code=ERROR_PROVIDER_API,
            )
        _assert_approved_url(url)
        try:
            async with self._http_client() as client:
                response = await client.post(url, data={"token": token})
        except httpx.HTTPError as exc:
            raise IntegrationOAuthError(
                f"OAuth revoke request failed: {type(exc).__name__}",
                error_code=ERROR_PROVIDER_API,
                retryable=True,
            ) from exc
        if response.status_code != 200:
            error_code, retryable = _classify_status(response.status_code)
            raise IntegrationOAuthError(
                f"OAuth revoke returned HTTP {response.status_code}",
                error_code=error_code,
                retryable=retryable,
            )

    async def probe_access_token(self, *, access_token: str) -> None:
        """Cheap authenticated probe validating a Google grant's access token.

        GETs the GSC site list with the Bearer token (never logged). Raises
        ``IntegrationOAuthError`` on any failure.
        """
        url = f"{GSC_API_BASE_URL}{_GSC_SITES_PROBE_PATH}"
        _assert_approved_url(url)
        try:
            async with self._http_client() as client:
                response = await client.get(
                    url, headers={"Authorization": f"Bearer {access_token}"}
                )
        except httpx.HTTPError as exc:
            raise IntegrationOAuthError(
                f"grant probe request failed: {type(exc).__name__}",
                error_code=ERROR_PROVIDER_API,
                retryable=True,
            ) from exc
        if response.status_code != 200:
            error_code, retryable = _classify_status(response.status_code)
            raise IntegrationOAuthError(
                f"grant probe returned HTTP {response.status_code}",
                error_code=error_code,
                retryable=retryable,
            )


def build_oauth_client(
    transport_kind: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> IntegrationOAuthClient:
    """Build an OAuth client for a transport (``transport`` = test seam).

    The domain service resolves clients through this factory so component
    tests can inject a ``httpx.MockTransport`` fake OAuth server.
    """
    return IntegrationOAuthClient(transport_kind, transport=transport)
