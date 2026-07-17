"""BYOK provider-connection service (workspace-scoped, invariant 5 + 6).

Every read/write filters by ``workspace_id``. The BYOK secret is Fernet-
encrypted on write and decrypted only inside ``run_connection_test`` to build a
short-lived adapter — it is never returned in a DTO, never logged, and never
persisted anywhere but the encrypted column (invariant 6).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.connectors.answer_engines.contracts import AnswerEngineRequest
from app.connectors.answer_engines.errors import ProviderError
from app.connectors.answer_engines.factory import build_adapter
from app.core.config.provider_catalog import (
    ERROR_PARSE,
    PROBE_PROMPT,
    TEST_STATUS_FAILED,
    TEST_STATUS_OK,
    default_model,
    default_probe_engine,
    is_active_transport,
    is_route_approved,
    provider_catalog_settings,
)
from app.core.security import decrypt_secret, encrypt_secret
from app.domain.providers.schemas import (
    ProviderConnectionCreate,
    ProviderConnectionResponse,
    ProviderConnectionTestResponse,
    ProviderConnectionUpdate,
    ProviderRouteResponse,
)
from app.models.provider import (
    ProviderConnection,
    ProviderConnectionTest,
    ProviderRoute,
)


class ProviderConnectionNotFoundError(LookupError):
    """Raised when a connection is missing or not in the caller's workspace."""


class InvalidRouteError(ValueError):
    """Raised when a requested (engine, transport) route is not approved."""


class LegacyConnectionReadOnlyError(RuntimeError):
    """Raised when a mutation/test targets a retired (legacy) transport.

    Legacy OpenRouter connections survive read-only for historical provenance
    (invariant 10). Updating or testing one is refused BEFORE any key
    decryption, mutation, or network call. The message is credential-free.
    """


def _connection_query():
    return select(ProviderConnection).options(
        selectinload(ProviderConnection.routes)
    )


