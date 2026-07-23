"""Integrations API DTOs — these NEVER carry tokens (invariant 6).

Tokens live Fernet-encrypted on ``IntegrationOAuthGrant`` and are decrypted
only inside the service for an exchange/probe/revoke call. The wire shapes
match the frontend zod contracts exactly (contract C6):
``integrationConnectionSchema`` and ``integrationTestResultSchema`` are
``.strict()`` — any leaked token key fails their validation loud.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


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
