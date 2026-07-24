"""Integrations API DTOs — these NEVER carry tokens (invariant 6).

Tokens live Fernet-encrypted on ``IntegrationOAuthGrant`` and are decrypted
only inside the service for an exchange/probe/revoke call. The wire shapes
match the frontend zod contracts exactly (contract C6):
``integrationConnectionSchema``, ``integrationTestResultSchema``,
``integrationSyncRunSchema``, and ``integrationSyncEnqueueSchema`` are
``.strict()`` — any leaked token key fails their validation loud.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class IntegrationConnectionResponse(BaseModel):
    """``GET /integrations`` row: a connection joined to its grant.

    Carries the grant's ``status`` + ``granted_scopes``; the grant's
    encrypted token columns are never present by construction.
    """

    id: uuid.UUID
    workspace_id: uuid.UUID
    grant_id: uuid.UUID
    provider: str
    label: str
    account_ref: str
    grant_status: str
    granted_scopes: list[str]
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IntegrationTestResponse(BaseModel):
    """``POST /integrations/{id}/test`` probe result (never the token)."""

    connection_id: uuid.UUID
    status: str
    error_code: str = ""
    detail: str = ""
    tested_at: datetime


class SyncWindowRequest(BaseModel):
    """Optional explicit window body for ``POST /integrations/{id}/sync``.

    Both bounds absent → the config default trailing window; both present →
    validated + clamped by the sync service; exactly one present → 422.
    """

    window_start: date | None = None
    window_end: date | None = None


class IntegrationSyncEnqueueResponse(BaseModel):
    """202 enqueue identity (contract C3) — the frontend polls the detail.

    Matches ``integrationSyncEnqueueSchema`` exactly (strict).
    """

    sync_run_id: uuid.UUID
    connection_id: uuid.UUID
    status: str


class IntegrationSyncRunResponse(BaseModel):
    """Sync-run history/detail projection (status, window, row counts).

    ``row_count`` is the summed ``row_count`` of the run's immutable import
    artifacts (0 before the worker lands any). ``error_code``/``error_detail``
    are ``""`` when there is no error. Matches ``integrationSyncRunSchema``
    exactly (strict) — a queue-row projection, never any token (invariant 7).
    """

    id: uuid.UUID
    connection_id: uuid.UUID
    sync_kind: str
    status: str
    window_start: date
    window_end: date
    row_count: int
    resync_seq: int
    error_code: str
    error_detail: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class IntegrationPropertyMappingCreate(BaseModel):
    """``POST /integrations/{id}/mappings`` body.

    ``provider`` must equal the referenced connection's provider (422);
    ``property_ref`` must resolve to one of the target project's owned
    domains (422) — GA4 property refs excepted: they are numeric property
    ids validated on shape, never domains. Width caps mirror the DB columns
    so an overlong value fails 422 here instead of a DataError at insert
    time.
    """

    provider: str = Field(min_length=1, max_length=16)
    property_ref: str = Field(min_length=1, max_length=512)
    project_id: uuid.UUID


class IntegrationPropertyMappingResponse(BaseModel):
    """One property→project bridge row (status ``active | disabled``)."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    connection_id: uuid.UUID
    provider: str
    property_ref: str
    project_id: uuid.UUID
    status: str
    created_at: datetime
    updated_at: datetime
