# Auth request/response schemas (all ids string UUID; secrets never returned).
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class Credentials(BaseModel):
    """Register / login payload."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class SessionUser(BaseModel):
    """Public user projection. The password hash is never exposed."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AuthResponse(BaseModel):
    """Response for register/login/me — the authenticated user only.

    The JWT is delivered out-of-band in the HttpOnly session cookie, never
    in the response body.
    """

    user: SessionUser


class OAuthProviderInfo(BaseModel):
    """Public projection of one OAuth provider.

    Flags + label only — client ids, client secrets, and redirect URIs are
    never exposed (invariant 6).
    """

    provider: str
    label: str
    configured: bool


class OAuthProvidersResponse(BaseModel):
    """Listing of the cataloged OAuth providers."""

    providers: list[OAuthProviderInfo]


class OAuthStartResponse(BaseModel):
    """Authorize URL + signed state for starting an OAuth flow."""

    authorize_url: str
    state: str