def connection_to_response(
    connection: ProviderConnection,
) -> ProviderConnectionResponse:
    """Project a connection to its DTO. NEVER includes the key (invariant 6)."""
    return ProviderConnectionResponse(
        id=connection.id,
        workspace_id=connection.workspace_id,
        label=connection.label,
        transport_provider=connection.transport_provider,
        base_url=connection.base_url,
        active=connection.active,
        api_key_set=bool(connection.api_key_encrypted),
        last_tested_at=connection.last_tested_at,
        last_test_status=connection.last_test_status,
        routes=[
            ProviderRouteResponse.model_validate(route)
            for route in connection.routes
        ],
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _build_routes(
    *,
    workspace_id: uuid.UUID,
    transport_provider: str,
    items: list[Any] | None,
) -> list[ProviderRoute]:
    routes: list[ProviderRoute] = []
    for item in items or []:
        logical_engine = item.logical_engine
        if not is_route_approved(logical_engine, transport_provider):
            raise InvalidRouteError(
                f"Route not approved at MVP: {logical_engine} via "
                f"{transport_provider}"
            )
        model = (item.transport_model or "").strip() or default_model(
            logical_engine, transport_provider
        )
        routes.append(
            ProviderRoute(
                workspace_id=workspace_id,
                logical_engine=logical_engine,
                transport_provider=transport_provider,
                transport_model=model,
                is_default=item.is_default,
            )
        )
    return routes


async def list_connections(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> list[ProviderConnection]:
    result = await session.execute(
        _connection_query()
        .where(ProviderConnection.workspace_id == workspace_id)
        .order_by(ProviderConnection.created_at.desc())
    )
    return list(result.scalars().all())


async def get_connection(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> ProviderConnection:
    result = await session.execute(
        _connection_query().where(
            ProviderConnection.id == connection_id,
            ProviderConnection.workspace_id == workspace_id,
        )
    )
    connection = result.scalar_one_or_none()
    if connection is None:
        raise ProviderConnectionNotFoundError(str(connection_id))
    return connection


async def create_connection(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    payload: ProviderConnectionCreate,
) -> ProviderConnection:
    routes = _build_routes(
        workspace_id=workspace_id,
        transport_provider=payload.transport_provider,
        items=payload.routes,
    )
    connection = ProviderConnection(
        workspace_id=workspace_id,
        label=payload.label,
        transport_provider=payload.transport_provider,
        base_url=payload.base_url,
        api_key_encrypted=encrypt_secret(payload.api_key.strip()),
        active=payload.active,
        routes=routes,
    )
    session.add(connection)
    await session.commit()
    return await get_connection(
        session, workspace_id=workspace_id, connection_id=connection.id
    )


async def update_connection(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
    payload: ProviderConnectionUpdate,
) -> ProviderConnection:
    connection = await get_connection(
        session, workspace_id=workspace_id, connection_id=connection_id
    )
    # Reject legacy (retired-transport) connections before any mutation.
    if not is_active_transport(connection.transport_provider):
        raise LegacyConnectionReadOnlyError(
            "This connection uses a retired transport and is historical and "
            "read-only; create a new direct connection instead."
        )
    if payload.label is not None:
        connection.label = payload.label
    if payload.base_url is not None:
        connection.base_url = payload.base_url
    if payload.active is not None:
        connection.active = payload.active
    # Key rotation: only re-encrypt when a non-empty key is supplied.
    if payload.api_key is not None and payload.api_key.strip():
        connection.api_key_encrypted = encrypt_secret(payload.api_key.strip())
    if payload.routes is not None:
        connection.routes = _build_routes(
            workspace_id=workspace_id,
            transport_provider=connection.transport_provider,
            items=payload.routes,
        )
    await session.commit()
    return await get_connection(
        session, workspace_id=workspace_id, connection_id=connection_id
    )


async def delete_connection(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> None:
    connection = await get_connection(
        session, workspace_id=workspace_id, connection_id=connection_id
    )
    await session.delete(connection)
    await session.commit()


async def run_connection_test(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> ProviderConnectionTestResponse:
    """Perform a live-ish connectivity probe through the adapter.

    Decrypts the key here (only here), builds a short-lived adapter, and fires a
    neutral, brand-free probe prompt. Records an append-only
    ``ProviderConnectionTest`` row and denormalizes the outcome onto the
    connection. The key is never logged or persisted (invariant 6).
    """
    connection = await get_connection(
        session, workspace_id=workspace_id, connection_id=connection_id
    )
    transport = connection.transport_provider
    # Refuse to test a retired-transport connection before decrypting the key
    # or issuing any network call (invariant 6 + 10).
    if not is_active_transport(transport):
        raise LegacyConnectionReadOnlyError(
            "This connection uses a retired transport and is historical and "
            "read-only; create a new direct connection instead."
        )
    # Prefer a configured route's engine/model; else fall back to a catalog
    # default engine for the transport.
    logical_engine = default_probe_engine(transport)
    model = default_model(logical_engine, transport)
    for route in connection.routes:
        logical_engine = route.logical_engine
        model = route.transport_model or model
        break

    status = TEST_STATUS_OK
    error_code = ""
    detail = "Connection succeeded"
    latency_ms: int | None = None
    resolved_model = model

    started = time.monotonic()
    try:
        api_key = decrypt_secret(connection.api_key_encrypted)
        adapter = build_adapter(
            logical_engine=logical_engine,
            transport_provider=transport,
            api_key=api_key,
            base_url=connection.base_url,
        )
        response = await adapter.execute(
            AnswerEngineRequest(
                prompt=PROBE_PROMPT,
                system_instruction="",
                model=model,
                timeout_seconds=provider_catalog_settings.test_timeout_seconds,
            )
        )
        latency_ms = response.latency_ms
        resolved_model = response.transport_model or model
    except ProviderError as exc:
        status = TEST_STATUS_FAILED
        error_code = exc.error_code
        detail = str(exc)
        latency_ms = int((time.monotonic() - started) * 1000)
    except Exception as exc:  # noqa: BLE001 - any transport fault is a failure
        status = TEST_STATUS_FAILED
        error_code = ERROR_PARSE
        detail = f"Unexpected error: {type(exc).__name__}"
        latency_ms = int((time.monotonic() - started) * 1000)

    tested_at = datetime.now(UTC)
    test_row = ProviderConnectionTest(
        workspace_id=workspace_id,
        connection_id=connection.id,
        status=status,
        error_code=error_code,
        detail=detail[:1024],
        latency_ms=latency_ms,
        logical_engine=logical_engine,
        transport_provider=transport,
        transport_model=resolved_model,
    )
    session.add(test_row)
    connection.last_tested_at = tested_at
    connection.last_test_status = status
    await session.commit()

    return ProviderConnectionTestResponse(
        connection_id=connection.id,
        status=status,
        error_code=error_code,
        detail=detail,
        latency_ms=latency_ms,
        logical_engine=logical_engine,
        transport_provider=transport,
        transport_model=resolved_model,
        tested_at=tested_at,
    )
